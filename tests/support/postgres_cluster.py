"""Import-only helper for an isolated, explicitly selected test PostgreSQL cluster."""

import json
import os
import subprocess
from pathlib import Path


def start_cluster(workspace, port=55439):
    root = Path(workspace).resolve() / ".refactor_validation" / "memory_pg17"
    binary = Path(r"C:\Program Files\PostgreSQL\17\bin")
    root.mkdir(parents=True, exist_ok=True)
    data = root / "pgdata"
    options = {"check": True, "capture_output": True, "text": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    if not (data / "PG_VERSION").exists():
        subprocess.run([str(binary / "initdb.exe"), "-D", str(data), "--encoding=UTF8",
                        "--locale=C", "--username=memory_test_admin", "--auth-local=trust",
                        "--auth-host=trust"], **options)
    status = subprocess.run([str(binary / "pg_ctl.exe"), "status", "-D", str(data)],
                            **{**options, "check": False})
    if status.returncode != 0:
        with (root / "control.log").open("a", encoding="utf-8") as control:
            subprocess.run([str(binary / "pg_ctl.exe"), "start", "-D", str(data),
                            "-l", str(root / "postgres.log"), "-o", f"-h 127.0.0.1 -p {port}", "-w"],
                           **{key: value for key, value in options.items() if key != "capture_output"},
                           stdout=control, stderr=subprocess.STDOUT)
    dsn = f"host=127.0.0.1 port={port} dbname=postgres user=memory_test_admin connect_timeout=3"
    (root / "test_connection.json").write_text(json.dumps({"dsn": dsn, "data_dir": str(data)}, indent=2), encoding="utf-8")
    return dsn


def stop_cluster(workspace):
    data = Path(workspace).resolve() / ".refactor_validation" / "memory_pg17" / "pgdata"
    binary = Path(r"C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe")
    subprocess.run([str(binary), "stop", "-D", str(data), "-m", "fast", "-w"],
                   check=True, capture_output=True, text=True,
                   **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
