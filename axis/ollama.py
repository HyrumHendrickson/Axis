from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

OLLAMA_API = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    pass


def _request(path: str, method: str = "GET", body: dict[str, Any] | None = None, timeout: int = 120) -> Any:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{OLLAMA_API}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.URLError as exc:
        raise OllamaError("could not reach Ollama at 127.0.0.1:11434; start it first") from exc
    except json.JSONDecodeError as exc:
        raise OllamaError("received invalid response from Ollama") from exc


def list_downloaded_models() -> list[dict[str, Any]]:
    payload = _request("/api/tags")
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def remove_model(name: str) -> None:
    _request("/api/delete", method="DELETE", body={"name": name})


def pull_model(name: str) -> None:
    body = json.dumps({"name": name, "stream": False}).encode("utf-8")
    req = urllib.request.Request(f"{OLLAMA_API}/api/pull", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if raw:
                json.loads(raw)
    except urllib.error.URLError as exc:
        raise OllamaError(f"failed to pull model '{name}'") from exc
    except json.JSONDecodeError as exc:
        raise OllamaError("unexpected response while pulling model") from exc


def chat(model: str, messages: list[dict[str, str]], timeout: int = 300) -> str:
    payload = _request(
        "/api/chat",
        method="POST",
        body={"model": model, "messages": messages, "stream": False},
        timeout=timeout,
    )
    message = payload.get("message", {})
    if not isinstance(message, dict):
        raise OllamaError("invalid chat response")
    content = message.get("content")
    if not isinstance(content, str):
        raise OllamaError("chat response missing content")
    return content


def list_online_models(limit: int = 100) -> list[str]:
    url = "https://ollama.com/library"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise OllamaError("failed to reach ollama.com library") from exc

    found = re.findall(r'href="/library/([a-zA-Z0-9_.-]+)"', html)
    ordered: list[str] = []
    seen: set[str] = set()
    for model in found:
        if model not in seen:
            ordered.append(model)
            seen.add(model)
        if len(ordered) >= limit:
            break
    return ordered
