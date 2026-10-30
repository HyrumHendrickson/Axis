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
  DELETE /api/agents/<name>                 delete an agent (shared files remain)
  GET    /api/agents/<name>/files           files in the shared workspace (flat list)
  POST   /api/agents/<name>/open-folder     show the shared workspace in Explorer/Finder
  GET    /api/files/<name>?path=dir         one folder of an agent's folder (the Workspace tab)
                                            (<name> "_shared" means the workspace folder shared by agents,
                                            and "_images" the folder of pictures Axis made)
  GET    /api/files/<name>/view?path=file   a file prepared for preview
  GET    /api/files/<name>/raw?path=file    the file itself (images, downloads)
  POST   /api/files/<name>/reveal?path=x    select the file in Explorer/Finder
  POST   /api/files/<name>/upload?path=dir&name=f   save the request body as a file
                                            (&mkdirs=1 makes the folder first when it is missing)
  POST   /api/files/<name>/mkdir?path=dir   make a folder
  POST   /api/files/<name>/create?path=file make a new, empty file (never replaces a file)
  POST   /api/files/<name>/rename?path=x&to=name   rename a file or folder in place (never replaces anything)
  POST   /api/files/<name>/move?path=x&to=dir   move a file or folder into another folder of the same place
  PUT    /api/files/<name>/save?path=file   replace a text file with the request body (UTF-8 text)
  POST   /api/files/<name>/run?path=file    run a Python, Rust, or terminal program and return its output
  DELETE /api/files/<name>?path=x           delete a file or folder
  GET    /api/skills                        list skills (built-in and custom)
  POST   /api/skills                        create a custom skill
  GET    /api/skills/<id>                   one skill
  PUT    /api/skills/<id>                   update a skill (a built-in skill's name, description and instructions)
  DELETE /api/skills/<id>                   delete a custom skill (and remove it from agents)
  GET    /api/skills/<id>/sends             exactly what the skill sends to the model: system text and actions
  POST   /api/skills/<id>/reset             put a built-in skill back to its default text
  POST   /api/chat                          stream a reply as newline-delimited JSON
                                            (a user message may carry "images": [{place, path}] for
                                            models that read pictures; "terminal": id shows the agent
                                            that terminal's recent commands and output, "terminal_commands": n
                                            how many of them (0 for none, default 5); "workspace":
                                            {place, path, file} shows the agent the Workspace folder the user
                                            is looking at; "folder": a full path keeps the agent in that folder
                                            for this chat instead of the workspace folder)
  GET    /api/workspace                     the workspace folder {folder, default, is_default, missing}
  PUT    /api/workspace                     use another folder as the workspace {folder} ("" for the default)
  GET    /api/folders?path=p                the folders inside p, for choosing a folder {path, parent, folders,
                                            roots, home} (no path: the home folder)
  GET    /api/terminal                      open terminals
  POST   /api/terminal                      open a terminal {place?, path?, shell?} in that folder (default: shared)
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
  GET    /api/images/gallery                pictures Axis made, in ~/Axis/images (newest first)
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
  GET    /api/artcraft                      the Craft apps and Rust: what is installed, and their install jobs
  POST   /api/artcraft/install              install an app, or Rust, in the background {app: photocraft|...|rust}
                                            (cancel it, or clear it from the list, with the /api/manage/jobs routes)
  POST   /api/artcraft/update               update an installed app, or Rust, in the background {app}
  POST   /api/artcraft/remove               delete an installed app (all its files), or Rust {app}
  POST   /api/artcraft/check                ask whether newer versions exist; answers like GET /api/artcraft
  POST   /api/artcraft/open                 start an installed app {app}; it keeps running if Axis stops
  GET    /api/cas                           Maxima and SymPy: what is installed, settings, the default, and jobs
  PUT    /api/cas/settings                  the Maxima path and the CAS agents use {maxima?, default?: maxima|sympy|""}
  POST   /api/cas/install                   install Maxima or SymPy in the background {system: maxima|sympy}
                                            (cancel it, or clear it from the list, with the /api/manage/jobs routes)
  POST   /api/cas/remove                    delete the SymPy environment {system: sympy}
  POST   /api/cas/run                       run code in the workspace folder {system?, code}; answers
                                            {system, exit, stdout, stderr, timed_out, question}
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import __version__, agents, artcraft, backend, cas, catalog, chats, engines, hub, images, jobs, latex, manager, ocr, procs, runner, skills, terminal, tools, updates
from .paths import FolderError, check_work_folder, images_folder, remove_tree, set_workspace_root, workspace_root, workspace_setting

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


# The Workspace tab's names for the workspace folder and the pictures folder. They can never clash
# with an agent, because agent names must start with a letter or number.
SHARED_NAME = "_shared"
IMAGES_NAME = "_images"


class SharedPlace:
    """Stands in for an agent in the file routes so the workspace folder can be browsed."""

    name = SHARED_NAME

    @property
    def workspace(self) -> Path:
        return workspace_root()


class ImagesPlace:
    """The folder where every picture Axis makes is saved (~/Axis/images)."""

    name = IMAGES_NAME

    @property
    def workspace(self) -> Path:
        return images_folder()


Place = Union[agents.Agent, SharedPlace, ImagesPlace]


def place(name: str) -> Place:
    if name == SHARED_NAME:
        workspace_root().mkdir(parents=True, exist_ok=True)
        return SharedPlace()
    if name == IMAGES_NAME:
        images_folder().mkdir(parents=True, exist_ok=True)
        return ImagesPlace()
    return agents.load(name)


def _owner(agent: Place) -> str:
    if isinstance(agent, SharedPlace):
        return "the workspace folder"
    if isinstance(agent, ImagesPlace):
        return "the images folder"
    return f"{agent.name}'s folder"


MAX_FOLDERS = 2000   # folders listed by /api/folders


def _roots() -> List[str]:
    """The tops of the file systems: drive letters on Windows, / elsewhere."""
    if os.name != "nt":
        return ["/"]
    import string
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]


def folder_choices(raw: str) -> Dict[str, Any]:
    """The folders inside one folder of the computer, so the page can let the user choose one.

    Only names of folders are listed, never files. This shows the user their own computer; agents
    never call it.
    """
    home = Path.home()
    try:
        folder = Path(raw).expanduser().resolve() if raw else home
    except OSError as exc:
        raise agents.AgentError(f"That folder cannot be opened: {exc.strerror or exc}") from exc
    if not folder.is_absolute():
        raise agents.AgentError("Give the folder's full path.")
    if not folder.is_dir():
        raise agents.AgentError(f"Folder '{raw}' does not exist.")
    found: List[Dict[str, str]] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if entry.is_dir() and not entry.name.startswith("."):
                        found.append({"name": entry.name, "path": str(folder / entry.name)})
                except OSError:
                    continue
    except OSError as exc:
        raise agents.AgentError(f"Axis cannot look inside '{folder}' ({exc.strerror or exc}).") from exc
    found.sort(key=lambda e: e["name"].lower())
    usable = True
    try:
        check_work_folder(str(folder))
    except FolderError:
        usable = False
    return {"path": str(folder), "parent": "" if folder.parent == folder else str(folder.parent),
            "folders": found[:MAX_FOLDERS], "more": max(0, len(found) - MAX_FOLDERS), "roots": _roots(),
            "home": str(home), "usable": usable}


MAX_NAME_BYTES = 200   # leaves room for " (2)" and a temporary prefix inside the 255-byte limit of most file systems
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def bad_file_name(name: str, windows: Optional[bool] = None) -> Optional[str]:
    """Why a file name cannot be used for an upload (None when it is fine).

    On Windows a name such as nul.txt or con opens a device instead of making a file, and a name that
    ends in a dot or a space is silently renamed, so those are refused there.
    """
    if not name or name in (".", "..") or any(c in name for c in '/\\:*?"<>|\0') or any(ord(c) < 32 for c in name):
        return "That file name cannot be used."
    if name in HIDDEN:
        return "That file name cannot be used."
    if len(name.encode("utf-8", "replace")) > MAX_NAME_BYTES:
        return "That file name is too long."
    if os.name == "nt" if windows is None else windows:
        if name.split(".")[0].strip().lower() in _WINDOWS_DEVICES or name[-1] in ". ":
            return "Windows cannot use that file name."
    return None


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

    def server_bind(self) -> None:
        # HTTPServer.server_bind calls socket.getfqdn(), a reverse-DNS lookup that can stall for many seconds
        # on macOS (and some networks). Axis only listens on 127.0.0.1 and never uses the name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]

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

    def handle_error(self, request, client_address) -> None:
        """A browser that closes a connection is routine, not an error worth a traceback."""
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


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
        remove_tree(target)
        if target.exists():
            raise agents.AgentError(f"Some files in '{rel}' could not be deleted. Close any that are open in other programs.")
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


def create_file(agent: "Place", rel: str) -> str:
    """Make a new, empty file in a folder that exists. Never replaces a file or folder. Returns its path."""
    agent.workspace.mkdir(parents=True, exist_ok=True)
    parent, _, name = str(rel).replace("\\", "/").rpartition("/")
    why = bad_file_name(name)
    if why:
        raise agents.AgentError(why)
    folder = folder_path(agent, parent)
    if any(part in HIDDEN for part in folder.relative_to(agent.workspace.resolve()).parts):
        raise agents.AgentError("Files cannot be made in that folder.")
    target = folder / name
    try:
        os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))   # exclusive: never replaces
    except FileExistsError:
        raise agents.AgentError(f"'{name}' already exists in that folder.") from None
    except OSError as exc:
        raise agents.AgentError(f"Could not make the file ({exc.strerror or exc}).") from exc
    return target.relative_to(agent.workspace.resolve()).as_posix()


def rename_path(agent: "Place", rel: str, new_name: Any) -> str:
    """Give a file or folder a new name, keeping it in the same folder. Never replaces anything.

    The new name is a plain name, not a path. On a file system that ignores case (Windows, macOS),
    changing only the capital letters is allowed. A link is renamed itself, not what it points to.
    Returns the new path.
    """
    if not isinstance(new_name, str):
        raise agents.AgentError("Give the new name as text.")
    new_name = new_name.strip()
    why = bad_file_name(new_name)
    if why:
        raise agents.AgentError(why.replace("file name", "name"))
    parent, _, old_name = str(rel).replace("\\", "/").rstrip("/").rpartition("/")
    if old_name in ("", ".", "..") or old_name in HIDDEN:
        raise agents.AgentError("Choose a file or folder to rename.")
    folder = folder_path(agent, parent)
    source, target = folder / old_name, folder / new_name
    if not os.path.lexists(source):
        raise agents.AgentError(f"'{rel}' does not exist in {_owner(agent)}.")
    if new_name != old_name:
        only_case = new_name.lower() == old_name.lower() and os.path.exists(target) and os.path.samefile(source, target)
        if os.path.lexists(target) and not only_case:
            raise agents.AgentError(f"'{new_name}' already exists in that folder.")
        try:
            os.rename(source, target)
        except OSError as exc:
            raise agents.AgentError(f"Could not rename it ({exc.strerror or exc}). "
                                    "Close any program that has it open and try again.") from exc
    return target.relative_to(agent.workspace.resolve()).as_posix()


def move_path(agent: "Place", rel: str, to_dir: str) -> str:
    """Move a file or folder into another folder of the same agent (or the shared folder).

    Both paths stay inside the folder. Nothing is ever overwritten, and a folder cannot be
    moved into itself. Returns the new path.
    """
    ws = agent.workspace
    try:
        source = tools.resolve(ws, rel)
        dest = tools.resolve(ws, to_dir, allow_root=True)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not (source.exists() or source.is_symlink()):
        raise agents.AgentError(f"'{rel}' does not exist in {_owner(agent)}.")
    if not dest.is_dir():
        raise agents.AgentError(f"Folder '{to_dir}' does not exist in {_owner(agent)}.")
    if any(part in HIDDEN for part in dest.relative_to(ws.resolve()).parts):
        raise agents.AgentError("Files cannot be moved into that folder.")
    if source.is_dir() and not source.is_symlink() and (dest == source or source in dest.parents):
        raise agents.AgentError("A folder cannot be moved into itself.")
    target = dest / source.name
    if target == source:
        return source.relative_to(ws.resolve()).as_posix()   # already there
    if target.exists() or target.is_symlink():
        raise agents.AgentError(f"'{source.name}' already exists in that folder.")
    shutil.move(str(source), str(target))
    return target.relative_to(ws.resolve()).as_posix()


def _reserve_name(folder: Path, name: str) -> Path:
    """Create an empty file for a new upload and return it: report.txt, then report (2).txt, and so on.

    The file is created exclusively, so two uploads of the same name (or a file that appears while this
    runs) can never end up with the same path.
    """
    stem, dot, ext = name.rpartition(".") if "." in name.lstrip(".") else (name, "", "")
    n = 1
    while True:
        candidate = folder / (name if n == 1 else f"{stem} ({n}){dot}{ext}")
        try:
            os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))
            return candidate
        except FileExistsError:
            n += 1


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
    # A fresh, exclusively created temporary name: a link planted at a guessable name cannot redirect the write.
    tmp = path.with_name(f".axis-save-{uuid.uuid4().hex}")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    st = path.stat()
    return {"path": path.relative_to(agent.workspace.resolve()).as_posix(), "size": st.st_size, "modified": _when(st.st_mtime)}


def run_script(agent: "Place", rel: str) -> Dict[str, Any]:
    """Run a Workspace program with the action matching its file type."""
    shared = agents.shared_folder(agent) if isinstance(agent, agents.Agent) else None
    started = time.monotonic()
    try:
        where = tools.Places(agent.workspace, shared)
        path = tools.locate(where, rel)[1]
        ext = path.suffix.lower()
        if ext in (".py", ".pyw"):
            result = tools.run_file(where, rel)
        elif ext == ".rs":
            result = tools.run_rust_file_result(where, rel)
        elif ext in (".sh", ".bash", ".ps1", ".bat", ".cmd", ".zsh", ".fish"):
            result = tools.run_terminal_file_result(where, rel)
        else:
            raise agents.AgentError("Only Python, Rust, bash/sh, PowerShell, and cmd program files can be run here.")
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    return {**result, "path": rel, "seconds": round(time.monotonic() - started, 2),
            "timeout": tools.PYTHON_TIMEOUT if ext in (".py", ".pyw") else tools.RUST_TIMEOUT if ext == ".rs" else tools.TERMINAL_TIMEOUT}


def folder_path(agent: "Place", rel: str) -> Path:
    agent.workspace.mkdir(parents=True, exist_ok=True)
    try:
        folder = tools.resolve(agent.workspace, rel, allow_root=True)
    except tools.ToolError as exc:
        raise agents.AgentError(str(exc)) from exc
    if not folder.is_dir():
        raise agents.AgentError(f"Folder '{rel}' does not exist in {_owner(agent)}.")
    return folder


DEFAULT_TERMINAL_COMMANDS = 5


def terminal_commands(value: Any) -> int:
    """How many of the newest terminal commands an agent is shown (0 to terminal.MAX_COMMANDS)."""
    if value is None or value == "":
        return DEFAULT_TERMINAL_COMMANDS
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise agents.AgentError("terminal_commands must be a whole number.") from None
    return max(0, min(n, terminal.MAX_COMMANDS))


def with_terminal(messages: List[Dict[str, Any]], sid: Any, commands: int = DEFAULT_TERMINAL_COMMANDS) -> List[Dict[str, Any]]:
    """Show the agent the newest commands of a terminal and their output, added to the newest message.

    commands is how many of the commands typed into it (counting back from the newest) are shown,
    each with what it printed. 0 shows nothing.
    """
    if not sid or commands <= 0:
        return messages
    try:
        session = terminal.get(str(sid))
    except terminal.TerminalError:
        return messages
    shot, shown = session.recent(commands)
    shot = shot.strip("\n") or "(nothing yet)"
    if shown:
        what = f"The last {shown} command{'' if shown == 1 else 's'} typed into it and their output"
    else:
        what = "Its output (no commands have been typed into it yet)"
    note = (f"\n\n---\nThe user's terminal ({session.shell}, started in {session.cwd}). "
            f"{what}, which you can see but not type into:\n```\n{shot}\n```")
    last = messages[-1]
    return messages[:-1] + [{**last, "content": last["content"] + note}]


WORKSPACE_LIST_LIMIT = 40   # entries of the folder an agent is shown


def _size_text(n: Optional[int]) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def with_workspace(messages: List[Dict[str, Any]], ctx: Any, agent: Optional[agents.Agent] = None) -> List[Dict[str, Any]]:
    """Show the agent the Workspace folder the user is looking at, added to the newest message.

    ctx is {place, path, file} from the page. Only names, sizes and item counts are shown, never file
    contents. A folder that does not exist, or a path outside the folder, is left out without an error.
    """
    if not isinstance(ctx, dict) or not ctx.get("place"):
        return messages
    try:
        where = place(str(ctx["place"]))
        listing = folder_listing(where, str(ctx.get("path") or ""))
    except (agents.AgentError, tools.ToolError):
        return messages
    if isinstance(where, ImagesPlace):
        return messages
    shared = isinstance(where, SharedPlace)
    own = agent is not None and not shared and where.name == agent.name
    has_shared = shared and agent is not None and skills.SHARED_SKILL in agent.skills
    path = listing["path"]
    if shared:
        owner = "the folder shared by agents"
    elif own:
        owner = "your own folder"
    else:
        owner = f"the folder of the agent '{where.name}'"
    lines = ["---", f"The user is looking at a folder in the Axis Workspace: {path or 'the top level'} of {owner}."]
    if own:
        lines.append(f"Your file actions reach it as '{path or '.'}'.")
    elif shared and has_shared:
        lines.append(f"Your file actions reach it as 'shared/{path}'." if path else "Your file actions reach it as 'shared'.")
    else:
        lines.append("You cannot reach it with your own actions; this list is only so you know what they are looking at.")
    entries = listing["entries"]
    if entries:
        lines.append("It contains:")
        for e in entries[:WORKSPACE_LIST_LIMIT]:
            if e["type"] == "dir":
                count = e["items"]
                lines.append(f"- {e['name']}/" + (f" ({count} item{'' if count == 1 else 's'})" if count is not None else ""))
            else:
                lines.append(f"- {e['name']} ({_size_text(e['size'])})")
        if len(entries) > WORKSPACE_LIST_LIMIT:
            lines.append(f"- ... and {len(entries) - WORKSPACE_LIST_LIMIT} more")
    else:
        lines.append("It is empty.")
    opened = str(ctx.get("file") or "").replace("\n", " ").strip()[:200]
    if opened:
        lines.append(f"The file open in the editor: {opened}")
    last = messages[-1]
    return messages[:-1] + [{**last, "content": last["content"] + "\n\n" + "\n".join(lines)}]


def chat_folder(value: Any) -> Optional[Path]:
    """The folder one chat keeps its agent in, or None for the workspace folder."""
    if value in (None, ""):
        return None
    try:
        folder = check_work_folder(value)
    except FolderError as exc:
        raise agents.AgentError(f"The chat's folder cannot be used. {exc}") from exc
    return None if folder == workspace_root() else folder


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
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True   # the browser went away (or was refused mid-upload); nothing left to tell it

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
                ocr.OcrError, terminal.TerminalError, artcraft.ArtCraftError, cas.CasError, latex.LatexError) as exc:
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
                # No other site may show Axis inside a frame and trick a click (clickjacking).
                return self._send(200, page(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "frame-ancestors 'none'", "X-Frame-Options": "DENY"})
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
        elif parts[1:] == ["workspace"]:
            try:
                if method == "GET":
                    return self._json(200, workspace_setting())
                if method == "PUT":
                    body = self._body()
                    return self._json(200, set_workspace_root(body.get("folder") if isinstance(body, dict) else None))
            except FolderError as exc:
                raise agents.AgentError(str(exc)) from exc
        elif parts[1:] == ["folders"] and method == "GET":
            return self._json(200, folder_choices(param("path")))

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
            if action == "create" and method == "POST":
                return self._json(201, {"created": create_file(agent, param("path"))})
            if action == "rename" and method == "POST":
                return self._json(200, {"renamed": rename_path(agent, param("path"), param("to"))})
            if action == "move" and method == "POST":
                return self._json(200, {"moved": move_path(agent, param("path"), param("to"))})
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
                label = rel.rsplit("/", 1)[-1] if rel else (where.workspace.name if isinstance(where, SharedPlace) else
                                                             "images" if isinstance(where, ImagesPlace) else where.name)
                return self._json(201, terminal.start(folder, label, str(body.get("shell") or "")).public())
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
                return self._json(200, {"images": images.gallery(images_folder())})
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

        elif n >= 2 and parts[1] == "artcraft":
            if n == 2 and method == "GET":
                return self._json(200, artcraft.status())
            if parts[2:] == ["install"] and method == "POST":
                return self._json(202, artcraft.install(self._body()))
            if parts[2:] == ["update"] and method == "POST":
                return self._json(202, artcraft.install(self._body(), update=True))
            if parts[2:] == ["remove"] and method == "POST":
                return self._json(200, artcraft.remove(self._body()))
            if parts[2:] == ["check"] and method == "POST":
                return self._json(200, artcraft.check_updates())
            if parts[2:] == ["open"] and method == "POST":
                return self._json(200, artcraft.open_app(self._body()))

        elif n >= 2 and parts[1] == "cas":
            if n == 2 and method == "GET":
                return self._json(200, cas.status())
            if parts[2:] == ["settings"] and method == "PUT":
                return self._json(200, cas.save_settings(self._body()))
            if parts[2:] == ["install"] and method == "POST":
                return self._json(202, cas.install(self._body()))
            if parts[2:] == ["remove"] and method == "POST":
                return self._json(200, cas.remove(self._body()))
            if parts[2:] == ["run"] and method == "POST":
                body = self._body()
                if not isinstance(body, dict):
                    raise cas.CasError("Request body must be a JSON object.")
                root = workspace_root()
                root.mkdir(parents=True, exist_ok=True)
                # The user's own code, like the Workspace Run button: not checked the way an agent's is.
                return self._json(200, cas.run(tools.Places(root), body.get("system"), body.get("code"), checked=False))

        elif n >= 2 and parts[1] == "latex":
            if n == 2 and method == "GET":
                return self._json(200, latex.status())
            if parts[2:] == ["install"] and method == "POST":
                return self._json(200, latex.install())
            if parts[2:] == ["enabled"] and method == "PUT":
                body = self._body()
                return self._json(200, latex.set_enabled(body.get("enabled") if isinstance(body, dict) else False))
            if parts[2:] == ["script"] and method == "GET":
                path = latex.script_path()
                if path is None:
                    return self._error(404, "The LaTeX renderer is not downloaded.")
                return self._send(200, path.read_bytes(), "application/javascript; charset=utf-8")
            if n == 2 and method == "DELETE":
                return self._json(200, latex.remove())

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
            if n == 4 and parts[3] == "sends" and method == "GET":
                return self._json(200, agents.skill_preview(parts[2]))
            if n == 4 and parts[3] == "reset" and method == "POST":
                return self._json(200, skills.reset(parts[2]))
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
        problem = bad_file_name(name)
        if problem:
            raise agents.AgentError(problem)
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
        target = _reserve_name(folder, name)   # claims the name atomically: nothing is ever overwritten
        tmp = folder / f".axis-upload-{uuid.uuid4().hex}"
        remaining = length
        try:
            with open(tmp, "xb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 256 * 1024))
                    if not chunk:
                        raise agents.AgentError("The upload was interrupted.")
                    f.write(chunk)
                    remaining -= len(chunk)
            os.replace(tmp, target)   # replaces only our own empty placeholder
        except BaseException:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
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
        source = "images tab"
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
            record = images.generate(body, images_folder(), source=source, on_event=emit,
                                     preview=body.get("preview") is True, init_image=init_image, init_name=init_name)
            emit({"image": {**record, "place": IMAGES_NAME}})
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
        folder = chat_folder(body.get("folder")) if agent else None
        commands = terminal_commands(body.get("terminal_commands"))
        messages = agents.build_messages(agent, body.get("messages"), nested=bool(body.get("nested_agent")))
        if body.get("terminal_tool") and agent:
            messages[-1] = {**messages[-1], "content": messages[-1]["content"] +
                            "\n\nThe user temporarily granted you the run_terminal tool for this conversation. "
                            "Use it only when needed and explain what you ran."}
        if folder is not None:
            messages[-1] = {**messages[-1], "content": messages[-1]["content"] +
                            f"\n\nFor this chat your folder is {folder}. Every action you take works inside it, "
                            "and you cannot leave it."}
        # With a folder of its own, the agent cannot reach the Workspace folder, and with_workspace says so.
        messages = attach_images(with_workspace(with_terminal(messages, body.get("terminal"), commands),
                                                body.get("workspace"), agent if folder is None else None))

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
            allowed = agents.tool_names(agent)
            if body.get("terminal_tool") and agent and "run_terminal" not in allowed:
                allowed = allowed + ["run_terminal"]
            execution_workspace = agent.workspace if agent else None
            execution_shared = agents.shared_folder(agent)
            if folder is not None:   # the chat's own folder: the agent is kept inside it alone
                execution_workspace = folder
                execution_shared = None
            def call_nested(agent_name: str, request: str) -> str:
                child = agents.load(agent_name)
                child_messages = agents.build_messages(child, [{"role": "user", "content": request}], nested=True)
                child_allowed = agents.tool_names(child, include_agent_calls=False)
                result_parts: List[str] = []
                def child_emit(event: Dict[str, Any]) -> None:
                    if event.get("t"):
                        result_parts.append(str(event["t"]))
                runner.reply(conn, model, child_messages, child_emit, tool_names=child_allowed,
                             workspace=child.workspace, shared=agents.shared_folder(child))
                return "".join(result_parts).strip() or "The called agent returned no text."

            runner.reply(conn, model, messages, emit,
                         tool_names=allowed, workspace=execution_workspace,
                         shared=execution_shared, image_preview=body.get("image_preview") is True,
                         agent_call=call_nested if "call_agent" in allowed else None)
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
