"""Programs Axis starts and stops for the user: llama-server, sd-server and ollama serve.

Every program Axis starts is recorded here, with its log, so the Models page can
show what is running and why something failed. Axis stops them all when it exits.

If Axis is killed without a chance to clean up, its programs could keep running
and hold on to memory. Two things prevent that:

* On Windows every program is placed in a job object that closes with Axis, so
  Windows ends them when Axis ends, however Axis ends.
* Elsewhere the programs are listed in ~/Axis/run/processes.json. When Axis
  starts again it takes back any that are still answering on their port, so the
  Models page can stop them.

Programs that run for one request (a picture from sd-cli, an OCR run, an agent's
code, an installer) are not servers, but they are tracked too (see track), so
quitting Axis ends them and the programs they started.
"""

from __future__ import annotations

import atexit
import collections
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from .paths import axis_home

LOG_LINES = 400
STOP_TIMEOUT = 15.0

# Local servers must never be reached through a system HTTP proxy.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ProcessError(Exception):
    """A program could not be started, or stopped before it was ready."""

    user_facing = True


class Cancelled(ProcessError):
    pass


@dataclass
class Managed:
    key: str                      # "llama:<id>", "image:<id>" or "ollama"
    kind: str                     # "llama", "image" or "ollama"
    target: str                   # the model id (or "" for ollama)
    port: int
    url: str
    cmd: List[str]
    proc: Optional[subprocess.Popen] = None
    pid: int = 0
    started: float = field(default_factory=time.time)
    ready: bool = False
    adopted: bool = False         # started by an earlier Axis that did not exit cleanly
    extra: Dict[str, Any] = field(default_factory=dict)
    log: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=LOG_LINES))

    def alive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return _pid_alive(self.pid)

    def tail(self, n: int = 12) -> str:
        return "\n".join(list(self.log)[-n:])

    def public(self) -> Dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "target": self.target, "port": self.port, "url": self.url,
                "pid": self.pid, "ready": self.ready, "alive": self.alive(), "adopted": self.adopted,
                "started": self.started, "log": list(self.log)[-40:], **self.extra}


_LOCK = threading.RLock()
_PROCS: Dict[str, Managed] = {}
_ADOPTED = False


# ----------------------------------------------------------------- helpers


def _state_path() -> Path:
    return axis_home() / "run" / "processes.json"


def _save_state() -> None:
    entries = [{"key": m.key, "kind": m.kind, "target": m.target, "port": m.port, "url": m.url, "pid": m.pid,
                "started": m.started, "extra": m.extra} for m in _PROCS.values() if m.pid]
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # losing this file only loses crash recovery


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)  # never use signal 0 on Windows: there os.kill always terminates
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_pid(pid: int, timeout: float = STOP_TIMEOUT) -> None:
    if not _pid_alive(pid):
        return
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
        except Exception:
            pass
        return
    try:
        os.killpg(pid, signal.SIGTERM)  # Axis starts each program in its own session (group id = pid)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port(start: int, span: int = 100, avoid: Optional[List[int]] = None) -> int:
    used = set(avoid or []) | {m.port for m in _PROCS.values()}
    for port in range(start, min(start + span, 65536)):
        if port not in used and port_free(port):
            return port
    raise ProcessError(f"No free port was found between {start} and {start + span - 1}.")


def http_status(url: str, timeout: float = 2.0) -> Optional[int]:
    """The HTTP status a local URL answers with, or None when nothing answers."""
    try:
        with _LOCAL.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


# --------------------------------------------- Windows: end children with Axis

_JOB = None


def _windows_job():
    """A job object that ends every program in it when Axis exits."""
    global _JOB
    if _JOB is not None or os.name != "nt":
        return _JOB
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        _JOB = job
    except Exception:
        _JOB = None
    return _JOB


def _assign_to_job(proc: subprocess.Popen) -> None:
    job = _windows_job()
    if not job:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject(job, int(proc._handle))  # type: ignore[attr-defined]
    except Exception:
        pass  # the program still runs; it is just not tied to Axis's lifetime


# ------------------------------------------- short-lived programs: end with Axis

_CHILDREN: Dict[int, subprocess.Popen] = {}
_CHILD_LIMIT = 64


def track(proc: subprocess.Popen) -> subprocess.Popen:
    """Remember a program that runs for one request, so quitting Axis ends it (and what it started).

    On Windows the program joins the job object, so it also ends if Axis is killed. Call untrack()
    when it has finished; a program that is forgotten is dropped once it has ended.
    """
    _assign_to_job(proc)
    with _LOCK:
        if len(_CHILDREN) >= _CHILD_LIMIT:
            for pid in [pid for pid, p in _CHILDREN.items() if p.poll() is not None]:
                del _CHILDREN[pid]
        _CHILDREN[proc.pid] = proc
    return proc


def untrack(proc: subprocess.Popen) -> None:
    with _LOCK:
        _CHILDREN.pop(proc.pid, None)


def tracked() -> List[subprocess.Popen]:
    """The tracked programs that are still running."""
    with _LOCK:
        return [p for p in _CHILDREN.values() if p.poll() is None]


def kill_tree(proc: subprocess.Popen) -> None:
    """End a program and everything it started. Never touches Axis's own process group."""
    if proc.poll() is not None:
        return   # nothing to end; its process id may already belong to another program
    if os.name == "nt":
        _taskkill(proc.pid)
    else:
        try:
            if os.getpgid(proc.pid) == proc.pid and proc.pid != os.getpgrp():
                os.killpg(proc.pid, signal.SIGKILL)   # it leads a group of its own (start_new_session)
        except OSError:
            pass
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def stop_children() -> int:
    """End every tracked program that is still running. Returns how many were running."""
    with _LOCK:
        found = list(_CHILDREN.values())
        _CHILDREN.clear()
    count = 0
    for proc in found:
        if proc.poll() is None:
            count += 1
        kill_tree(proc)
    return count


# ------------------------------------------------------------------ running


def _reader(m: Managed, on_line: Optional[Callable[[str], None]]) -> None:
    proc = m.proc
    if proc is None or proc.stdout is None:
        return
    buf = b""
    try:
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.replace(b"\r", b"\n").split(b"\n")
            for raw in lines:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    m.log.append(line[:500])
                    if on_line:
                        try:
                            on_line(line)
                        except Exception:
                            pass
    except (OSError, ValueError):
        pass
    finally:
        if buf.strip():
            m.log.append(buf.decode("utf-8", errors="replace").strip()[:500])


def start(key: str, kind: str, target: str, cmd: List[str], port: int, ready: Callable[[str], bool],
          cwd: Optional[Path] = None, timeout: float = 600.0, cancelled: Optional[Callable[[], bool]] = None,
          on_line: Optional[Callable[[str], None]] = None, env: Optional[Dict[str, str]] = None,
          extra: Optional[Dict[str, Any]] = None) -> Managed:
    """Start a server and wait until ready(url) is true.

    Raises ProcessError (with the program's last log lines) when it exits early,
    is not ready within `timeout` seconds, or when `cancelled()` becomes true.
    """
    adopt()
    _DEAD.pop(key, None)
    with _LOCK:
        old = _PROCS.get(key)
        if old is not None and old.alive():
            raise ProcessError("It is already running.")
        url = f"http://127.0.0.1:{port}"
        m = Managed(key=key, kind=kind, target=target, port=port, url=url, cmd=list(cmd), extra=dict(extra or {}))
        kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True  # Axis stops it; Ctrl+C in the terminal must not race that
        try:
            m.proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd and cwd.is_dir() else None, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      env={**os.environ, **(env or {})}, **kwargs)
        except OSError as exc:
            raise ProcessError(f"Could not start {Path(cmd[0]).name}: {exc.strerror or exc}") from exc
        _assign_to_job(m.proc)
        m.pid = m.proc.pid
        _PROCS[key] = m
        _save_state()
    threading.Thread(target=_reader, args=(m, on_line), daemon=True).start()

    deadline = time.time() + timeout
    while True:
        if cancelled and cancelled():
            stop(key)
            raise Cancelled("Stopped before it was ready.")
        if not m.alive():
            time.sleep(0.2)  # let the reader collect the last lines
            code = m.proc.poll() if m.proc else None
            with _LOCK:
                if _PROCS.get(key) is m:
                    del _PROCS[key]
                    _save_state()
            tail = m.tail()
            _close(m)
            raise ProcessError(f"{Path(cmd[0]).name} exited (code {code}) before it was ready."
                               + (f"\n{tail}" if tail else ""))
        try:
            ok = ready(url)
        except ProcessError as exc:   # the program reported that it cannot get ready
            stop(key)
            raise ProcessError(f"{exc}\n{m.tail()}".strip()) from exc
        except Exception:
            ok = False
        if ok:
            m.ready = True
            return m
        if time.time() > deadline:
            stop(key)
            raise ProcessError(f"{Path(cmd[0]).name} was not ready after {int(timeout)} seconds, so it was stopped."
                               + (f"\n{m.tail()}" if m.log else ""))
        time.sleep(0.25)


def stop(key: str, timeout: float = STOP_TIMEOUT) -> bool:
    """Stop a program Axis started. Returns False when it was not running."""
    with _LOCK:
        m = _PROCS.pop(key, None)
        _save_state()
    if m is None:
        return False
    if m.proc is not None:
        if m.proc.poll() is None:
            try:
                _terminate_tree(m.proc)
                m.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(m.proc)
                try:
                    m.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        _reap_group(m.proc)
        _close(m)
    elif m.pid:
        # Taken over from an earlier Axis. Only end it if it still answers on its
        # port, so a reused process id never ends an unrelated program.
        if http_status(m.url) is not None:
            _kill_pid(m.pid, timeout)
    return True


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Ask a program and everything it started to stop.

    `ollama serve` runs each model in a child process, and so may other servers; ending only
    the parent can leave those running and holding memory.
    """
    if os.name == "nt":
        _taskkill(proc.pid)
        if proc.poll() is None:
            proc.terminate()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)  # started with start_new_session, so its group id is its pid
    except OSError:
        proc.terminate()


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        _taskkill(proc.pid)
        if proc.poll() is None:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()


def _reap_group(proc: subprocess.Popen) -> None:
    """End anything the program started that outlived it (POSIX; Windows uses taskkill /T above)."""
    if os.name == "nt":
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass  # the group is already empty


def _taskkill(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def _close(m: Managed) -> None:
    """Release the pipe of a program that has ended (its reader thread stops at end of file)."""
    if m.proc is None or m.proc.stdout is None:
        return
    try:
        m.proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return
    try:
        m.proc.stdout.close()
    except (OSError, ValueError):
        pass


def get(key: str) -> Optional[Managed]:
    adopt()
    with _LOCK:
        m = _PROCS.get(key)
        if m is not None and not m.alive():
            # It ended on its own (crashed, or was closed outside Axis). Keep the
            # log for the Models page, but stop tracking it as running.
            del _PROCS[key]
            _save_state()
            _DEAD[key] = m
            _close(m)
            return None
        return m


_DEAD: Dict[str, Managed] = {}


def last_exit(key: str) -> Optional[Managed]:
    """A program that ended on its own, so its log can be shown once."""
    return _DEAD.get(key)


def forget_exit(key: str) -> None:
    _DEAD.pop(key, None)


def running(kind: Optional[str] = None) -> List[Managed]:
    adopt()
    with _LOCK:
        keys = list(_PROCS)
    out = []
    for key in keys:
        m = get(key)
        if m is not None and (kind is None or m.kind == kind):
            out.append(m)
    return out


def stop_all() -> None:
    """Stop every server Axis started, then every short-lived program it is still running."""
    with _LOCK:
        keys = list(_PROCS)
    for key in keys:
        try:
            stop(key, timeout=8)
        except Exception:
            pass
    try:
        stop_children()
    except Exception:
        pass


def adopt(probe: Optional[Callable[[Dict[str, Any]], bool]] = None) -> None:
    """Take back programs that an earlier Axis started and did not stop (once per run)."""
    global _ADOPTED
    with _LOCK:
        if _ADOPTED:
            return
        _ADOPTED = True
        try:
            entries = json.loads(_state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            entries = []
        for e in entries if isinstance(entries, list) else []:
            try:
                key, pid, port = str(e["key"]), int(e["pid"]), int(e["port"])
                url = str(e.get("url") or f"http://127.0.0.1:{port}")
            except (KeyError, TypeError, ValueError):
                continue
            if key in _PROCS or not _pid_alive(pid) or http_status(url) is None:
                continue
            m = Managed(key=key, kind=str(e.get("kind") or ""), target=str(e.get("target") or ""), port=port, url=url,
                        cmd=[], pid=pid, started=float(e.get("started") or time.time()), ready=True, adopted=True,
                        extra=e.get("extra") if isinstance(e.get("extra"), dict) else {})
            m.log.append("Started by an earlier Axis session, which did not stop it.")
            _PROCS[key] = m
        _save_state()


def reset_for_tests() -> None:
    global _ADOPTED
    stop_all()
    with _LOCK:
        _PROCS.clear()
        _DEAD.clear()
        _ADOPTED = False


atexit.register(stop_all)
