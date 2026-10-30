"""Running copies of Axis, so `axis quit` can find and stop every one of them.

Each `axis` that serves the web app writes ~/Axis/run/instances/<pid>.json with its port
and removes it when it exits. `axis quit` and the **Quit Axis** button ask every recorded copy,
and anything else answering as Axis on the usual ports, to quit through POST /api/quit. A copy
that does not stop in time is terminated; on Windows its job object then ends the models it
started.

A copy is always asked with {"others": false}: it stops itself (and what it started) but does not
go on to ask the other copies, which the one asking is already doing.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .paths import axis_home

_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))

FIRST_PORT = 8765   # server.make_server tries this port and the next TRIES - 1 ports
TRIES = 20


def usual_ports() -> List[int]:
    """The ports an Axis that was not given one may be serving on."""
    return list(range(FIRST_PORT, FIRST_PORT + TRIES))


def _folder() -> Path:
    return axis_home() / "run" / "instances"


def register(port: int) -> Optional[Path]:
    """Record this Axis (called once it is serving). Returns the file, for unregister()."""
    path = _folder() / f"{os.getpid()}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid(), "port": port, "started": time.time()}), encoding="utf-8")
    except OSError:
        return None  # `axis quit` still finds Axis on the usual ports
    return path


def unregister(path: Optional[Path]) -> None:
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass


def recorded() -> List[Dict[str, Any]]:
    out = []
    for f in sorted(_folder().glob("*.json")) if _folder().is_dir() else []:
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
            out.append({"pid": int(e["pid"]), "port": int(e["port"]), "file": f})
        except (OSError, ValueError, KeyError, TypeError):
            unregister(f)
    return out


def ping(port: int, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    """What the Axis on this port says about itself, or None if nothing answers as Axis."""
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("app") == "axis" else None


def ask_to_quit(port: int) -> bool:
    """Ask the Axis on this port to stop itself only (it does not ask the other copies)."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b'{"others": false}', method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _LOCAL.open(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def find(ports: Iterable[int]) -> List[Dict[str, Any]]:
    """Every Axis answering now: the recorded ones plus any on `ports`. Stale records are removed."""
    from .procs import _pid_alive

    found: Dict[int, Dict[str, Any]] = {}
    for e in recorded():
        info = ping(e["port"])
        if info is not None and info.get("pid") in (None, e["pid"]):
            found[e["port"]] = {"port": e["port"], "pid": e["pid"], "file": e["file"]}
        elif not _pid_alive(e["pid"]):
            unregister(e["file"])
    for port in ports:
        if port in found:
            continue
        info = ping(port, timeout=0.5)
        if info is not None:
            # Whatever answers on a port can claim any process id, so a claimed id is never used to end a
            # process: only ids that Axis wrote to its own folder are. Without one, Axis is asked to quit.
            found[port] = {"port": port, "pid": 0, "file": None}
    for e in found.values():
        if e["pid"] == os.getpid():
            e["pid"] = 0  # never wait for, or end, the process asking
    return sorted(found.values(), key=lambda e: e["port"])


def quit_all(ports: Iterable[int], timeout: float = 45.0, say=print, skip_port: Optional[int] = None) -> int:
    """Ask every running Axis to quit and wait for it. Returns how many were stopped.

    skip_port leaves out the copy at that port: the one that is doing the asking (the Quit Axis button),
    which stops itself afterwards.
    """
    from .procs import _kill_pid, _pid_alive

    running = [e for e in find(ports) if skip_port is None or e["port"] != skip_port]
    for e in running:
        say(f"Stopping Axis on port {e['port']}" + (f" (process {e['pid']})" if e["pid"] else "") + "...")
        e["asked"] = ask_to_quit(e["port"])

    def gone(e: Dict[str, Any]) -> bool:
        return ping(e["port"], timeout=0.5) is None and not (e["pid"] and _pid_alive(e["pid"]))

    deadline = time.time() + timeout
    waiting = list(running)
    while waiting and time.time() < deadline:
        waiting = [e for e in waiting if not gone(e)]
        if waiting:
            time.sleep(0.3)
    for e in waiting:  # did not stop by itself; end it (on POSIX, SIGTERM still runs its cleanup first)
        if not e["pid"]:
            say(f"Axis on port {e['port']} did not stop. Close its window or terminal to stop it.")
        else:
            say(f"Axis on port {e['port']} did not stop in time; ending process {e['pid']}.")
            _kill_pid(e["pid"], timeout=15)
    for e in running:
        if e.get("file") is not None and not (e["pid"] and _pid_alive(e["pid"])):
            unregister(e["file"])
    return len(running)
