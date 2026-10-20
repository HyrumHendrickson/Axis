from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .storage import AxisPaths, read_json, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def load_processes(paths: AxisPaths) -> dict[str, dict[str, str | int]]:
    data = read_json(paths.processes_file, {})
    return data if isinstance(data, dict) else {}


def save_processes(paths: AxisPaths, data: dict[str, dict[str, str | int]]) -> None:
    write_json(paths.processes_file, data)


def start_agent_process(paths: AxisPaths, agent_name: str) -> int:
    log_path = paths.logs / f"{agent_name}.log"
    cmd = [sys.executable, "-m", "axis.worker", "--agent", agent_name]
    log_handle = log_path.open("a", encoding="utf-8")
    package_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = package_root if not existing else f"{package_root}{os.pathsep}{existing}"

    kwargs: dict[str, object] = {
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "cwd": str(paths.home),
        "close_fds": True,
        "env": env,
    }

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["preexec_fn"] = os.setsid

    proc = subprocess.Popen(cmd, **kwargs)
    return int(proc.pid)


def stop_agent_process(pid: int, grace_seconds: float = 8.0) -> None:
    if not is_running(pid):
        return

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False)
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    end = time.time() + grace_seconds
    while time.time() < end:
        if not is_running(pid):
            return
        time.sleep(0.2)

    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
