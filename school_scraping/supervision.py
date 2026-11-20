"""Bound one explicitly owned child tree; never target processes by name.

Windows launches a standard-library-only base-Python gate. The gate cannot start
the real command until its PID is assigned to a kill-on-close Job Object. POSIX
children use a new session/process group. Imports perform no process or OS I/O.
"""

from dataclasses import dataclass
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time


class SupervisionError(RuntimeError):
    """Containment or cleanup failed; messages must not expose command/env data."""


@dataclass(frozen=True)
class SupervisionResult:
    returncode: int | None
    timed_out: bool
    cancelled: bool
    duration_seconds: float
    cleanup_verified: bool
    forced_cleanup: bool = False


_GATED_LAUNCHER = """import json,os,signal,subprocess,sys
try:
    permit=json.loads(sys.stdin.readline())
    if permit.get('pid') != os.getpid():
        raise SystemExit(125)
    signal.signal(signal.SIGINT, lambda *_: None)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda *_: None)
    child=subprocess.Popen(permit['command'], stdin=subprocess.DEVNULL)
    raise SystemExit(child.wait())
except SystemExit:
    raise
except BaseException:
    raise SystemExit(126)
"""


def _seconds(value, label, *, optional=False, allow_zero=False):
    if optional and value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero)):
        raise ValueError(f"{label} must be a finite {'nonnegative' if allow_zero else 'positive'} number")
    return float(value)


class _WindowsJob:
    """Small ctypes wrapper around a private, non-inheritable Job Object."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Limits), ("io", IO), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                ("user", "kernel", "period_user", "period_kernel")] + [
                ("page_faults", wintypes.DWORD), ("total", wintypes.DWORD),
                ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

        self._ctypes, self._wintypes, self._accounting = ctypes, wintypes, Accounting
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self._kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise SupervisionError("Cannot create an owned process job")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise SupervisionError("Cannot configure owned process containment")

    def assign(self, process):
        handle = self._wintypes.HANDLE(int(process._handle))
        if not self._kernel.AssignProcessToJobObject(self.handle, handle):
            raise SupervisionError("Cannot contain the gated child; command was not released")

    def active(self):
        value = self._accounting()
        if not self._kernel.QueryInformationJobObject(self.handle, 1, self._ctypes.byref(value),
                                                      self._ctypes.sizeof(value), None):
            raise SupervisionError("Cannot verify owned process containment")
        return value.active

    def terminate(self):
        if not self._kernel.TerminateJobObject(self.handle, 124):
            raise SupervisionError("Cannot terminate the owned process job")

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self._kernel.CloseHandle(handle):
                raise SupervisionError("Cannot close owned process containment")


class ProcessTree:
    """One launch and its descendants, with explicit ownership and bounded stop."""

    def __init__(self, command, *, cwd, env=None, popen_factory=None,
                 windows_job_factory=None, platform=None):
        if not command or isinstance(command, (str, bytes)):
            raise ValueError("Command must be a nonempty argument sequence")
        self.command = [os.fspath(value) for value in command]
        self.cwd, self.env = os.fspath(cwd), env
        self._popen = popen_factory or subprocess.Popen
        self._job_factory = windows_job_factory or _WindowsJob
        self.platform = platform or sys.platform
        self.process = self.job = None
        self.forced_cleanup = False
        self.cleanup_verified = False

    def start(self):
        try:
            if self.platform == "win32":
                self.job = self._job_factory()
                base = getattr(sys, "_base_executable", None)
                if not base:
                    raise SupervisionError("A direct base interpreter is required for the Windows gate")
                self.process = self._popen([base, "-I", "-B", "-S", "-c", _GATED_LAUNCHER],
                    cwd=self.cwd, env=self.env, stdin=subprocess.PIPE, text=True, encoding="utf-8",
                    creationflags=0x08000000 | 0x00000200, close_fds=True)
                self.job.assign(self.process)
                try:
                    self.process.stdin.write(json.dumps({"pid": self.process.pid, "command": self.command}) + "\n")
                    self.process.stdin.flush()
                finally:
                    self.process.stdin.close()
            else:
                self.process = self._popen(self.command, cwd=self.cwd, env=self.env,
                    stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            return self
        except BaseException as error:
            # Before gate release, this can only stop our exact harmless gate PID.
            if self.process is not None:
                try:
                    if self.process.stdin is not None and not self.process.stdin.closed:
                        self.process.stdin.close()
                    if self.job is not None:
                        self.job.close()
                        self.job = None
                    if self.process.poll() is None:
                        self.process.kill()
                    self.process.wait(timeout=3)
                except Exception:
                    pass
            elif self.job is not None:
                self.job.close()
                self.job = None
            if isinstance(error, (KeyboardInterrupt, SystemExit, SupervisionError)):
                raise
            raise SupervisionError("Owned child startup failed") from None

    def poll(self):
        return self.process.poll()

    def _group_exists(self):
        try:
            os.killpg(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def stop(self, grace_seconds=5, kill_seconds=3):
        grace_seconds = _seconds(grace_seconds, "Shutdown grace", allow_zero=True)
        kill_seconds = _seconds(kill_seconds, "Forced cleanup timeout")
        if self.process is None:
            self.cleanup_verified = True
            return
        if self.process.poll() is None:
            try:
                if self.platform == "win32":
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(self.process.pid, signal.SIGINT)
            except (OSError, ValueError):
                pass  # A no-console Windows service still has forced Job cleanup.
            deadline = time.monotonic() + grace_seconds
            while self.process.poll() is None and time.monotonic() < deadline:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        self.close(kill_seconds=kill_seconds)

    def close(self, kill_seconds=3):
        """Reap the direct child and contain stragglers even after exit code zero."""
        kill_seconds = _seconds(kill_seconds, "Forced cleanup timeout")
        if self.cleanup_verified:
            return
        if self.process is None:
            self.cleanup_verified = True
            return
        reaped = False
        try:
            if self.platform == "win32":
                if self.job.active():
                    self.forced_cleanup = True
                    self.job.terminate()
                deadline = time.monotonic() + kill_seconds
                while self.job.active():
                    if time.monotonic() >= deadline:
                        raise SupervisionError("Owned process tree did not finish forced cleanup")
                    time.sleep(0.02)
            elif self._group_exists():
                self.forced_cleanup = True
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait(timeout=kill_seconds)
            reaped = True
        except (OSError, subprocess.TimeoutExpired):
            raise SupervisionError("Owned process cleanup could not be verified") from None
        finally:
            if self.job is not None:
                try:
                    self.job.close()  # Kill-on-close remains the final containment boundary.
                finally:
                    self.job = None
                    if not reaped:
                        # A failed Job audit still closes containment and reaps
                        # its gate; preserve failure rather than claiming cleanup.
                        self.process.wait(timeout=kill_seconds)
        self.cleanup_verified = True


def supervise(command, *, cwd, timeout, stop_event=None, env=None,
              grace_seconds=5, kill_seconds=3, poll_seconds=0.05, tree_factory=ProcessTree):
    """Run once; timeout/cancellation remains failure even if shutdown returns zero.

    The execution budget is followed by a bounded shutdown grace/forced cleanup.
    No restart/retry, database access, logging of command/env, or activation occurs.
    """
    timeout = _seconds(timeout, "Child timeout", optional=True)
    grace_seconds = _seconds(grace_seconds, "Shutdown grace", allow_zero=True)
    kill_seconds = _seconds(kill_seconds, "Forced cleanup timeout")
    poll_seconds = _seconds(poll_seconds, "Poll interval")
    stop_event = stop_event or threading.Event()
    started = time.monotonic()
    if stop_event.is_set():
        return SupervisionResult(None, False, True, 0.0, True)
    tree = tree_factory(command, cwd=cwd, env=env)
    timed_out = cancelled = False
    try:
        tree.start()
        while True:
            cancelled = stop_event.is_set()
            timed_out = timeout is not None and time.monotonic() - started >= timeout
            if cancelled or timed_out:
                tree.stop(grace_seconds=grace_seconds, kill_seconds=kill_seconds)
                break
            if tree.poll() is not None:
                tree.close(kill_seconds=kill_seconds)
                break
            remaining = poll_seconds if timeout is None else min(poll_seconds,
                max(0, timeout - (time.monotonic() - started)))
            stop_event.wait(remaining)
        return SupervisionResult(tree.poll(), timed_out, cancelled,
            round(time.monotonic() - started, 6), tree.cleanup_verified, tree.forced_cleanup)
    finally:
        if tree.process is not None and not tree.cleanup_verified:
            original_error = sys.exc_info()[0] is not None
            try:
                tree.stop(grace_seconds=0, kill_seconds=kill_seconds)
            except Exception:
                if not original_error:
                    raise
