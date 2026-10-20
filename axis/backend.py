"""Connection to a model server that the user has already started.

Axis never starts, downloads, or stops models. It supports two kinds of server:

* Ollama (default http://127.0.0.1:11434). Running models come from /api/ps.
* llama.cpp ``llama-server`` or any other OpenAI-compatible server
  (default http://127.0.0.1:8080). Loaded models come from /v1/models.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

DEFAULT_URLS = ("http://127.0.0.1:11434", "http://127.0.0.1:8080")
PROBE_TIMEOUT = 3.0
GENERATE_TIMEOUT = 600.0

# Local servers should never be reached through a system HTTP proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

START_HELP = (
    "Start a model first, then try again:\n"
    "  Ollama:     ollama run llama3.2:3b --keepalive -1m\n"
    "  llama.cpp:  llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M\n"
    "See the README for full setup instructions."
)


class BackendError(Exception):
    """A problem talking to the model server that the user must fix."""


class ServerUnavailable(BackendError):
    """The server could not be reached at all."""


@dataclass
class Backend:
    kind: str  # "ollama" or "openai"
    url: str
    models: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return "Ollama" if self.kind == "ollama" else "llama.cpp / OpenAI-compatible"


# --------------------------------------------------------------------------- HTTP


def _open(url: str, body: Optional[dict] = None, timeout: float = PROBE_TIMEOUT):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    return _OPENER.open(req, timeout=timeout)


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        err = payload.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        if err:
            return str(err)
        return raw.strip() or exc.reason
    except Exception:
        return str(exc.reason)


def _get_json(url: str):
    with _open(url) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------- detection


def _probe_ollama(base: str) -> Optional[Backend]:
    try:
        info = _get_json(base + "/api/version")
    except Exception:
        return None
    if not isinstance(info, dict) or "version" not in info:
        return None
    try:
        payload = _get_json(base + "/api/ps")
    except Exception as exc:
        raise BackendError(f"Ollama is running at {base} but /api/ps failed: {exc}") from exc
    models = [m.get("name") or m.get("model") for m in payload.get("models") or [] if isinstance(m, dict)]
    return Backend("ollama", base, [m for m in models if m])


def _probe_openai(base: str) -> Optional[Backend]:
    try:
        payload = _get_json(base + "/v1/models")
    except urllib.error.HTTPError as exc:
        if exc.code == 503:  # llama-server answers 503 while the model is still loading
            raise BackendError(f"The server at {base} is still loading its model. Wait a moment and retry.") from exc
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    models = [m.get("id") for m in payload.get("data") or [] if isinstance(m, dict)]
    return Backend("openai", base, [m for m in models if m])


def resolve_url(url: Optional[str]) -> Optional[str]:
    url = url or os.environ.get("AXIS_URL")
    if not url:
        return None
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def connect(url: Optional[str] = None) -> Backend:
    """Find a running server with at least one loaded model, or raise BackendError."""
    explicit = resolve_url(url)
    candidates = [explicit] if explicit else list(DEFAULT_URLS)

    found_empty: Optional[Backend] = None
    for base in candidates:
        backend = _probe_ollama(base) or _probe_openai(base)
        if backend is None:
            continue
        if backend.models:
            return backend
        found_empty = found_empty or backend

    if found_empty is not None:
        raise BackendError(f"{found_empty.label} is running at {found_empty.url}, but no model is loaded.\n" + START_HELP)
    where = explicit if explicit else " or ".join(DEFAULT_URLS)
    raise ServerUnavailable(f"No model server is running at {where}.\n" + START_HELP)


def resolve_model(backend: Backend, requested: Optional[str]) -> str:
    """Pick a loaded model. A requested model must be one that is currently loaded."""
    if not requested:
        return backend.models[0]
    for name in backend.models:
        if requested == name or f"{requested}:latest" == name:
            return name
    running = ", ".join(backend.models)
    hint = f"ollama run {requested} --keepalive -1m" if backend.kind == "ollama" else "restart llama-server with that model"
    raise BackendError(f"Model '{requested}' is not loaded. Loaded: {running}\nTo use it: {hint}")


# --------------------------------------------------------------------------- chat


class ToolsUnsupported(BackendError):
    """The server or model rejected the tools parameter."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any  # dict when valid, otherwise the raw text the model produced
    raw: str = ""


@dataclass
class Turn:
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)


def _parse_args(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _stream_ollama(backend: Backend, model: str, messages: list, tools: Optional[list], turn: Turn) -> Iterator[str]:
    # keep_alive -1 keeps the model loaded until the user stops it (ollama stop <model>).
    body: Dict[str, Any] = {"model": model, "messages": messages, "stream": True, "keep_alive": -1}
    if tools:
        body["tools"] = tools
    with _open(backend.url + "/api/chat", body, timeout=GENERATE_TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                raise BackendError(str(chunk["error"]))
            message = chunk.get("message") or {}
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                n = len(turn.tool_calls)
                turn.tool_calls.append(ToolCall(str(call.get("id") or f"call_{n}"), str(fn.get("name") or ""),
                                                _parse_args(fn.get("arguments")), json.dumps(fn.get("arguments"))))
            text = message.get("content") or ""
            if text:
                yield text
            if chunk.get("done"):
                return


def _stream_openai(backend: Backend, model: str, messages: list, tools: Optional[list], turn: Turn) -> Iterator[str]:
    body: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    partial: Dict[int, Dict[str, str]] = {}
    try:
        with _open(backend.url + "/v1/chat/completions", body, timeout=GENERATE_TIMEOUT) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    err = chunk["error"]
                    raise BackendError(str(err.get("message") if isinstance(err, dict) else err))
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    for call in delta.get("tool_calls") or []:
                        slot = partial.setdefault(int(call.get("index") or 0), {"id": "", "name": "", "args": ""})
                        slot["id"] = call.get("id") or slot["id"]
                        fn = call.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
                    text = delta.get("content") or ""
                    if text:
                        yield text
    finally:
        for i in sorted(partial):
            slot = partial[i]
            turn.tool_calls.append(ToolCall(slot["id"] or f"call_{i}", slot["name"], _parse_args(slot["args"]), slot["args"]))


def complete(
    backend: Backend,
    model: str,
    messages: list,
    on_token: Optional[Callable[[str], None]] = None,
    tools: Optional[list] = None,
) -> Turn:
    """Send a conversation; stream text to on_token; return the text and any tool calls."""
    stream = _stream_ollama if backend.kind == "ollama" else _stream_openai
    turn = Turn("")
    parts: List[str] = []
    try:
        for text in stream(backend, model, messages, tools, turn):
            parts.append(text)
            if on_token:
                on_token(text)
    except urllib.error.HTTPError as exc:
        message = _http_error_message(exc)
        if tools and exc.code in (400, 500, 501) and "tool" in message.lower():
            raise ToolsUnsupported(message) from exc
        raise BackendError(f"Server error ({exc.code}): {message}") from exc
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise ServerUnavailable(f"Lost connection to the model server at {backend.url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BackendError("The model server sent a response Axis could not read.") from exc
    turn.text = "".join(parts)
    return turn


def chat(backend: Backend, model: str, messages: list, on_token: Optional[Callable[[str], None]] = None) -> str:
    """Send a conversation and return the full reply, streaming tokens to on_token."""
    return complete(backend, model, messages, on_token).text


def assistant_message(backend: Backend, turn: Turn) -> Dict[str, Any]:
    """The assistant turn (with its tool calls) in the server's own format."""
    if backend.kind == "ollama":
        calls = [{"function": {"name": c.name, "arguments": c.arguments if isinstance(c.arguments, dict) else {}}}
                 for c in turn.tool_calls]
        return {"role": "assistant", "content": turn.text, "tool_calls": calls}
    calls = [{"id": c.id, "type": "function",
              "function": {"name": c.name, "arguments": c.raw if c.raw else json.dumps(c.arguments)}}
             for c in turn.tool_calls]
    return {"role": "assistant", "content": turn.text or None, "tool_calls": calls}


def tool_message(backend: Backend, call: ToolCall, result: str) -> Dict[str, Any]:
    if backend.kind == "ollama":
        return {"role": "tool", "content": result, "tool_name": call.name}
    return {"role": "tool", "tool_call_id": call.id, "content": result}
