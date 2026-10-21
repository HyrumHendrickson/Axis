"""Saved conversations.

Each chat is ~/Axis/chats/<id>.json:

    {"id", "agent", "title", "created", "updated", "messages": [...]}

agent is "" for Direct chat. title is "" unless the user renamed the chat; the
list shows the first message instead. Chats are only deleted by the user, or
together with their agent.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .paths import axis_home

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
MAX_MESSAGES = 2000
MAX_TITLE = 200

_LOCK = threading.Lock()
_META: Dict[str, Tuple[float, Dict[str, Any]]] = {}  # path -> (mtime, summary) so listing stays fast


class ChatError(Exception):
    pass


def chats_root() -> Path:
    return axis_home() / "chats"


def _path(chat_id: Any) -> Path:
    if not isinstance(chat_id, str) or not ID_RE.fullmatch(chat_id):
        raise ChatError(f"Chat '{chat_id}' does not exist.")
    return chats_root() / f"{chat_id}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _first_line(text: str, limit: int = 80) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "\u2026"


def summary(chat: Dict[str, Any]) -> Dict[str, Any]:
    msgs = chat.get("messages") or []
    first = next((m.get("content") for m in msgs if isinstance(m, dict) and m.get("role") == "user"), "")
    return {
        "id": chat.get("id"),
        "agent": chat.get("agent") or "",
        "title": chat.get("title") or _first_line(first) or "Untitled chat",
        "custom_title": bool(chat.get("title")),
        "created": chat.get("created") or "",
        "updated": chat.get("updated") or "",
        "messages": len(msgs),
    }


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChatError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ChatError(f"{path.name} is not a valid chat file.")
    data["id"] = path.stem
    return data


def _write(chat: Dict[str, Any]) -> None:
    root = chats_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{chat['id']}.json.tmp"
    tmp.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, root / f"{chat['id']}.json")


def _messages(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_MESSAGES:
        raise ChatError(f"messages must be a list of at most {MAX_MESSAGES} messages.")
    out = []
    for m in value:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            raise ChatError("Each message needs a role of 'user' or 'assistant' and text content.")
        out.append(m)
    return out


def list_all() -> List[Dict[str, Any]]:
    root = chats_root()
    if not root.is_dir():
        return []
    result = []
    with _LOCK:
        for path in root.glob("*.json"):
            if not ID_RE.fullmatch(path.stem):
                continue
            try:
                mtime = path.stat().st_mtime
                cached = _META.get(str(path))
                if not cached or cached[0] != mtime:
                    cached = (mtime, summary(_read(path)))
                    _META[str(path)] = cached
                result.append(cached[1])
            except (OSError, ChatError):
                continue
    result.sort(key=lambda c: c["updated"], reverse=True)
    return result


def get(chat_id: str) -> Dict[str, Any]:
    path = _path(chat_id)
    if not path.is_file():
        raise ChatError(f"Chat '{chat_id}' does not exist.")
    return _read(path)


def save(chat_id: str, data: Dict[str, Any], agent: str) -> Dict[str, Any]:
    """Create or replace a chat. The caller checks that the agent exists."""
    path = _path(chat_id)
    if not isinstance(data, dict):
        raise ChatError("Request body must be a JSON object.")
    messages = _messages(data.get("messages"))
    title = data.get("title") or ""
    if not isinstance(title, str):
        raise ChatError("title must be text.")
    with _LOCK:
        try:
            old = _read(path) if path.is_file() else {}
        except ChatError:
            old = {}
        chat = {
            "id": chat_id, "agent": agent, "title": title.strip()[:MAX_TITLE],
            "created": old.get("created") or _now(), "updated": _now(), "messages": messages,
        }
        _write(chat)
    return summary(chat)


def rename(chat_id: str, title: Any) -> Dict[str, Any]:
    if not isinstance(title, str):
        raise ChatError("title must be text.")
    with _LOCK:
        chat = get(chat_id)
        chat["title"] = title.strip()[:MAX_TITLE]
        _write(chat)  # renaming does not count as activity, so "updated" stays
    return summary(chat)


def delete(chat_id: str) -> None:
    path = _path(chat_id)
    with _LOCK:
        if not path.is_file():
            raise ChatError(f"Chat '{chat_id}' does not exist.")
        path.unlink()
        _META.pop(str(path), None)


def delete_for_agent(agent: str) -> int:
    """Called when an agent is deleted: its chats go with it."""
    n = 0
    for c in list_all():
        if c["agent"] == agent:
            try:
                delete(c["id"])
                n += 1
            except (OSError, ChatError):
                continue
    return n


def rename_agent(old: str, new: str) -> None:
    """Called when an agent is renamed, so its chats follow it."""
    with _LOCK:
        root = chats_root()
        if not root.is_dir():
            return
        for path in root.glob("*.json"):
            try:
                chat = _read(path)
            except ChatError:
                continue
            if chat.get("agent") == old:
                chat["agent"] = new
                _write(chat)
