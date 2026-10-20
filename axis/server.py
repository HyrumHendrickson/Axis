"""The local web server behind the Axis page.

Routes:
  GET    /                    the app
  GET    /api/ping            identifies a running Axis
  GET    /api/status          model server and loaded models
  GET    /api/agents          list agents
  POST   /api/agents          create an agent
  GET    /api/agents/<name>   one agent
  PUT    /api/agents/<name>   update (or rename) an agent
  DELETE /api/agents/<name>   delete an agent
  POST   /api/chat            stream a reply as newline-delimited JSON
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from . import __version__, agents, backend

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 5 * 1024 * 1024


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


class Handler(BaseHTTPRequestHandler):
    server: AxisServer
    server_version = f"Axis/{__version__}"

    def log_message(self, format, *args):  # keep the terminal quiet
        pass

    # ------------------------------------------------------------ helpers

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
            return json.loads(raw.decode("utf-8"))
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
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if method == "GET" and path == "/":
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and path == "/api/ping":
                return self._json(200, {"app": "axis", "version": __version__})
            if method == "GET" and path == "/api/status":
                return self._status()
            if path == "/api/agents":
                if method == "GET":
                    return self._json(200, [a.to_dict() for a in agents.list_all()])
                if method == "POST":
                    return self._json(201, agents.create(self._body()).to_dict())
            if path.startswith("/api/agents/"):
                name = urllib.parse.unquote(path[len("/api/agents/"):])
                if method == "GET":
                    return self._json(200, agents.load(name).to_dict())
                if method == "PUT":
                    return self._json(200, agents.update(name, self._body()).to_dict())
                if method == "DELETE":
                    agents.delete(name)
                    return self._json(200, {"deleted": name})
            if method == "POST" and path == "/api/chat":
                return self._chat()
            return self._error(404, "Not found.")
        except agents.AgentError as exc:
            missing = "does not exist" in str(exc)
            return self._error(404 if missing else 400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # never let one bad request take the page down
            return self._error(500, f"Unexpected error: {exc}")

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    # ------------------------------------------------------------ endpoints

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
            backend.chat(conn, model, messages, on_token=lambda t: emit({"t": t}))
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
