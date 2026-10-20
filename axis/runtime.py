from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .storage import AxisPaths, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _is_running_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_running_windows(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        err = ctypes.get_last_error()
        if err == 5:
            return True
        return False
    try:
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        if not ok:
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def load_processes(paths: AxisPaths) -> dict[str, dict[str, str | int]]:
    if not paths.processes_file.exists():
        return {}
    try:
        raw = paths.processes_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        backup = paths.processes_file.with_name(f"{paths.processes_file.name}.corrupt-{int(time.time())}")
        try:
            shutil.copy2(paths.processes_file, backup)
        except OSError:
            pass
        return {}
    return data if isinstance(data, dict) else {}


def save_processes(paths: AxisPaths, data: dict[str, dict[str, str | int]]) -> None:
    write_json(paths.processes_file, data)


@contextmanager
def edit_processes(paths: AxisPaths, timeout_seconds: float = 5.0):
    with _processes_lock(paths, timeout_seconds=timeout_seconds):
        data = load_processes(paths)
        if not isinstance(data, dict):
            data = {}
        yield data
        save_processes(paths, data)


@contextmanager
def _processes_lock(paths: AxisPaths, timeout_seconds: float = 5.0, poll_seconds: float = 0.05):
    lock_path = paths.processes_file.with_suffix(f"{paths.processes_file.suffix}.lock")
    deadline = time.time() + timeout_seconds
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() >= deadline:
                raise RuntimeError("timed out waiting for process state lock")
            time.sleep(poll_seconds)

    try:
        if fd is not None:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


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

    try:
        proc = subprocess.Popen(cmd, **kwargs)
        return int(proc.pid)
    finally:
        log_handle.close()


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
