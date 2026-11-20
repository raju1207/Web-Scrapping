"""Harmless offline process containment; no scheduler/source/database execution."""

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from school_scraping.supervision import ProcessTree, SupervisionError, supervise


def running(pid):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            value = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(value))) and value.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        state = Path(f"/proc/{pid}/stat")
        if state.exists() and state.read_text().split(") ", 1)[1].startswith("Z"):
            return False  # Reaped-by-init timing is separate from live descendants.
        return True
    except ProcessLookupError:
        return False


class SupervisionTests(unittest.TestCase):
    def test_invalid_timeout_never_launches(self):
        for timeout in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(timeout=timeout):
                factory = Mock()
                with self.assertRaises(ValueError):
                    supervise([sys.executable, "-c", "pass"], cwd=Path.cwd(),
                              timeout=timeout, tree_factory=factory)
                factory.assert_not_called()

    def test_already_cancelled_never_launches(self):
        stop, factory = threading.Event(), Mock()
        stop.set()
        result = supervise([sys.executable, "-c", "pass"], cwd=Path.cwd(),
                           timeout=1, stop_event=stop, tree_factory=factory)
        self.assertTrue(result.cancelled)
        factory.assert_not_called()

    def test_real_child_preserves_exit_code_and_reaps(self):
        result = supervise([sys.executable, "-B", "-S", "-c", "raise SystemExit(7)"],
                           cwd=Path.cwd(), timeout=5)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.cancelled)
        self.assertTrue(result.cleanup_verified)
        self.assertFalse(result.forced_cleanup)

    def test_windows_gate_is_assigned_before_command_permit(self):
        events = []

        class GateInput(io.StringIO):
            def write(self, value):
                events.append(("permit", json.loads(value)))
                return super().write(value)

        process = Mock(pid=4321, stdin=GateInput(), returncode=0)
        process.poll.return_value = 0
        job = Mock()
        job.assign.side_effect = lambda child: events.append(("assigned", child.pid))
        job.active.return_value = 0
        popen = Mock(return_value=process)
        command = ["actual-venv-python.exe", "actual-source.py", "--bounded"]
        tree = ProcessTree(command, cwd=Path.cwd(), platform="win32", popen_factory=popen,
                           windows_job_factory=lambda: job)
        tree.start()
        tree.close()
        self.assertEqual([event[0] for event in events], ["assigned", "permit"])
        self.assertEqual(events[1][1], {"pid": 4321, "command": command})
        self.assertNotIn(command[0], popen.call_args.args[0])
        self.assertIn("-I", popen.call_args.args[0])
        self.assertIn("-S", popen.call_args.args[0])
        job.close.assert_called_once()

    def test_windows_assignment_failure_never_releases_actual_command(self):
        stdin = Mock(closed=False)
        process = Mock(pid=4321, stdin=stdin, returncode=None)
        process.poll.side_effect = lambda: process.returncode
        process.kill.side_effect = lambda: setattr(process, "returncode", -9)
        job = Mock()
        job.assign.side_effect = SupervisionError("Cannot contain gated child")
        tree = ProcessTree(["source-must-not-start.exe"], cwd=Path.cwd(), platform="win32",
                           popen_factory=Mock(return_value=process), windows_job_factory=lambda: job)
        with self.assertRaises(SupervisionError):
            tree.start()
        stdin.write.assert_not_called()
        process.kill.assert_called_once()
        job.close.assert_called_once()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows gate isolation check')
    def test_gate_ignores_untrusted_cwd_and_pythonpath_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'unexpected-import.txt'
            (Path(directory)/'json.py').write_text(
                'from pathlib import Path\nPath('+repr(str(marker))+').write_text("unexpected")\n')
            env = os.environ.copy()
            env['PYTHONPATH'] = directory
            result = supervise([sys.executable,'-I','-B','-S','-c','raise SystemExit(0)'],
                cwd=directory,env=env,timeout=5)
            self.assertEqual(result.returncode,0)
            self.assertTrue(result.cleanup_verified)
            self.assertFalse(marker.exists())

    def test_windows_audit_failure_still_closes_job_and_reaps_gate(self):
        process = Mock(returncode=0)
        job = Mock()
        job.active.side_effect = SupervisionError("Cannot audit owned job")
        tree = ProcessTree(["unused"], cwd=Path.cwd(), platform="win32")
        tree.process, tree.job = process, job
        with self.assertRaises(SupervisionError):
            tree.close(kill_seconds=0.1)
        job.close.assert_called_once()
        process.wait.assert_called_once_with(timeout=0.1)
        self.assertFalse(tree.cleanup_verified)

    def test_hung_child_and_descendant_stop_without_harming_another_owned_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "owned-pids.json"
            child = """import json,os,pathlib,signal,subprocess,sys,time
signal.signal(signal.SIGINT, signal.SIG_IGN)
if hasattr(signal,'SIGBREAK'): signal.signal(signal.SIGBREAK,signal.SIG_IGN)
grandchild=subprocess.Popen([sys.executable,'-B','-S','-c',
    'import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),grandchild.pid]))
time.sleep(30)
"""
            unrelated = ProcessTree([sys.executable, "-B", "-S", "-c", "import time;time.sleep(30)"],
                                    cwd=directory).start()
            try:
                started = time.monotonic()
                result = supervise([sys.executable, "-B", "-S", "-c", child, str(evidence)],
                                   cwd=directory, timeout=1.5, grace_seconds=0.1, kill_seconds=2)
                self.assertTrue(result.timed_out)
                self.assertTrue(result.cleanup_verified)
                self.assertTrue(result.forced_cleanup)
                self.assertLess(time.monotonic() - started, 7)
                self.assertIsNone(unrelated.poll())
                self.assertTrue(evidence.is_file(), "Harmless child never reached its descendant launch")
                for pid in json.loads(evidence.read_text()):
                    with self.subTest(pid=pid):
                        self.assertFalse(running(pid), "An owned descendant survived termination")
            finally:
                unrelated.stop(grace_seconds=0, kill_seconds=2)

    def test_cancellation_reaps_the_real_owned_child(self):
        stop = threading.Event()
        timer = threading.Timer(0.3, stop.set)
        timer.start()
        try:
            result = supervise([sys.executable, "-B", "-S", "-c", "import time;time.sleep(30)"],
                               cwd=Path.cwd(), timeout=5, stop_event=stop,
                               grace_seconds=0.1, kill_seconds=2)
        finally:
            timer.cancel()
            timer.join()
        self.assertTrue(result.cancelled)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.cleanup_verified)


if __name__ == "__main__":
    unittest.main()
