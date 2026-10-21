"""The local web server behind the Axis page.

Routes:
  GET    /                                  the app
  GET    /api/ping                          identifies a running Axis
  GET    /api/status                        model server and loaded models
  GET    /api/agents                        list agents
  POST   /api/agents                        create an agent
  GET    /api/agents/<name>                 one agent
  PUT    /api/agents/<name>                 update (or rename) an agent
  DELETE /api/agents/<name>                 delete an agent and its folder
  GET    /api/agents/<name>/files           every file in the agent's folder (flat list)
  POST   /api/agents/<name>/open-folder     show the folder in Explorer/Finder
  GET    /api/files/<name>?path=dir         one folder of an agent's workspace
  GET    /api/files/<name>/view?path=file   a file prepared for preview
  GET    /api/files/<name>/raw?path=file    the file itself (images, downloads)
  POST   /api/files/<name>/reveal?path=x    select the file in Explorer/Finder
  GET    /api/skills                        list skills (built-in and custom)
  POST   /api/skills                        create a custom skill
  GET    /api/skills/<id>                   one skill
  PUT    /api/skills/<id>                   update a custom skill
  DELETE /api/skills/<id>                   delete a custom skill (and remove it from agents)
  POST   /api/chat                          stream a reply as newline-delimited JSON
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, agents, backend, runner, skills, tools

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 5 * 1024 * 1024
MAX_PREVIEW = 512 * 1024
HIDDEN = {".axis-tmp"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}


class ClientGone(Exception):
    """The browser closed the connection (for example, the user pressed Stop)."""


class AxisServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two programs share a port, which hides conflicts.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, model_url: Optional[str] = None):
        super().__init__(address, Handler)
        self.model_url = model_url
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _count(folder: Path) -> Optional[int]:
    try:
        return sum(1 for c in folder.iterdir() if c.name not in HIDDEN)
    except OSError:
        return None


def folder_listing(agent: agents.Agent, rel: str) -> Dict[str, Any]:
    ws = agent.workspace
    ws.mkdir(parents=True, exist_ok=True)
    try:
        folder = tools.resolve(ws, rel, allow_root=True)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not folder.is_dir():
        raise agents.AgentError(f"Folder '{rel}' does not exist in {agent.name}'s folder.")
    entries = []
    for child in folder.iterdir():
        if child.name in HIDDEN:
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        is_dir = child.is_dir()
        entries.append({
            "name": child.name,
            "path": child.relative_to(ws.resolve()).as_posix(),
            "type": "dir" if is_dir else "file",
            "size": None if is_dir else st.st_size,
            "modified": _when(st.st_mtime),
            "items": _count(child) if is_dir else None,
        })
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    path = folder.relative_to(ws.resolve()).as_posix()
    return {"agent": agent.name, "path": "" if path == "." else path, "workspace": str(ws), "entries": entries}


def file_target(agent: agents.Agent, rel: str) -> Path:
    try:
        path = tools.resolve(agent.workspace, rel)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not path.is_file():
        raise agents.AgentError(f"File '{rel}' does not exist in {agent.name}'s folder.")
    return path


def file_preview(agent: agents.Agent, rel: str) -> Dict[str, Any]:
    path = file_target(agent, rel)
    st = path.stat()
    info: Dict[str, Any] = {
        "agent": agent.name, "path": path.relative_to(agent.workspace.resolve()).as_posix(),
        "name": path.name, "size": st.st_size, "modified": _when(st.st_mtime),
    }
    ext = path.suffix.lower()
    if ext in IMAGE_TYPES:
        return {**info, "kind": "image"}
    if ext == ".pdf":
        return {**info, "kind": "pdf"}
    with path.open("rb") as f:
        data = f.read(MAX_PREVIEW + 1)
    truncated = len(data) > MAX_PREVIEW
    data = data[:MAX_PREVIEW]
    if b"\0" in data[:8192]:
        return {**info, "kind": "binary"}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(data) - 4:  # cut in the middle of a character
            text = data[:exc.start].decode("utf-8", errors="replace")
        else:
            try:
                text = data.decode("cp1252")
            except UnicodeDecodeError:
                return {**info, "kind": "binary"}
    return {**info, "kind": "text", "text": text, "truncated": truncated}


def open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal(path: Path) -> None:
    """Show a file selected in Explorer/Finder. Never runs the file."""
    if path.is_dir():
        return open_folder(path)
    if os.name == "nt":
        # Explorer needs exactly: explorer /select,"C:\path with spaces\file"
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        open_folder(path.parent)


class Handler(BaseHTTPRequestHandler):
    server: AxisServer
    server_version = f"Axis/{__version__}"

    def log_message(self, format, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------------------ helpers

    def _send(self, code: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise agents.AgentError("Request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise agents.AgentError("Request body is not valid JSON.") from exc

    def _trusted(self) -> bool:
        """Only answer requests addressed to this machine, from this page.

        This stops other websites open in the browser from using Axis.
        """
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and urllib.parse.urlsplit(origin).netloc not in self.server.allowed_hosts:
            return False
        return True

    def _route(self, method: str) -> None:
        if not self._trusted():
            return self._error(403, "Forbidden.")
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        param = lambda key: (query.get(key) or [""])[0]  # noqa: E731
        parts: List[str] = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
        try:
            return self._dispatch(method, parts, param)
        except (agents.AgentError, skills.SkillError) as exc:
            missing = "does not exist" in str(exc)
            return self._error(404 if missing else 400, str(exc))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception as exc:  # never let one bad request take the page down
            return self._error(500, f"Unexpected error: {exc}")

    def _dispatch(self, method: str, parts: List[str], param) -> None:
        n = len(parts)
        if n == 0:
            if method == "GET":
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif parts[0] != "api":
            pass
        elif parts[1:] == ["ping"] and method == "GET":
            return self._json(200, {"app": "axis", "version": __version__})
        elif parts[1:] == ["status"] and method == "GET":
            return self._status()
        elif parts[1:] == ["chat"] and method == "POST":
            return self._chat()

        elif n >= 2 and parts[1] == "agents":
            if n == 2 and method == "GET":
                return self._json(200, [a.to_dict() for a in agents.list_all()])
            if n == 2 and method == "POST":
                return self._json(201, agents.create(self._body()).to_dict())
            if n == 3:
                if method == "GET":
                    return self._json(200, agents.load(parts[2]).to_dict())
                if method == "PUT":
                    return self._json(200, agents.update(parts[2], self._body()).to_dict())
                if method == "DELETE":
                    agents.delete(parts[2])
                    return self._json(200, {"deleted": parts[2]})
            if n == 4 and parts[3] == "files" and method == "GET":
                agent = agents.load(parts[2])
                return self._json(200, {"workspace": str(agent.workspace), "files": agents.list_workspace(agent)})
            if n == 4 and parts[3] == "open-folder" and method == "POST":
                agent = agents.load(parts[2])
                agent.workspace.mkdir(parents=True, exist_ok=True)
                open_folder(agent.workspace)
                return self._json(200, {"opened": str(agent.workspace)})

        elif n >= 3 and parts[1] == "files":
            agent = agents.load(parts[2])
            action = parts[3] if n == 4 else ""
            if n == 3 and method == "GET":
                return self._json(200, folder_listing(agent, param("path")))
            if action == "view" and method == "GET":
                return self._json(200, file_preview(agent, param("path")))
            if action == "raw" and method == "GET":
                return self._raw(file_target(agent, param("path")), download=param("download") == "1")
            if action == "reveal" and method == "POST":
                agent.workspace.mkdir(parents=True, exist_ok=True)
                try:
                    target = tools.resolve(agent.workspace, param("path"), allow_root=True)
                except tools.ToolError as exc:
                    raise agents.AgentError(str(exc)) from exc
                if not target.exists():
                    raise agents.AgentError(f"'{param('path')}' does not exist in {agent.name}'s folder.")
                reveal(target)
                return self._json(200, {"revealed": str(target)})

        elif n >= 2 and parts[1] == "skills":
            if n == 2 and method == "GET":
                return self._json(200, skills.list_all())
            if n == 2 and method == "POST":
                return self._json(201, skills.create(self._body()))
            if n == 3:
                if method == "GET":
                    return self._json(200, skills.get(parts[2]))
                if method == "PUT":
                    return self._json(200, skills.update(parts[2], self._body()))
                if method == "DELETE":
                    skills.delete(parts[2])
                    agents.remove_skill_everywhere(parts[2])
                    return self._json(200, {"deleted": parts[2]})
        return self._error(404, "Not found.")

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    # ------------------------------------------------------------ endpoints

    def _raw(self, path: Path, download: bool) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/xhtml+xml"):
            ctype = "text/plain"  # never let an agent's page run on this site
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        name = urllib.parse.quote(path.name)
        extra = {"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{name}"}
        if not ctype.startswith("application/pdf"):
            extra["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile, 256 * 1024)

    def _status(self) -> None:
        try:
            conn = backend.connect(self.server.model_url)
        except backend.BackendError as exc:
            message = str(exc).split("\n")[0]
            running = not isinstance(exc, backend.ServerUnavailable)
            return self._json(200, {"ready": False, "server_running": running, "message": message, "models": []})
        return self._json(
            200,
            {"ready": True, "server_running": True, "server": conn.label, "url": conn.url, "models": conn.models},
        )

    def _chat(self) -> None:
        body = self._body()
        if not isinstance(body, dict):
            raise agents.AgentError("Request body must be a JSON object.")
        agent = agents.load(body["agent"]) if body.get("agent") else None
        messages = agents.build_messages(agent, body.get("messages"))

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True

        def emit(event: dict) -> None:
            try:
                self.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as exc:
                raise ClientGone() from exc

        try:
            conn = backend.connect(self.server.model_url)
            requested = body.get("model") or (agent.model if agent else "") or None
            model = backend.resolve_model(conn, requested)
            emit({"model": model})
            runner.reply(conn, model, messages, emit,
                         tool_names=agents.tool_names(agent), workspace=agent.workspace if agent else None)
            emit({"done": True})
        except ClientGone:
            return
        except Exception as exc:  # headers are already sent, so report errors in the stream
            message = str(exc) if isinstance(exc, backend.BackendError) else f"Unexpected error: {exc}"
            try:
                emit({"error": message})
            except ClientGone:
                return


def find_running(port: int) -> bool:
    """True if an Axis server is already answering on this port."""
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=1) as resp:
            return json.loads(resp.read().decode("utf-8")).get("app") == "axis"
    except Exception:
        return False


def make_server(port: int, model_url: Optional[str] = None, tries: int = 20) -> AxisServer:
    """Bind to the first free port starting at `port` (0 picks any free port)."""
    last: Optional[OSError] = None
    for candidate in ([0] if port == 0 else range(port, port + tries)):
        try:
            return AxisServer(("127.0.0.1", candidate), model_url)
        except OSError as exc:
            last = exc
    raise OSError(f"No free port found from {port} to {port + tries - 1}: {last}")
