"""The local web server behind the Axis page.

Routes:
  GET    /                                  the app
  GET    /api/ping                          identifies a running Axis
  POST   /api/quit                          stops Axis (and the models it started)
  POST   /api/page?id=x[&closing=1]         an open page says it is still there (or is closing)
  GET    /api/status                        model server and loaded models
  GET    /api/update                        installed and newest version (the first call starts the check)
  POST   /api/update                        download and install the newest Axis in the background
  POST   /api/update/check                  check GitHub again now
  GET    /api/models                        details for each model (memory, context, size)
  GET    /api/agents                        list agents
  POST   /api/agents                        create an agent
  GET    /api/agents/<name>                 one agent
  PUT    /api/agents/<name>                 update (or rename) an agent
  DELETE /api/agents/<name>                 delete an agent and its folder
  GET    /api/agents/<name>/files           every file in the agent's folder (flat list)
  POST   /api/agents/<name>/open-folder     show the folder in Explorer/Finder
  GET    /api/files/<name>?path=dir         one folder of an agent's folder (the Workspace tab)
                                            (<name> "_shared" means the folder shared by agents)
  GET    /api/files/<name>/view?path=file   a file prepared for preview
  GET    /api/files/<name>/raw?path=file    the file itself (images, downloads)
  POST   /api/files/<name>/reveal?path=x    select the file in Explorer/Finder
  POST   /api/files/<name>/upload?path=dir&name=f   save the request body as a file
                                            (&mkdirs=1 makes the folder first when it is missing)
  POST   /api/files/<name>/mkdir?path=dir   make a folder
  PUT    /api/files/<name>/save?path=file   replace a text file with the request body (UTF-8 text)
  POST   /api/files/<name>/run?path=file    run a .py file (guarded like Run Python) and return its output
  DELETE /api/files/<name>?path=x           delete a file or folder
  GET    /api/skills                        list skills (built-in and custom)
  POST   /api/skills                        create a custom skill
  GET    /api/skills/<id>                   one skill
  PUT    /api/skills/<id>                   update a custom skill
  DELETE /api/skills/<id>                   delete a custom skill (and remove it from agents)
  POST   /api/chat                          stream a reply as newline-delimited JSON
                                            (a user message may carry "images": [{place, path}] for
                                            models that read pictures; "terminal": id shows the agent
                                            that terminal's recent output)
  GET    /api/terminal                      open terminals
  POST   /api/terminal                      open a terminal {place?, path?} in that folder (default: shared)
  GET    /api/terminal/<id>?since=n         output from offset n {text, next, reset, alive, exit}
  POST   /api/terminal/<id>/input           type into the terminal {text}
  POST   /api/terminal/<id>/interrupt       stop the running command (Ctrl+C)
  DELETE /api/terminal/<id>                 close a terminal
  GET    /api/chats                         saved chats (newest first, without messages)
  GET    /api/chats/<id>                    one saved chat with its messages
  PUT    /api/chats/<id>                    create or replace a saved chat
  PATCH  /api/chats/<id>                    rename a saved chat
  DELETE /api/chats/<id>                    delete a saved chat
  GET    /api/images                        sd-cli status, settings and image models
  PUT    /api/images/settings               set the sd-cli path, working folder, default model
  POST   /api/images/models                 add an image model
  PUT    /api/images/models/<id>            change an image model
  DELETE /api/images/models/<id>            remove an image model (its files are not touched)
  GET    /api/images/gallery?place=x        pictures in a folder's images/ ("_shared" or an agent)
  POST   /api/images/generate               make a picture, streaming progress as newline-delimited JSON
                                            ("preview": true also streams each step as a small picture;
                                            "init_image": {place, path} and "strength" edit that picture)
  GET    /api/ocr                           Tesseract, the PyTorch environment, settings and OCR models
  PUT    /api/ocr/settings                  Tesseract path, Python for PyTorch, device, PyTorch package index
  POST   /api/ocr/run                       read the text in a picture {model, image: {place, path}, languages?, psm?},
                                            streaming progress as newline-delimited JSON
  GET    /api/manage                        engines, installed models, what is running, and jobs
  POST   /api/manage/install                install a model {engine, name, choice?, role?, target?}
                                            (engine "ocr" takes {kind: tesseract|easyocr|doctr, name})
                                            (answers {choose: [...]} when the user must pick a file;
                                            an image name such as z-image-turbo installs every file it needs)
  POST   /api/manage/start                  start a model {engine, model}
  POST   /api/manage/stop                   stop a model {engine, model}
  POST   /api/manage/delete                 delete a model and its files {engine, model, files?}
  POST   /api/manage/ollama/start           start the Ollama server
  POST   /api/manage/ollama/stop            stop the Ollama server (only if Axis started it)
  POST   /api/manage/engines/install        install or update llama.cpp, stable-diffusion.cpp, Tesseract or
                                            PyTorch for OCR {engine: llama|sd|tesseract|pytorch, build?}
  POST   /api/manage/engines/remove         remove the copy Axis installed {engine}
  PUT    /api/manage/settings               llama-server path, context size, extra arguments
  POST   /api/manage/jobs/<id>/cancel       cancel an install or start
  DELETE /api/manage/jobs/<id>              remove a finished job from the list
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import __version__, agents, backend, catalog, chats, engines, hub, images, jobs, manager, ocr, procs, runner, skills, terminal, tools, updates
from .paths import shared_root

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 5 * 1024 * 1024
MAX_UPLOAD = 512 * 1024 * 1024
MAX_PREVIEW = 512 * 1024
MAX_SAVE = 5 * 1024 * 1024          # largest text file the Workspace editor saves
CHAT_IMAGE_TYPES = {".png", ".jpg", ".jpeg"}   # what both Ollama and llama.cpp read
MAX_CHAT_IMAGE = 20 * 1024 * 1024
CHAT_IMAGES_SENT = 4                            # only the newest pictures are sent again with each message
HIDDEN = set(tools.HIDDEN_NAMES)
_PAGE: Optional[bytes] = None


def page() -> bytes:
    """The page, read once. An update replaces the file on disk while this (old) server still runs,
    so a reload must keep getting the page that matches this server until Axis is restarted."""
    global _PAGE
    if _PAGE is None:
        _PAGE = (WEB_DIR / "index.html").read_bytes()
    return _PAGE


IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}


# The Workspace tab's name for the shared folder. It can never clash with an agent,
# because agent names must start with a letter or number.
SHARED_NAME = "_shared"


class SharedPlace:
    """Stands in for an agent in the file routes so the shared folder can be browsed."""

    name = SHARED_NAME

    @property
    def workspace(self) -> Path:
        return shared_root()


Place = Union[agents.Agent, SharedPlace]


def place(name: str) -> Place:
    if name == SHARED_NAME:
        shared_root().mkdir(parents=True, exist_ok=True)
        return SharedPlace()
    return agents.load(name)


def _owner(agent: Place) -> str:
    return "the shared folder" if isinstance(agent, SharedPlace) else f"{agent.name}'s folder"


class ClientGone(Exception):
    """The browser closed the connection (for example, the user pressed Stop)."""


class Pages:
    """The Axis pages that are open, so Axis can stop when its window is closed.

    Each page reports itself every few seconds and says when it is closing. A reload closes
    one page and opens another, so Axis waits `grace` seconds before treating "no pages" as
    closed. A page that stops reporting without saying goodbye (the browser crashed) is
    dropped after `stale` seconds; this is long, because browsers slow the timers of hidden
    windows to about once a minute.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: Dict[str, float] = {}
        self._ever = False
        self._changed = time.monotonic()

    def beat(self, page_id: str, now: Optional[float] = None) -> None:
        with self._lock:
            self._seen[page_id] = time.monotonic() if now is None else now
            self._ever = True

    def gone(self, page_id: str, now: Optional[float] = None) -> None:
        with self._lock:
            if self._seen.pop(page_id, None) is not None:
                self._changed = time.monotonic() if now is None else now

    def count(self) -> int:
        with self._lock:
            return len(self._seen)

    def all_closed(self, now: float, stale: float, grace: float) -> bool:
        """True once a page has been open and every page has since closed."""
        with self._lock:
            for page_id, last in list(self._seen.items()):
                if now - last > stale:
                    del self._seen[page_id]
                    self._changed = min(self._changed, now - grace)
            return self._ever and not self._seen and now - self._changed >= grace


class AxisServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two programs share a port, which hides conflicts.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, model_url: Optional[str] = None):
        super().__init__(address, Handler)
        self.model_url = model_url
        self.pages = Pages()
        self.close_with_window = False  # set when Axis opened its own window (cli.cmd_serve)
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


def folder_listing(agent: "Place", rel: str) -> Dict[str, Any]:
    ws = agent.workspace
    ws.mkdir(parents=True, exist_ok=True)
    try:
        folder = tools.resolve(ws, rel, allow_root=True)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not folder.is_dir():
        raise agents.AgentError(f"Folder '{rel}' does not exist in {_owner(agent)}.")
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


def file_target(agent: "Place", rel: str) -> Path:
    try:
        path = tools.resolve(agent.workspace, rel)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not path.is_file():
        raise agents.AgentError(f"File '{rel}' does not exist in {_owner(agent)}.")
    return path


def file_preview(agent: "Place", rel: str) -> Dict[str, Any]:
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


def image_ref(ref: Any, label: str = "picture") -> "tuple[Place, Path]":
    """A {place, path} reference to an image file inside an agent's folder or the shared folder."""
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref["path"].strip():
        raise agents.AgentError(f"Choose a {label}.")
    where = place(str(ref.get("place") or SHARED_NAME))
    return where, file_target(where, ref["path"])


def attach_images(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Swap each message's {place, path} image references for the files themselves.

    Only the newest CHAT_IMAGES_SENT pictures are sent; older ones, and files that are
    gone or cannot be read by the model servers, become a short note in the text.
    """
    budget = CHAT_IMAGES_SENT
    out: List[Dict[str, Any]] = []
    for m in reversed(messages):
        refs = m.get("images")
        if not refs:
            out.append(m)
            continue
        kept: List[Path] = []
        notes: List[str] = []
        for ref in refs:
            name = str(ref.get("path") or "")
            if budget <= 0:
                notes.append(f"[earlier picture: {name}]")
                continue
            try:
                _, path = image_ref(ref)
            except agents.AgentError:
                notes.append(f"[picture no longer available: {name}]")
                continue
            if path.suffix.lower() not in CHAT_IMAGE_TYPES or path.stat().st_size > MAX_CHAT_IMAGE:
                notes.append(f"[picture that could not be sent: {name}]")
                continue
            kept.append(path)
            budget -= 1
        msg = {k: v for k, v in m.items() if k != "images"}
        if notes:
            msg["content"] = (msg["content"] + "\n\n" + "\n".join(notes)).strip()
        if kept:
            msg["images"] = kept
        out.append(msg)
    return list(reversed(out))


def delete_path(agent: "Place", rel: str) -> str:
    """Delete a file or a whole folder inside the agent's folder (never the folder itself)."""
    try:
        target = tools.resolve(agent.workspace, rel)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
    else:
        raise agents.AgentError(f"'{rel}' does not exist in {_owner(agent)}.")
    return target.relative_to(agent.workspace.resolve()).as_posix()


def make_dir(agent: "Place", rel: str) -> str:
    agent.workspace.mkdir(parents=True, exist_ok=True)
    try:
        target = tools.resolve(agent.workspace, rel)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if target.name in HIDDEN:
        raise agents.AgentError("That folder name cannot be used.")
    if target.exists():
        raise agents.AgentError(f"'{rel}' already exists.")
    target.mkdir(parents=True)
    return target.relative_to(agent.workspace.resolve()).as_posix()


def save_text(agent: "Place", rel: str, data: bytes) -> Dict[str, Any]:
    """Replace a text file in the folder with data (UTF-8). The file must exist already."""
    path = file_target(agent, rel)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise agents.AgentError("The text could not be read as UTF-8.") from exc
    with path.open("rb") as f:
        old_start = f.read(65536)
    if b"\r\n" in old_start and b"\r\n" not in data:   # keep Windows line endings
        data = data.replace(b"\n", b"\r\n")
    tmp = path.with_name(path.name + ".axis-save")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    st = path.stat()
    return {"path": path.relative_to(agent.workspace.resolve()).as_posix(), "size": st.st_size, "modified": _when(st.st_mtime)}


def run_script(agent: "Place", rel: str) -> Dict[str, Any]:
    """Run a .py file from the folder the way an agent's run_python_file does (guarded, time-limited)."""
    shared = None if isinstance(agent, SharedPlace) else agents.shared_folder(agent)
    started = time.monotonic()
    try:
        result = tools.run_file(tools.Places(agent.workspace, shared), rel)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    return {**result, "path": rel, "seconds": round(time.monotonic() - started, 2), "timeout": tools.PYTHON_TIMEOUT}


def folder_path(agent: "Place", rel: str) -> Path:
    agent.workspace.mkdir(parents=True, exist_ok=True)
    try:
        folder = tools.resolve(agent.workspace, rel, allow_root=True)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not folder.is_dir():
        raise agents.AgentError(f"Folder '{rel}' does not exist in {_owner(agent)}.")
    return folder


def with_terminal(messages: List[Dict[str, Any]], sid: Any) -> List[Dict[str, Any]]:
    """Show the agent the newest output of a terminal, added to the newest message."""
    if not sid:
        return messages
    try:
        session = terminal.get(str(sid))
    except terminal.TerminalError:
        return messages
    shot = session.snapshot().strip("\n") or "(nothing yet)"
    note = (f"\n\n---\nThe user's terminal ({session.shell}, started in {session.cwd}). "
            f"Its newest output, which you can see but not type into:\n```\n{shot}\n```")
    last = messages[-1]
    return messages[:-1] + [{**last, "content": last["content"] + note}]


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

    def _length(self) -> int:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise agents.AgentError("Bad Content-Length header.")
        if length < 0:
            raise agents.AgentError("Bad Content-Length header.")
        return length

    def _body(self) -> Any:
        length = self._length()
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
        except (agents.AgentError, skills.SkillError, chats.ChatError, images.ImageError, manager.ManageError,
                hub.HubError, jobs.JobError, procs.ProcessError, engines.EngineError, catalog.CatalogError,
                ocr.OcrError, terminal.TerminalError) as exc:
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
                return self._send(200, page(), "text/html; charset=utf-8")
        elif parts[0] != "api":
            pass
        elif parts[1:] == ["ping"] and method == "GET":
            return self._json(200, {"app": "axis", "version": __version__, "pid": os.getpid()})
        elif parts[1:] == ["quit"] and method == "POST":
            # Stop serve_forever from another thread; cli.cmd_serve then stops the models Axis started.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return self._json(200, {"stopping": True})
        elif parts[1:] == ["page"] and method == "POST":
            page_id = param("id")[:64]
            if not page_id:
                raise agents.AgentError("A page id is required.")
            if param("closing"):
                self.server.pages.gone(page_id)
            else:
                self.server.pages.beat(page_id)
            return self._json(200, {"closeWithWindow": self.server.close_with_window})
        elif parts[1:] == ["status"] and method == "GET":
            return self._status()
        elif parts[1:] == ["update"]:
            if method == "GET":
                return self._json(200, updates.state())
            if method == "POST":
                return self._json(202, updates.start_update())
        elif parts[1:] == ["update", "check"] and method == "POST":
            updates.check(force=True)
            return self._json(200, updates.state())
        elif parts[1:] == ["models"] and method == "GET":
            return self._json(200, backend.describe(self.server.model_url))
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
            agent = place(parts[2])
            action = parts[3] if n == 4 else ""
            if n == 3 and method == "GET":
                return self._json(200, folder_listing(agent, param("path")))
            if n == 3 and method == "DELETE":
                return self._json(200, {"deleted": delete_path(agent, param("path"))})
            if action == "upload" and method == "POST":
                return self._json(201, self._upload(agent, param("path"), param("name"), make=param("mkdirs") == "1"))
            if action == "mkdir" and method == "POST":
                return self._json(201, {"made": make_dir(agent, param("path"))})
            if action == "save" and method == "PUT":
                length = self._length()
                if length > MAX_SAVE:
                    raise agents.AgentError(f"Files larger than {MAX_SAVE // (1024 * 1024)} MB cannot be saved here.")
                return self._json(200, save_text(agent, param("path"), self.rfile.read(length) if length else b""))
            if action == "run" and method == "POST":
                return self._json(200, run_script(agent, param("path")))
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
                    raise agents.AgentError(f"'{param('path')}' does not exist in {_owner(agent)}.")
                reveal(target)
                return self._json(200, {"revealed": str(target)})

        elif n >= 2 and parts[1] == "terminal":
            if n == 2 and method == "GET":
                return self._json(200, terminal.list_all())
            if n == 2 and method == "POST":
                body = self._body()
                body = body if isinstance(body, dict) else {}
                where = place(str(body.get("place") or SHARED_NAME))
                rel = str(body.get("path") or "")
                folder = folder_path(where, rel)
                label = rel.rsplit("/", 1)[-1] if rel else ("shared" if isinstance(where, SharedPlace) else where.name)
                return self._json(201, terminal.start(folder, label).public())
            if n >= 3:
                session = terminal.get(parts[2])
                if n == 3 and method == "GET":
                    try:
                        since = int(param("since") or 0)
                    except ValueError:
                        since = 0
                    return self._json(200, session.read(since))
                if n == 3 and method == "DELETE":
                    terminal.close(session.id)
                    return self._json(200, {"closed": session.id})
                if parts[3:] == ["input"] and method == "POST":
                    body = self._body()
                    session.write(body.get("text", "") if isinstance(body, dict) else "")
                    return self._json(200, {"ok": True})
                if parts[3:] == ["interrupt"] and method == "POST":
                    session.interrupt()
                    return self._json(200, {"ok": True})

        elif n >= 2 and parts[1] == "chats":
            if n == 2 and method == "GET":
                return self._json(200, chats.list_all())
            if n == 3:
                if method == "GET":
                    return self._json(200, chats.get(parts[2]))
                if method == "PUT":
                    body = self._body()
                    agent = body.get("agent") if isinstance(body, dict) else None
                    agent = agents.load(agent).name if agent else ""
                    return self._json(200, chats.save(parts[2], body, agent))
                if method == "PATCH":
                    body = self._body()
                    return self._json(200, chats.rename(parts[2], body.get("title") if isinstance(body, dict) else None))
                if method == "DELETE":
                    chats.delete(parts[2])
                    return self._json(200, {"deleted": parts[2]})

        elif n >= 2 and parts[1] == "images":
            if n == 2 and method == "GET":
                return self._json(200, images.status())
            if parts[2:] == ["settings"] and method == "PUT":
                return self._json(200, images.save_settings(self._body()))
            if parts[2:] == ["gallery"] and method == "GET":
                return self._json(200, {"images": images.gallery(place(param("place") or SHARED_NAME).workspace)})
            if parts[2:] == ["generate"] and method == "POST":
                return self._generate()
            if n >= 3 and parts[2] == "models":
                if n == 3 and method == "POST":
                    return self._json(201, images.create_model(self._body()))
                if n == 4 and method == "PUT":
                    return self._json(200, images.update_model(parts[3], self._body()))
                if n == 4 and method == "DELETE":
                    files = param("files") or "none"
                    if manager.image_server_url(parts[3]):
                        manager.image_stop(parts[3])
                    return self._json(200, images.delete_model(parts[3], files=files))

        elif n >= 2 and parts[1] == "ocr":
            if n == 2 and method == "GET":
                return self._json(200, ocr.status())
            if parts[2:] == ["settings"] and method == "PUT":
                return self._json(200, ocr.save_settings(self._body()))
            if parts[2:] == ["run"] and method == "POST":
                return self._ocr()

        elif n >= 2 and parts[1] == "manage":
            url = self.server.model_url
            if n == 2 and method == "GET":
                return self._json(200, manager.state(url))
            if n == 3 and method == "POST" and parts[2] in ("install", "start", "stop", "delete"):
                result = getattr(manager, parts[2])(self._body(), url)
                return self._json(202 if "job" in result else 200, result)
            if n == 4 and parts[2] == "ollama" and parts[3] in ("start", "stop") and method == "POST":
                result = manager.ollama_server(parts[3], url)
                return self._json(202 if "job" in result else 200, result)
            if n == 4 and parts[2] == "engines" and parts[3] in ("install", "remove") and method == "POST":
                if parts[3] == "install":
                    return self._json(202, manager.install_engine(self._body()))
                return self._json(200, manager.remove_engine(self._body()))
            if parts[2:] == ["settings"] and method == "PUT":
                manager.save_settings(self._body())
                return self._json(200, manager.state(url))
            if n >= 4 and parts[2] == "jobs":
                if n == 5 and parts[4] == "cancel" and method == "POST":
                    return self._json(200, jobs.cancel(parts[3]).public())
                if n == 4 and method == "DELETE":
                    jobs.dismiss(parts[3])
                    return self._json(200, {"deleted": parts[3]})

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

    def do_PATCH(self):
        self._route("PATCH")

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

    def _upload(self, agent: "Place", rel_dir: str, name: str, make: bool = False) -> Dict[str, Any]:
        length = self._length()
        if length > MAX_UPLOAD:
            raise agents.AgentError(f"Files larger than {MAX_UPLOAD // (1024 * 1024)} MB cannot be uploaded.")
        name = (name or "").strip()
        if not name or name in (".", "..") or any(c in name for c in '/\\:*?"<>|\0') or name in HIDDEN:
            raise agents.AgentError("That file name cannot be used.")
        ws = agent.workspace
        ws.mkdir(parents=True, exist_ok=True)
        try:
            folder = tools.resolve(ws, rel_dir, allow_root=True)
        except tools.ToolError as exc:
            raise agents.AgentError(str(exc)) from exc
        if make and not folder.exists() and not any(part in HIDDEN for part in Path(rel_dir).parts):
            folder.mkdir(parents=True)
        if not folder.is_dir():
            raise agents.AgentError(f"Folder '{rel_dir}' does not exist in {_owner(agent)}.")
        stem, dot, ext = name.rpartition(".") if "." in name.lstrip(".") else (name, "", "")
        target, n = folder / name, 2
        while target.exists():  # never overwrite: report.txt -> report (2).txt
            target, n = folder / (f"{stem} ({n}){dot}{ext}"), n + 1
        tmp = folder / (target.name + ".axis-upload")
        remaining = length
        try:
            with tmp.open("wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 256 * 1024))
                    if not chunk:
                        raise agents.AgentError("The upload was interrupted.")
                    f.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
        return {"path": target.relative_to(ws.resolve()).as_posix(), "size": length}

    def _status(self) -> None:
        try:
            conns = backend.connect_all(self.server.model_url)
        except backend.BackendError as exc:
            message = str(exc).split("\n")[0]
            running = not isinstance(exc, backend.ServerUnavailable)
            return self._json(200, {"ready": False, "server_running": running, "message": message, "models": []})
        labels = []
        for c in conns:
            if c.label not in labels:
                labels.append(c.label)
        return self._json(200, {"ready": True, "server_running": True, "server": " and ".join(labels),
                                "url": conns[0].url if len(conns) == 1 else ", ".join(c.url for c in conns),
                                "models": backend.loaded_models(conns)})

    def _generate(self) -> None:
        body = self._body()
        if not isinstance(body, dict):
            raise agents.AgentError("Request body must be a JSON object.")
        where = place(str(body.get("place") or SHARED_NAME))
        source = "images tab" if isinstance(where, SharedPlace) else "images tab (agent folder)"
        init_image, init_name = None, ""
        if body.get("init_image") is not None:
            src_place, init_image = image_ref(body["init_image"], "picture to edit")
            images.check_init_image(init_image)
            init_name = f"{src_place.name}:{init_image.relative_to(src_place.workspace.resolve()).as_posix()}"
            source = "image edit"

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
                raise ClientGone() from exc  # stops sd-cli: the user pressed Cancel or left

        try:
            record = images.generate(body, where.workspace, source=source, on_event=emit,
                                     preview=body.get("preview") is True, init_image=init_image, init_name=init_name)
            emit({"image": {**record, "place": where.name}})
            emit({"done": True})
        except ClientGone:
            return
        except Exception as exc:
            message = str(exc) if isinstance(exc, images.ImageError) else f"Unexpected error: {exc}"
            try:
                emit({"error": message})
            except ClientGone:
                return

    def _ocr(self) -> None:
        body = self._body()
        if not isinstance(body, dict):
            raise agents.AgentError("Request body must be a JSON object.")
        src_place, path = image_ref(body.get("image"), "picture to read")
        ocr.check_image(path)
        source = f"{src_place.name}:{path.relative_to(src_place.workspace.resolve()).as_posix()}"

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
                raise ClientGone() from exc   # stops a model run for this picture: the user pressed Cancel or left

        try:
            result = ocr.run(body, path, emit)
            emit({"result": {**result, "source": source}})
            emit({"done": True})
        except ClientGone:
            return
        except Exception as exc:
            known = isinstance(exc, (ocr.OcrError, agents.AgentError)) or getattr(exc, "user_facing", False)
            try:
                emit({"error": str(exc) if known else f"Unexpected error: {exc}"})
            except ClientGone:
                return

    def _chat(self) -> None:
        body = self._body()
        if not isinstance(body, dict):
            raise agents.AgentError("Request body must be a JSON object.")
        agent = agents.load(body["agent"]) if body.get("agent") else None
        messages = attach_images(with_terminal(agents.build_messages(agent, body.get("messages")), body.get("terminal")))

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
            requested = body.get("model") or (agent.model if agent else "") or None
            conn, model = backend.pick(self.server.model_url, requested)
            emit({"model": model})
            runner.reply(conn, model, messages, emit,
                         tool_names=agents.tool_names(agent), workspace=agent.workspace if agent else None,
                         shared=agents.shared_folder(agent), image_preview=body.get("image_preview") is True)
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
