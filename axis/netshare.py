"""Sharing models between computers that run Axis.

One computer can **host**: it opens a port, and other computers that **connect** to it can use its running chat
models and image models. A connected computer keeps everything else to itself: its system prompts, skills, agents,
actions, files, chats and settings stay where they are, and only the model is borrowed. A chat is sent to the host
as plain messages (with the context size this computer asks for), and the host's own Ollama or llama.cpp answers
it. A picture is made from the prompt and settings this computer sends, and comes back as a PNG that is saved in
this computer's own images folder.

Hosting
-------
``start_hosting`` opens a second listener, separate from the server that shows the Axis page, so the page's own
check (``Handler._trusted`` in server.py) is not touched. It is off until the user presses **Start sharing**, off
again after every restart, and it is closed when Axis quits. It answers only these requests, and nothing else:

    GET  /share/v1/ping      identifies Axis sharing its models
    GET  /share/v1/models    the chat models that are loaded and the image models that are on (names and defaults,
                             never file paths)
    POST /share/v1/chat      run one chat turn on a loaded model; streams newline-delimited JSON
    POST /share/v1/image     make one picture on an image model that is on; streams newline-delimited JSON

Every request must carry the header ``X-Axis-Share: 1``. A web page open in a browser cannot add it to a request to
another site without the host's permission, which the host never gives, so other sites cannot use the port.

Many computers may use a host at once. Chats run side by side (the host's own engine decides how many really run
in parallel: Ollama's ``OLLAMA_NUM_PARALLEL``, llama.cpp's ``--parallel``); a chat request over the limit
(AXIS_SHARE_CHATS, 64 by default) is refused with a plain message. Pictures are made one at a time, because they
compete for the same graphics memory, and wait in a first-come, first-served line; each waiting computer is told
how many pictures are ahead of it. The host makes each picture in a temporary folder and deletes it once it has
been sent, so nothing a visitor makes is kept on the host.

Connecting
----------
``connect`` takes the host's address and port, checks them, and keeps looking at the host every few seconds in the
background. While connected, the host's models are offered to backend.py (chat) and images.py (pictures) next to the
local ones, named "name \u00b7 address:port". Nothing here blocks the page: an unreachable host just shows as
unreachable and its models disappear until it answers again.

Stored in ~/Axis:

    net-settings.json          the host's address, whether to stay connected, the port last used for hosting
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import socketserver
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__, backend, images
from .paths import axis_home

DEFAULT_PORT = 8800
PREFIX = "/share/v1"
HEADER = "X-Axis-Share"
MAX_BODY = 96 * 1024 * 1024          # a chat with pictures, or a picture to edit
MAX_MESSAGES = 4000
MAX_CONTEXT = 1 << 20
POLL_SECONDS = 4.0                    # how often a connected computer looks at its host
PROBE_TIMEOUT = 3.0
CLIENT_KEEP = 300.0                   # how long the host remembers a computer that has gone quiet
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PARAMS = ("prompt", "negative_prompt", "width", "height", "steps", "cfg_scale", "sampler", "seed", "strength")


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


CHAT_LIMIT = _limit("AXIS_SHARE_CHATS", 64)      # chats the host runs for others at the same moment
QUEUE_LIMIT = _limit("AXIS_SHARE_QUEUE", 32)     # pictures that may wait in line

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ShareError(Exception):
    """A problem with sharing the user can fix; the message is shown as-is."""

    user_facing = True


class _Gone(Exception):
    """The other computer closed the connection (it pressed Stop or Cancel, or went away)."""


# ----------------------------------------------------------------------- settings


def _settings_path() -> Path:
    return axis_home() / "net-settings.json"


def load_settings() -> Dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    port = data.get("host_port")
    return {"address": str(data.get("address") or ""), "connected": data.get("connected") is True,
            "host_port": port if isinstance(port, int) and 0 < port < 65536 else DEFAULT_PORT}


def _save_settings(**changes: Any) -> None:
    data = {**load_settings(), **changes}
    try:
        _settings_path().parent.mkdir(parents=True, exist_ok=True)
        _settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass   # the settings are a convenience; sharing still works without them


def parse_address(text: Any) -> Tuple[str, int, str]:
    """(host, port, "host:port") from what the user typed: 192.168.1.5:8800, http://pc:8800 or just 192.168.1.5."""
    raw = str(text or "").strip()
    if not raw:
        raise ShareError("Type the address of the other computer, for example 192.168.1.5:8800.")
    raw = raw.split("://", 1)[1] if "://" in raw else raw
    raw = raw.split("/", 1)[0]
    try:
        parts = urllib.parse.urlsplit("//" + raw)
        host, port = parts.hostname, parts.port
    except ValueError:
        host, port = None, None
    if not host or any(c.isspace() or c in "\\@?#" for c in raw):
        raise ShareError(f"'{raw}' is not an address. Use the form 192.168.1.5:8800.")
    port = port or DEFAULT_PORT
    shown = f"[{host}]" if ":" in host else host
    return host, port, f"{shown}:{port}"


# ------------------------------------------------------------------------- what a host offers


def chat_models(model_url: Optional[str] = None) -> List[Dict[str, str]]:
    """The chat models loaded on this computer, with the kind of server each runs on."""
    try:
        conns = backend.connect_all(model_url, remote=False)   # never someone else's models
    except backend.BackendError:
        return []
    return [{"name": m, "kind": c.kind} for c in conns for m in c.models]


def image_models() -> List[Dict[str, Any]]:
    """The image models that are on (started on the Models page): names and defaults only."""
    from . import manager
    out: List[Dict[str, Any]] = []
    try:
        models = images.list_models()
    except Exception:
        return out
    for m in models:
        if m.get("missing") or not manager.image_server_url(m["id"]):
            continue
        out.append({k: m.get(k) for k in ("id", "name", "description", "width", "height", "steps", "cfg_scale", "sampler")})
    return out


def addresses() -> List[str]:
    """This computer's network addresses (never the loopback one), the ones other computers can try."""
    found: List[str] = []

    def add(ip: str) -> None:
        if ip and not ip.startswith(("127.", "169.254.", "0.")) and ip not in found:
            found.append(ip)

    try:   # the address used to reach the network; a UDP "connect" sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 9))
            add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(info[4][0]))
    except OSError:
        pass
    return found


# ---------------------------------------------------------------------------- the line for pictures


class _Line:
    """Pictures are made one at a time. Computers wait in a first-come, first-served line."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.cv = threading.Condition()
        self.waiting: List[object] = []
        self.busy = False

    def length(self) -> int:
        with self.cv:
            return len(self.waiting) + (1 if self.busy else 0)

    def full(self) -> bool:
        with self.cv:
            return len(self.waiting) >= self.limit

    def enter(self, emit: Callable[[dict], None]) -> None:
        """Wait for a turn; emit({"queued": n}) every second says how many pictures are ahead. Raises when emit does."""
        me = object()
        with self.cv:
            self.waiting.append(me)
        try:
            while True:
                with self.cv:
                    if self.waiting[0] is me and not self.busy:
                        self.waiting.pop(0)
                        self.busy = True
                        return
                    ahead = self.waiting.index(me) + (1 if self.busy else 0)
                    self.cv.wait(timeout=1.0)
                emit({"queued": ahead})
        except BaseException:
            with self.cv:
                if me in self.waiting:
                    self.waiting.remove(me)
                self.cv.notify_all()
            raise

    def leave(self) -> None:
        with self.cv:
            self.busy = False
            self.cv.notify_all()


# --------------------------------------------------------------------------------- the host


class Host:
    """The state of one open sharing port."""

    def __init__(self, httpd: "_ShareServer", model_url: Optional[str]) -> None:
        self.httpd = httpd
        self.port: int = httpd.server_address[1]
        self.model_url = model_url
        self.started = time.time()
        self.chat_slots = threading.BoundedSemaphore(CHAT_LIMIT)
        self.line = _Line(QUEUE_LIMIT)
        self.lock = threading.Lock()
        self.clients: Dict[str, Dict[str, Any]] = {}
        self.served = {"chat": 0, "image": 0}
        self.connections: set = set()

    def begin(self, ip: str, kind: str) -> None:
        with self.lock:
            c = self.clients.setdefault(ip, {"chat": 0, "image": 0, "requests": 0, "last": 0.0})
            c[kind] += 1
            c["requests"] += 1
            c["last"] = time.time()

    def end(self, ip: str, kind: str, ok: bool) -> None:
        with self.lock:
            c = self.clients.setdefault(ip, {"chat": 0, "image": 0, "requests": 0, "last": 0.0})
            c[kind] = max(0, c[kind] - 1)
            c["last"] = time.time()
            if ok:
                self.served[kind] += 1

    def touch(self, ip: str) -> None:
        with self.lock:
            self.clients.setdefault(ip, {"chat": 0, "image": 0, "requests": 0, "last": 0.0})["last"] = time.time()

    def status(self) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            for ip in [i for i, c in self.clients.items() if now - c["last"] > CLIENT_KEEP and not c["chat"] and not c["image"]]:
                del self.clients[ip]
            clients = [{"address": ip, "chats": c["chat"], "images": c["image"], "requests": c["requests"],
                        "seconds_ago": round(now - c["last"])} for ip, c in sorted(self.clients.items())]
            served = dict(self.served)
        return {"clients": clients, "active_chats": sum(c["chats"] for c in clients),
                "active_images": sum(c["images"] for c in clients), "waiting_images": max(0, self.line.length() - 1),
                "served": served}


class _ShareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    request_queue_size = 64
    share: Host

    def server_bind(self) -> None:
        # Like AxisServer: HTTPServer.server_bind asks DNS for a name, which can stall for many seconds.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]


class ShareHandler(BaseHTTPRequestHandler):
    server_version = "AxisShare"
    protocol_version = "HTTP/1.0"
    timeout = 60

    def log_message(self, *args: Any) -> None:
        pass

    def setup(self) -> None:
        super().setup()
        self.server.share.connections.add(self.connection)   # type: ignore[attr-defined]

    def finish(self) -> None:
        self.server.share.connections.discard(self.connection)   # type: ignore[attr-defined]
        try:
            super().finish()
        except OSError:
            pass

    # -- plumbing

    @property
    def share(self) -> Host:
        return self.server.share   # type: ignore[attr-defined]

    def _ip(self) -> str:
        return str(self.client_address[0])

    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self) -> bool:
        if self.headers.get(HEADER) != "1":
            self._json(403, {"error": "This is the port another Axis uses to borrow models. Connect to it from Axis, on the Models page."})
            return False
        return True

    def _body(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            self._json(413 if length > MAX_BODY else 400, {"error": "The request is empty or too large."})
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            self._json(400, {"error": "The request is not valid JSON."})
            return None
        if not isinstance(body, dict):
            self._json(400, {"error": "The request must be a JSON object."})
            return None
        return body

    def _stream(self) -> Callable[[dict], None]:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except (ConnectionError, OSError) as exc:
            raise _Gone() from exc
        self.close_connection = True

        def emit(event: dict) -> None:
            try:
                self.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionError, OSError, ValueError) as exc:
                raise _Gone() from exc
        return emit

    # -- routes

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] not in (PREFIX + "/ping", PREFIX + "/models"):
            return self._json(404, {"error": "Not found."})
        if not self._allowed():
            return
        self.share.touch(self._ip())
        if self.path.split("?", 1)[0].endswith("/ping"):
            return self._json(200, {"axis_share": True, "version": __version__})
        return self._json(200, {"version": __version__, "chat": chat_models(self.share.model_url), "image": image_models()})

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]
        if route not in (PREFIX + "/chat", PREFIX + "/image"):
            return self._json(404, {"error": "Not found."})
        if not self._allowed():
            return
        body = self._body()
        if body is None:
            return
        return self._chat(body) if route.endswith("/chat") else self._image(body)

    def _chat(self, body: dict) -> None:
        model, messages, tools = body.get("model"), body.get("messages"), body.get("tools")
        if not isinstance(model, str) or not model or not isinstance(messages, list) or not messages \
                or len(messages) > MAX_MESSAGES or not all(isinstance(m, dict) for m in messages):
            return self._json(400, {"error": "A chat needs a model and a list of messages."})
        if tools is not None and not isinstance(tools, list):
            return self._json(400, {"error": "The tools must be a list."})
        temp = body.get("temperature")
        temp = float(temp) if isinstance(temp, (int, float)) and not isinstance(temp, bool) and 0 <= temp <= 2 else None
        ctx = body.get("num_ctx")
        ctx = ctx if isinstance(ctx, int) and not isinstance(ctx, bool) and 0 < ctx <= MAX_CONTEXT else None
        if not self.share.chat_slots.acquire(blocking=False):
            return self._json(503, {"error": f"The host is already running {CHAT_LIMIT} chats for other computers. Try again in a moment."})
        ip, ok = self._ip(), False
        self.share.begin(ip, "chat")
        try:
            try:
                emit = self._stream()
                conn, name = backend.pick(self.share.model_url, model, remote=False)
                turn = backend.complete(conn, name, messages, lambda t: emit({"t": t}), tools=tools or None,
                                        temperature=temp, wired=True, num_ctx=ctx)
                emit({"turn": {"tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments, "raw": c.raw}
                                              for c in turn.tool_calls], "stats": turn.stats}})
                ok = True
            except _Gone:
                return
            except Exception as exc:
                kinds = ((backend.ToolsUnsupported, "tools_unsupported"), (backend.ImagesUnsupported, "images_unsupported"),
                         (backend.ServerUnavailable, "unavailable"))
                kind = next((k for cls, k in kinds if isinstance(exc, cls)), "error")
                text = str(exc) if isinstance(exc, backend.BackendError) else "The host could not run the chat."
                try:
                    emit({"error": text, "type": kind})
                except _Gone:
                    return
        finally:
            self.share.end(ip, "chat", ok)
            self.share.chat_slots.release()

    def _image(self, body: dict) -> None:
        wanted = body.get("model")
        if not isinstance(wanted, str) or wanted not in {m["id"] for m in image_models()}:
            return self._json(400, {"error": "That image model is not on at the host. The host starts it on the Models page."})
        if self.share.line.full():
            return self._json(503, {"error": f"{QUEUE_LIMIT} pictures are already waiting at the host. Try again in a moment."})
        raw: Dict[str, Any] = {k: body[k] for k in PARAMS if k in body}
        raw["model"] = wanted
        ip, ok = self._ip(), False
        self.share.begin(ip, "image")
        tmp = Path(tempfile.mkdtemp(prefix="axis-share-"))
        try:
            def forward(event: dict) -> None:
                if "log" not in event:   # the engine's own log lines can name the host's folders
                    emit(event)

            try:
                emit = self._stream()
                init: Optional[Path] = None
                if isinstance(body.get("init_image"), str):
                    ext = str(body.get("init_ext") or ".png").lower()
                    ext = ext if ext in images.INIT_TYPES else ".png"
                    try:
                        data = base64.b64decode(body["init_image"], validate=True)
                    except ValueError as exc:
                        raise images.ImageError("The picture to edit could not be read.") from exc
                    if len(data) > images.MAX_INIT_BYTES:
                        raise images.ImageError("The picture to edit is too large.")
                    init = tmp / ("source" + ext)
                    init.write_bytes(data)
                self.share.line.enter(emit)
                try:
                    record = images.generate(raw, tmp / "out", source="network", on_event=forward,
                                             preview=body.get("preview") is True, init_image=init)
                    png = (tmp / "out" / record["path"]).read_bytes()
                finally:
                    self.share.line.leave()
                keep = ("prompt", "negative_prompt", "width", "height", "steps", "cfg_scale", "sampler", "seed")
                emit({"image": {"png": base64.b64encode(png).decode("ascii"), "seconds": record.get("seconds"),
                                "params": {k: record.get(k) for k in keep}}})
                emit({"done": True})
                ok = True
            except _Gone:
                return
            except Exception as exc:
                text = str(exc) if getattr(exc, "user_facing", False) else "The host could not make the picture."
                try:
                    emit({"error": text})
                except _Gone:
                    return
        finally:
            self.share.end(ip, "image", ok)
            shutil.rmtree(tmp, ignore_errors=True)


_LOCK = threading.RLock()
_HOST: Optional[Host] = None


def start_hosting(port: int, model_url: Optional[str] = None, avoid: int = 0) -> Dict[str, Any]:
    """Open the sharing port (0 picks a free one). avoid is the port of the Axis page itself."""
    global _HOST
    if not isinstance(port, int) or isinstance(port, bool) or not (port == 0 or 1024 <= port <= 65535):
        raise ShareError("Choose a port between 1024 and 65535.")
    if port and port == avoid:
        raise ShareError(f"Port {port} is the one the Axis page uses. Choose another.")
    with _LOCK:
        if _HOST is not None:
            raise ShareError("Sharing is already on.")
        try:
            httpd = _ShareServer(("0.0.0.0", port), ShareHandler)
        except OSError as exc:
            raise ShareError(f"Port {port} cannot be used ({exc.strerror or exc}). Choose another port.") from exc
        host = Host(httpd, model_url)
        httpd.share = host
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="axis-share").start()
        _HOST = host
    if port:
        _save_settings(host_port=port)
    return host_status()


def stop_hosting() -> Dict[str, Any]:
    """Close the port and every connection on it. Chats and pictures in progress are stopped."""
    global _HOST
    with _LOCK:
        host, _HOST = _HOST, None
    if host is not None:
        host.httpd.shutdown()
        host.httpd.server_close()
        for conn in list(host.connections):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    return host_status()


def host_status() -> Dict[str, Any]:
    host = _HOST
    out: Dict[str, Any] = {"hosting": host is not None, "port": host.port if host else load_settings()["host_port"],
                           "limit": CHAT_LIMIT, "queue_limit": QUEUE_LIMIT}
    if host is None:
        return out
    out.update(host.status())
    out["addresses"] = [f"{ip}:{host.port}" for ip in addresses()]
    out["offering"] = {"chat": chat_models(host.model_url), "image": image_models()}
    out["since"] = host.started
    return out


# ------------------------------------------------------------------------------ connecting

_CLIENT: Dict[str, Any] = {"tag": "", "state": "off", "message": "", "chat": [], "image": [], "checked": 0.0}
_POLL_STOP: Optional[threading.Event] = None


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", HEADER: "1"})
    with _OPENER.open(req, timeout=PROBE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fetch(tag: str) -> Dict[str, Any]:
    """The host's model list, or a ShareError that says what to do."""
    base = "http://" + tag
    try:
        ping = _get(base + PREFIX + "/ping")
        if not isinstance(ping, dict) or not ping.get("axis_share"):
            raise ShareError(f"Something answers at {tag}, but it is not Axis sharing its models.")
        data = _get(base + PREFIX + "/models")
    except ShareError:
        raise
    except urllib.error.HTTPError as exc:
        raise ShareError(f"Something answers at {tag}, but it is not Axis sharing its models (error {exc.code}).") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ShareError(f"Could not reach {tag} ({reason}). Check the address and port, and that sharing is on "
                         "at that computer (Models, Host, Start sharing).") from exc
    if not isinstance(data, dict):
        raise ShareError(f"{tag} sent an answer Axis could not read.")
    return {"chat": [m for m in data.get("chat") or [] if isinstance(m, dict) and isinstance(m.get("name"), str)],
            "image": [m for m in data.get("image") or [] if isinstance(m, dict) and isinstance(m.get("id"), str)]}


def _record(tag: str, info: Optional[Dict[str, Any]], message: str = "") -> None:
    with _LOCK:
        if _CLIENT["tag"] != tag:
            return   # disconnected, or connected somewhere else, while the answer was on its way
        if info is None:
            _CLIENT.update(state="unreachable", message=message, chat=[], image=[], checked=time.time())
        else:
            _CLIENT.update(state="connected", message="", chat=info["chat"], image=info["image"], checked=time.time())


def _poll(tag: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            _record(tag, _fetch(tag))
        except ShareError as exc:
            _record(tag, None, str(exc))
        stop.wait(POLL_SECONDS)


def _stop_polling() -> None:
    global _POLL_STOP
    if _POLL_STOP is not None:
        _POLL_STOP.set()
    _POLL_STOP = None


def _start_polling(tag: str) -> None:
    global _POLL_STOP
    _stop_polling()
    _POLL_STOP = threading.Event()
    threading.Thread(target=_poll, args=(tag, _POLL_STOP), daemon=True, name="axis-share-poll").start()


def connect(address: Any) -> Dict[str, Any]:
    """Check the host and stay connected to it. Raises ShareError (with what to do) when it cannot be reached."""
    _, _, tag = parse_address(address)
    info = _fetch(tag)
    with _LOCK:
        _CLIENT.update(tag=tag, state="connected", message="", chat=info["chat"], image=info["image"], checked=time.time())
        _start_polling(tag)
    _save_settings(address=tag, connected=True)
    return client_status()


def disconnect() -> Dict[str, Any]:
    with _LOCK:
        _stop_polling()
        _CLIENT.update(tag="", state="off", message="", chat=[], image=[], checked=0.0)
    _save_settings(connected=False)
    return client_status()


def resume() -> None:
    """On start-up: connect again to the host that was in use. The first look happens in the background."""
    s = load_settings()
    if not (s["connected"] and s["address"]):
        return
    try:
        tag = parse_address(s["address"])[2]
    except ShareError:
        return
    with _LOCK:
        _CLIENT.update(tag=tag, state="connecting", message="", chat=[], image=[], checked=0.0)
        _start_polling(tag)


def client_status() -> Dict[str, Any]:
    s = load_settings()
    with _LOCK:
        out = dict(_CLIENT)
    out["address"] = out["tag"] or s["address"]
    out["wanted"] = bool(out["tag"])
    return out


def remote_backends() -> List["backend.Backend"]:
    """The connected host's chat models, as Backends for backend.connect_all (one for each kind of server)."""
    with _LOCK:
        tag, state, models = _CLIENT["tag"], _CLIENT["state"], list(_CLIENT["chat"])
    if state != "connected":
        return []
    out: Dict[str, backend.Backend] = {}
    for m in models:
        kind = "ollama" if m.get("kind") == "ollama" else "openai"
        out.setdefault(kind, backend.Backend(kind, "http://" + tag, [], remote=True, tag=tag)).models.append(m["name"])
    return list(out.values())


def remote_image_models() -> List[Dict[str, Any]]:
    """The connected host's running image models, shaped like images.list_models() entries."""
    with _LOCK:
        tag, state, models = _CLIENT["tag"], _CLIENT["state"], list(_CLIENT["image"])
    if state != "connected":
        return []
    return [{"id": f"net:{tag}/{m['id']}", "name": f"{m.get('name') or m['id']}{backend.REMOTE_SEP}{tag}",
             "description": m.get("description") or "", "width": m.get("width"), "height": m.get("height"),
             "steps": m.get("steps"), "cfg_scale": m.get("cfg_scale"), "sampler": m.get("sampler") or "",
             "files": [], "missing": [], "remote": True, "host": tag, "host_id": m["id"]} for m in models]


def find_remote_image(name_or_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The remote image model a picture request names (by id or shown name), or None for a local one.

    With no name, a remote model is used only when there is no local image model at all."""
    remote = remote_image_models()
    if not remote:
        return None
    if not name_or_id:
        try:
            return remote[0] if not images.list_models() else None
        except Exception:
            return None
    key = str(name_or_id).strip().lower()
    return next((m for m in remote if m["id"].lower() == key or m["name"].lower() == key), None)


def generate_remote(model: Dict[str, Any], raw: Dict[str, Any], folder: Path, source: str,
                    emit: Callable[[dict], None], preview: bool, init_image: Optional[Path], init_name: str) -> Dict[str, Any]:
    """Make a picture on the host and save it in `folder` here, as images.generate does for a local model."""
    tag = model["host"]
    with _LOCK:
        live = _CLIENT["tag"] == tag and _CLIENT["state"] == "connected"
    if not live:
        raise images.ImageError(f"The computer at {tag} is not connected right now. Connect to it on the Models page.")
    body: Dict[str, Any] = {k: raw[k] for k in PARAMS if raw.get(k) not in (None, "")}
    body["model"] = model["host_id"]
    body["preview"] = bool(preview)
    if init_image is not None:
        try:
            body["init_image"] = base64.b64encode(init_image.read_bytes()).decode("ascii")
        except OSError as exc:
            raise images.ImageError(f"Could not read the picture to edit: {exc.strerror or exc}") from exc
        body["init_ext"] = init_image.suffix.lower()
    req = urllib.request.Request("http://" + tag + PREFIX + "/image", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/x-ndjson", HEADER: "1"})
    started = time.time()
    emit({"phase": f"Sending the request to {tag}"})
    try:
        with _OPENER.open(req, timeout=min(images.TIMEOUT, 900.0)) as resp:
            for line in resp:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                event = json.loads(text)
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise images.ImageError(str(event["error"]))
                if "queued" in event:
                    ahead = event["queued"]
                    emit({"queued": True})
                    emit({"phase": f"Waiting for {tag}" + (f" ({ahead} picture{'s' if ahead != 1 else ''} ahead)"
                                                          if isinstance(ahead, int) and ahead > 0 else "")})
                elif isinstance(event.get("image"), dict):
                    got = event["image"]
                    try:
                        png = base64.b64decode(got.get("png") or "", validate=True)
                    except ValueError as exc:
                        raise images.ImageError(f"{tag} sent a picture Axis could not read.") from exc
                    params = {k: (got.get("params") or {}).get(k) for k in
                              ("prompt", "negative_prompt", "width", "height", "steps", "cfg_scale", "sampler", "seed")}
                    seconds = got.get("seconds")
                    return images.store_picture(folder, params, png, model["name"], model["id"],
                                                seconds if isinstance(seconds, (int, float)) else round(time.time() - started, 1),
                                                source, init_name, host=tag)
                elif "done" not in event:
                    emit({k: v for k, v in event.items() if k in ("phase", "progress", "preview")})
    except urllib.error.HTTPError as exc:
        try:
            message = str(json.loads(exc.read().decode("utf-8", errors="replace")).get("error") or exc.reason)
        except Exception:
            message = str(exc.reason)
        raise images.ImageError(f"{tag}: {message}") from exc
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise images.ImageError(f"Lost connection to {tag} ({getattr(exc, 'reason', exc)}).") from exc
    except ValueError as exc:
        raise images.ImageError(f"{tag} sent an answer Axis could not read.") from exc
    raise images.ImageError(f"{tag} stopped before the picture was finished.")


# ------------------------------------------------------------------------------- all together


def status() -> Dict[str, Any]:
    return {"host": host_status(), "client": client_status(), "default_port": DEFAULT_PORT}


def shutdown() -> None:
    """Close the sharing port and stop looking at the host (Axis is quitting)."""
    try:
        stop_hosting()
    finally:
        with _LOCK:
            _stop_polling()
            _CLIENT.update(tag="", state="off", message="", chat=[], image=[], checked=0.0)


backend.add_remote_source(remote_backends)
