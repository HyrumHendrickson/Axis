"""Connection to the model servers.

Two kinds of server are supported:

* Ollama (default http://127.0.0.1:11434). Running models come from /api/ps.
* llama.cpp ``llama-server`` or any other OpenAI-compatible server
  (default http://127.0.0.1:8080). Loaded models come from /v1/models.

Axis looks at every server it knows about at once: the default addresses (or the
one given with --url / AXIS_URL), plus each llama-server that Axis started itself
on the Models page. A chat goes to whichever server has the chosen model loaded.

Pictures: a message may carry "images", a list of image files (pathlib.Path). They
are sent as base64 in each server's own format: an "images" list on the message for
Ollama, and image_url parts of the content for llama.cpp (which needs the model's
mmproj file loaded, see manager.llama_start).
"""

from __future__ import annotations

import base64
import json
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

DEFAULT_URLS = ("http://127.0.0.1:11434", "http://127.0.0.1:8080")
PROBE_TIMEOUT = 3.0
GENERATE_TIMEOUT = 600.0
# Ollama's default context is 4096 tokens and it silently drops anything older,
# which can cut off an agent's instructions and tool definitions. 0 = leave Ollama's default.
NUM_CTX = int(os.environ.get("AXIS_NUM_CTX", "8192"))

# Local servers should never be reached through a system HTTP proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Models Axis asked Ollama to keep loaded (keep_alive -1), as (server url, model name).
# Ollama never unloads these on its own, so Axis unloads them when it quits; otherwise they
# stay in memory (and in `ollama ps`) whenever Ollama was started outside Axis.
_PINNED: set = set()
_PINNED_LOCK = threading.Lock()


def pin_ollama(url: str, model: str) -> None:
    with _PINNED_LOCK:
        _PINNED.add((url.rstrip("/"), model))


def unpin_ollama(url: str, model: str) -> None:
    with _PINNED_LOCK:
        _PINNED.discard((url.rstrip("/"), model))


def take_pinned_ollama() -> List[tuple]:
    """Every model Axis pinned in Ollama, forgetting them (called once when Axis quits)."""
    with _PINNED_LOCK:
        out = sorted(_PINNED)
        _PINNED.clear()
    return out


START_HELP = (
    "Start a model on the Models page, or from a terminal:\n"
    "  Ollama:     ollama run llama3.2:3b --keepalive -1m\n"
    "  llama.cpp:  llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M -c 8192\n"
    "See the README for full setup instructions."
)

# Functions that return extra server addresses to look at (the llama-servers Axis started).
_URL_SOURCES: List[Callable[[], List[str]]] = []


def add_url_source(source: Callable[[], List[str]]) -> None:
    if source not in _URL_SOURCES:
        _URL_SOURCES.append(source)


class BackendError(Exception):
    """A problem talking to the model server that the user must fix."""

    user_facing = True


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


def _get_json(url: str, timeout: float = PROBE_TIMEOUT):
    with _open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _post_json(url: str, body: dict, timeout: float = PROBE_TIMEOUT):
    with _open(url, body, timeout=timeout) as resp:
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


def candidates(url: Optional[str] = None) -> List[str]:
    """Server addresses to look at, in order of preference."""
    explicit = resolve_url(url)
    out = [explicit] if explicit else list(DEFAULT_URLS)
    for source in _URL_SOURCES:
        try:
            extra = source()
        except Exception:
            continue
        for u in extra:
            u = u.rstrip("/")
            if u not in out:
                out.append(u)
    return out


def _probe(base: str) -> Optional[Backend]:
    return _probe_ollama(base) or _probe_openai(base)


def _probe_all(urls: List[str]) -> List[Any]:
    """Probe every address at once; each result is a Backend, None, or the BackendError raised."""
    def one(base: str):
        try:
            return _probe(base)
        except BackendError as exc:
            return exc
    if len(urls) == 1:
        return [one(urls[0])]
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        return list(pool.map(one, urls))


def connect_all(url: Optional[str] = None) -> List[Backend]:
    """Every reachable server that has at least one model loaded. Raises BackendError when there is none."""
    urls = candidates(url)
    results = _probe_all(urls)
    found = [r for r in results if isinstance(r, Backend) and r.models]
    if found:
        return found
    errors = [r for r in results if isinstance(r, BackendError)]
    if errors:
        raise errors[0]
    empty = next((r for r in results if isinstance(r, Backend)), None)
    if empty is not None:
        raise BackendError(f"{empty.label} is running at {empty.url}, but no model is loaded.\n" + START_HELP)
    explicit = resolve_url(url)
    where = explicit if explicit else " or ".join(DEFAULT_URLS)
    raise ServerUnavailable(f"No model server is running at {where}.\n" + START_HELP)


def _match(models: List[str], requested: str) -> Optional[str]:
    for name in models:
        if requested == name or f"{requested}:latest" == name:
            return name
    return None


def connect(url: Optional[str] = None, model: Optional[str] = None) -> Backend:
    """Find a running server with at least one loaded model (the one that has `model`, when given)."""
    backends = connect_all(url)
    if model:
        for b in backends:
            if _match(b.models, model):
                return b
    return backends[0]


def loaded_models(backends: List[Backend]) -> List[str]:
    out: List[str] = []
    for b in backends:
        for m in b.models:
            if m not in out:
                out.append(m)
    return out


def pick(url: Optional[str], requested: Optional[str]) -> "tuple[Backend, str]":
    """The server and model a chat should use."""
    backends = connect_all(url)
    if not requested:
        return backends[0], backends[0].models[0]
    for b in backends:
        name = _match(b.models, requested)
        if name:
            return b, name
    raise BackendError(f"Model '{requested}' is not loaded. Loaded: {', '.join(loaded_models(backends))}\n"
                       "To use it, start it on the Models page.")


def resolve_model(backend: Backend, requested: Optional[str]) -> str:
    """Pick a loaded model. A requested model must be one that is currently loaded."""
    if not requested:
        return backend.models[0]
    name = _match(backend.models, requested)
    if name:
        return name
    running = ", ".join(backend.models)
    raise BackendError(f"Model '{requested}' is not loaded. Loaded: {running}\nTo use it, start it on the Models page.")


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
    # Token counts and speed reported by the server, when it reports them:
    # prompt_tokens, output_tokens, seconds (time spent writing the output).
    stats: Dict[str, Any] = field(default_factory=dict)


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


class ImagesUnsupported(BackendError):
    """The model (or the way it was started) cannot read pictures."""


IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
              ".gif": "image/gif", ".bmp": "image/bmp"}


def _b64(path: Path, cache: Dict[str, str]) -> str:
    key = str(path)
    if key not in cache:
        try:
            cache[key] = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise BackendError(f"Could not read the picture {path.name}: {exc.strerror or exc}") from exc
    return cache[key]


def wire_messages(kind: str, messages: list) -> list:
    """Messages in the server's format, with any attached pictures encoded."""
    if not any(isinstance(m, dict) and m.get("images") for m in messages):
        return messages
    cache: Dict[str, str] = {}
    out = []
    for m in messages:
        files = m.get("images") if isinstance(m, dict) else None
        if not files:
            out.append(m)
            continue
        rest = {k: v for k, v in m.items() if k != "images"}
        if kind == "ollama":
            rest["images"] = [_b64(Path(f), cache) for f in files]
        else:
            parts: List[Dict[str, Any]] = [{"type": "text", "text": rest.get("content") or ""}]
            for f in files:
                mime = IMAGE_MIME.get(Path(f).suffix.lower(), "image/png")
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{_b64(Path(f), cache)}"}})
            rest["content"] = parts
        out.append(rest)
    return out


def _images_message(model: str, kind: str) -> str:
    text = (f"{model} cannot read pictures. Choose a model that can (the Models page lists \"vision\" among "
            "its capabilities), such as gemma3, qwen2.5vl or llava.")
    if kind != "ollama":
        text += (" A llama.cpp model also needs its vision file (mmproj): install the model again on the Models "
                 "page so Axis downloads it, then stop and start the model.")
    return text


def _stream_ollama(backend: Backend, model: str, messages: list, tools: Optional[list], turn: Turn,
                   temperature: Optional[float]) -> Iterator[str]:
    # keep_alive -1 keeps the model loaded until the user stops it (ollama stop <model>).
    body: Dict[str, Any] = {"model": model, "messages": wire_messages("ollama", messages), "stream": True, "keep_alive": -1}
    options: Dict[str, Any] = {}
    if NUM_CTX > 0:
        options["num_ctx"] = NUM_CTX
    if temperature is not None:
        options["temperature"] = temperature
    if options:
        body["options"] = options
    if tools:
        body["tools"] = tools
    pin_ollama(backend.url, model)
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
                _ollama_stats(chunk, turn)
                return


def _ollama_stats(chunk: dict, turn: Turn) -> None:
    stats: Dict[str, Any] = {}
    if isinstance(chunk.get("prompt_eval_count"), int):
        stats["prompt_tokens"] = chunk["prompt_eval_count"]
    if isinstance(chunk.get("eval_count"), int):
        stats["output_tokens"] = chunk["eval_count"]
    if isinstance(chunk.get("eval_duration"), (int, float)) and chunk["eval_duration"] > 0:
        stats["seconds"] = chunk["eval_duration"] / 1e9
    turn.stats.update(stats)


def _openai_stats(chunk: dict, turn: Turn) -> None:
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        if isinstance(usage.get("prompt_tokens"), int):
            turn.stats["prompt_tokens"] = usage["prompt_tokens"]
        if isinstance(usage.get("completion_tokens"), int):
            turn.stats["output_tokens"] = usage["completion_tokens"]
    timings = chunk.get("timings")  # llama-server
    if isinstance(timings, dict):
        if isinstance(timings.get("prompt_n"), int):
            # prompt_n counts only tokens not already cached, so prefer usage when it exists
            turn.stats.setdefault("prompt_tokens", timings["prompt_n"] + int(timings.get("cache_n") or 0))
        if isinstance(timings.get("predicted_n"), int):
            turn.stats["output_tokens"] = timings["predicted_n"]
        if isinstance(timings.get("predicted_ms"), (int, float)) and timings["predicted_ms"] > 0:
            turn.stats["seconds"] = timings["predicted_ms"] / 1000


def _stream_openai(backend: Backend, model: str, messages: list, tools: Optional[list], turn: Turn,
                   temperature: Optional[float]) -> Iterator[str]:
    body: Dict[str, Any] = {"model": model, "messages": wire_messages("openai", messages), "stream": True}
    if temperature is not None:
        body["temperature"] = temperature
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
                _openai_stats(chunk, turn)
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
    temperature: Optional[float] = None,
) -> Turn:
    """Send a conversation; stream text to on_token; return the text and any tool calls."""
    stream = _stream_ollama if backend.kind == "ollama" else _stream_openai
    turn = Turn("")
    parts: List[str] = []
    try:
        for text in stream(backend, model, messages, tools, turn, temperature):
            parts.append(text)
            if on_token:
                on_token(text)
    except urllib.error.HTTPError as exc:
        message = _http_error_message(exc)
        low = message.lower()
        if any(isinstance(m, dict) and m.get("images") for m in messages) and \
                ("image" in low or "vision" in low or "mmproj" in low or "multimodal" in low):
            raise ImagesUnsupported(_images_message(model, backend.kind) + f"\n({message})") from exc
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


# ------------------------------------------------------------------ model info


def _num(value: Any) -> Optional[float]:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def kv_cache_bytes(info: Dict[str, Any], context: int) -> Optional[int]:
    """Estimate the memory the context cache needs (16-bit keys and values)."""
    layers, embd, heads = info.get("layers"), info.get("embedding_length"), info.get("head_count")
    kv_heads = info.get("head_count_kv") or heads
    key_len = info.get("key_length") or (embd // heads if embd and heads else None)
    val_len = info.get("value_length") or key_len
    if not (layers and key_len and kv_heads and context):
        return None
    per_layer = kv_heads if isinstance(kv_heads, list) else [kv_heads] * int(layers)
    per_layer = [h for h in per_layer if isinstance(h, (int, float))]
    return int(sum(per_layer) * (key_len + val_len) * 2 * context)


def _arch_info(model_info: Dict[str, Any]) -> Dict[str, Any]:
    arch = model_info.get("general.architecture") or ""
    get = lambda key: model_info.get(f"{arch}.{key}")  # noqa: E731
    out = {
        "architecture": arch or None,
        "parameter_count": _num(model_info.get("general.parameter_count")),
        "max_context": _num(get("context_length")),
        "embedding_length": _num(get("embedding_length")),
        "layers": _num(get("block_count")),
        "head_count": _num(get("attention.head_count")),
        "head_count_kv": get("attention.head_count_kv"),
        "key_length": _num(get("attention.key_length")),
        "value_length": _num(get("attention.value_length")),
        "vocab_size": _num(get("vocab_size")),
        "expert_count": _num(get("expert_count")),
    }
    hk = out["head_count_kv"]
    if not (_num(hk) or (isinstance(hk, list) and all(_num(x) is not None for x in hk))):
        out["head_count_kv"] = None
    return out


_SHOW_CACHE: Dict[tuple, Dict[str, Any]] = {}


def _ollama_show(base: str, name: str, stamp: Optional[str] = None) -> Dict[str, Any]:
    """/api/show for one model. Cached per install time, since it only changes when the model is pulled again."""
    key = (base, name, stamp)
    if stamp and key in _SHOW_CACHE:
        return dict(_SHOW_CACHE[key])
    info = _ollama_show_uncached(base, name)
    if stamp and info:
        if len(_SHOW_CACHE) > 500:
            _SHOW_CACHE.clear()
        _SHOW_CACHE[key] = info
    return dict(info)


def _ollama_show_uncached(base: str, name: str) -> Dict[str, Any]:
    try:
        data = _post_json(base + "/api/show", {"model": name, "name": name}, timeout=10)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    info = _arch_info(data.get("model_info") or {})
    caps = data.get("capabilities")
    info["capabilities"] = [str(c) for c in caps] if isinstance(caps, list) else []
    params = data.get("parameters")
    info["parameters"] = params.strip() if isinstance(params, str) else ""
    lic = data.get("license")
    info["license"] = (lic.strip().splitlines() or [""])[0][:160] if isinstance(lic, str) else ""
    return info


def _ollama_models(base: str) -> Dict[str, Any]:
    running = (_get_json(base + "/api/ps").get("models") or [])
    try:
        installed = _get_json(base + "/api/tags", timeout=10).get("models") or []
    except Exception:
        installed = []
    by_name: Dict[str, Dict[str, Any]] = {}
    for m in installed:
        if isinstance(m, dict) and (m.get("name") or m.get("model")):
            name = m.get("name") or m.get("model")
            d = m.get("details") or {}
            by_name[name] = {
                "name": name, "running": False, "disk_size": _num(m.get("size")), "modified": m.get("modified_at"),
                "family": d.get("family"), "parameter_size": d.get("parameter_size"),
                "quantization": d.get("quantization_level"), "format": d.get("format"),
            }
    for m in running:
        if not isinstance(m, dict) or not (m.get("name") or m.get("model")):
            continue
        name = m.get("name") or m.get("model")
        d = m.get("details") or {}
        entry = by_name.setdefault(name, {"name": name, "disk_size": None, "modified": None})
        size, vram = _num(m.get("size")), _num(m.get("size_vram"))
        entry.update({
            "running": True, "memory": size, "memory_gpu": vram,
            "gpu_percent": round(100 * vram / size) if size and vram is not None else None,
            "context": _num(m.get("context_length")), "expires": m.get("expires_at"),
            "family": entry.get("family") or d.get("family"),
            "parameter_size": entry.get("parameter_size") or d.get("parameter_size"),
            "quantization": entry.get("quantization") or d.get("quantization_level"),
            "format": entry.get("format") or d.get("format"),
        })
    models = list(by_name.values())
    with ThreadPoolExecutor(max_workers=min(8, len(models) or 1)) as pool:
        shows = list(pool.map(lambda m: _ollama_show(base, m["name"], m.get("modified")), models))
    for m, info in zip(models, shows):
        m.update({k: v for k, v in info.items() if v is not None or k not in m})
        planned = NUM_CTX if NUM_CTX > 0 else 4096
        if m.get("max_context"):
            planned = min(planned, int(m["max_context"]))
        m["axis_context"] = planned
        kv = kv_cache_bytes(m, planned)
        m["estimated_memory"] = (m["disk_size"] + kv) if (m.get("disk_size") and kv) else None
        m["estimated_cache"] = kv
    models.sort(key=lambda m: (not m["running"], m["name"].lower()))
    return {"models": models}


def _openai_models(base: str) -> Dict[str, Any]:
    payload = _get_json(base + "/v1/models")
    try:
        props = _get_json(base + "/props")
        props = props if isinstance(props, dict) else {}
    except Exception:
        props = {}
    gen = props.get("default_generation_settings") or {}
    if not isinstance(gen, dict):
        gen = {}
    slot_ctx = _num(gen.get("n_ctx")) or _num((gen.get("params") or {}).get("n_ctx") if isinstance(gen.get("params"), dict) else None)
    slots = _num(props.get("total_slots"))
    modalities = props.get("modalities") if isinstance(props.get("modalities"), dict) else {}
    models = []
    for m in payload.get("data") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        caps = ["completion"] + [k for k, v in modalities.items() if v]
        models.append({
            "name": m["id"], "running": True,
            "context": slot_ctx or _num(meta.get("n_ctx")),
            "max_context": _num(meta.get("n_ctx_train")),
            "parameter_count": _num(meta.get("n_params")),
            "memory": _num(meta.get("size")),
            "memory_note": "model weights only; the context cache uses more" if _num(meta.get("size")) else None,
            "embedding_length": _num(meta.get("n_embd")),
            "vocab_size": _num(meta.get("n_vocab")),
            "slots": slots,
            "model_path": props.get("model_path") if isinstance(props.get("model_path"), str) else None,
            "build": props.get("build_info") if isinstance(props.get("build_info"), str) else None,
            "capabilities": caps if modalities else [],
        })
    return {"models": models}


def describe(url: Optional[str] = None) -> Dict[str, Any]:
    """Everything the servers report about their models, for the Models page."""
    urls = candidates(url)
    results = _probe_all(urls)
    servers: List[Backend] = []
    message = ""
    for base, r in zip(urls, results):
        if isinstance(r, Backend):
            if r.kind == "ollama" or r.models:   # an Ollama server with nothing loaded still lists installed models
                servers.append(r)
        elif isinstance(r, BackendError) and not message:
            message = str(r).split("\n")[0]
    if not servers:
        if not message:
            explicit = resolve_url(url)
            message = f"No model server is running at {explicit if explicit else ' or '.join(DEFAULT_URLS)}."
        return {"ready": False, "message": message, "models": [], "servers": []}
    models: List[Dict[str, Any]] = []
    errors: List[str] = []
    for conn in servers:
        try:
            data = _ollama_models(conn.url) if conn.kind == "ollama" else _openai_models(conn.url)
        except Exception as exc:
            errors.append(f"Could not read model details from {conn.url}: {exc}")
            continue
        for m in data["models"]:
            m.setdefault("server_url", conn.url)
            m.setdefault("server_kind", conn.kind)
            models.append(m)
    if not models and errors:
        return {"ready": False, "message": errors[0], "models": [], "servers": []}
    first = servers[0]
    has_ollama = any(s.kind == "ollama" for s in servers)
    return {"ready": any(s.models for s in servers), "server": first.label, "kind": first.kind, "url": first.url,
            "servers": [{"label": s.label, "kind": s.kind, "url": s.url, "models": s.models} for s in servers],
            "axis_context": NUM_CTX if has_ollama else None, "errors": errors, "models": models}
