"""Agent storage and prompt assembly.

Each agent lives in ~/Axis/agents/<name>/:

    agent.json   settings
    workspace/   the agent's own folder; every file it makes is saved here

Settings:
* system_prompt  who the agent is (sent as the system message)
* config         a list of instructions appended to every message you send
* skills         ids of skills from the skill library (see skills.py)
* model          preferred model ("" means whichever model is loaded)

Deleting an agent deletes its whole folder, including the workspace.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import skills as skill_lib
from .paths import axis_home

DEFAULT_PROMPT = "You are a concise and helpful assistant."
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_TEXT = 20000
MAX_ITEMS = 100
HISTORY_CHARS = int(os.environ.get("AXIS_HISTORY_CHARS", "20000"))


class AgentError(Exception):
    pass


def agents_root() -> Path:
    return axis_home() / "agents"


def validate_name(name: Any) -> str:
    name = str(name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise AgentError(
            "Agent names use letters, numbers, '-' or '_', start with a letter or number, "
            "and are at most 64 characters."
        )
    return name


def _text(value: Any, label: str, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AgentError(f"{label} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise AgentError(f"{label} is too long (limit {limit} characters).")
    return value


@dataclass
class Agent:
    name: str
    system_prompt: str = DEFAULT_PROMPT
    model: str = ""
    config: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    created: str = ""

    @property
    def folder(self) -> Path:
        return agents_root() / self.name

    @property
    def workspace(self) -> Path:
        return self.folder / "workspace"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["workspace"] = str(self.workspace)
        return data

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(exist_ok=True)
        data = asdict(self)
        tmp = self.folder / "agent.json.tmp"
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.folder / "agent.json")


def _apply(agent: Agent, data: Dict[str, Any]) -> None:
    """Validate and copy editable fields from data onto agent."""
    if "system_prompt" in data:
        agent.system_prompt = _text(data["system_prompt"], "System prompt") or DEFAULT_PROMPT
    if "model" in data:
        agent.model = _text(data["model"], "Model", 200)
    if "config" in data:
        items = data["config"]
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise AgentError(f"Config must be a list of at most {MAX_ITEMS} instructions.")
        agent.config = [t for t in (_text(i, "Config instruction") for i in items) if t]
    if "skills" in data:
        items = data["skills"]
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise AgentError(f"Skills must be a list of at most {MAX_ITEMS} skill ids.")
        chosen: List[str] = []
        for skill_id in items:
            if not isinstance(skill_id, str) or not skill_lib.exists(skill_id):
                raise AgentError(f"Unknown skill '{skill_id}'. It may have been deleted; reload the page.")
            if skill_id not in chosen:
                chosen.append(skill_id)
        agent.skills = chosen


def create(data: Dict[str, Any]) -> Agent:
    name = validate_name(data.get("name"))
    if (agents_root() / name).exists():
        raise AgentError(f"An agent named '{name}' already exists.")
    agent = Agent(name=name, created=datetime.now().isoformat(timespec="seconds"))
    _apply(agent, data)
    agent.save()
    return agent


def load(name: str) -> Agent:
    name = validate_name(name)
    path = agents_root() / name / "agent.json"
    if not path.is_file():
        raise AgentError(f"Agent '{name}' does not exist.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError(f"{path} is not a valid agent file.")
    agent = Agent(name=name, created=str(data.get("created") or ""))
    raw_skills = data.get("skills") or []
    migrated = False
    if isinstance(raw_skills, list) and any(isinstance(x, dict) for x in raw_skills):
        # Axis 2.0 stored skills inside each agent; move them into the library.
        ids = []
        for item in raw_skills:
            if isinstance(item, dict):
                new_id = skill_lib.find_or_create(str(item.get("name") or ""), str(item.get("instructions") or ""))
                if new_id and item.get("enabled", True):
                    ids.append(new_id)
            elif isinstance(item, str):
                ids.append(item)
        raw_skills, migrated = ids, True
    data = dict(data)
    data["skills"] = [x for x in raw_skills if isinstance(x, str) and skill_lib.exists(x)] if isinstance(raw_skills, list) else []
    try:
        _apply(agent, {k: data[k] for k in ("system_prompt", "model", "config", "skills") if k in data})
    except AgentError as exc:
        raise AgentError(f"{path}: {exc}") from exc
    if migrated:
        agent.save()
    return agent


def update(name: str, data: Dict[str, Any]) -> Agent:
    agent = load(name)
    new_name = validate_name(data.get("name", agent.name))
    _apply(agent, data)
    if new_name != agent.name:
        if (agents_root() / new_name).exists():
            raise AgentError(f"An agent named '{new_name}' already exists.")
        try:
            os.replace(agent.folder, agents_root() / new_name)
        except OSError as exc:
            raise AgentError(
                f"Could not rename the agent's folder ({exc.strerror or exc}). "
                "Close any of its files that are open in other programs and try again."
            ) from exc
        agent.name = new_name
    agent.save()
    return agent


def list_all() -> List[Agent]:
    root = agents_root()
    if not root.is_dir():
        return []
    result = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and NAME_RE.fullmatch(child.name) and (child / "agent.json").is_file():
            try:
                result.append(load(child.name))
            except AgentError:
                continue
    return result


def delete(name: str) -> None:
    agent = load(name)
    folder = agent.folder.resolve()
    if folder.parent != agents_root().resolve():
        raise AgentError("Refusing to delete a folder outside the Axis agents directory.")
    def retry_writable(func, path, *_):
        # Windows refuses to delete read-only files; make them writable and retry.
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=retry_writable)
    else:
        shutil.rmtree(folder, onerror=retry_writable)
    if folder.exists():
        raise AgentError(
            "Some of the agent's files could not be deleted. Close any of its files that are open "
            f"in other programs, then delete the agent again. Folder: {folder}"
        )


# ----------------------------------------------------------------- prompts


def build_messages(agent: Optional[Agent], history: Any) -> List[Dict[str, str]]:
    """Turn a chat history into the messages sent to the model.

    For an agent, the system message is its system prompt plus enabled skills,
    and its config instructions are appended to the newest user message.
    """
    if not isinstance(history, list) or not history:
        raise AgentError("The conversation is empty.")
    messages: List[Dict[str, str]] = []
    for item in history[-200:]:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise AgentError("Each message needs a role of 'user' or 'assistant'.")
        content = item.get("content")
        if not isinstance(content, str):
            raise AgentError("Each message needs text content.")
        messages.append({"role": item["role"], "content": content})
    if messages[-1]["role"] != "user" or not messages[-1]["content"].strip():
        raise AgentError("The last message must be a non-empty user message.")

    # Keep the newest messages that fit the budget so the model's context window
    # is not overflowed (Ollama silently drops the oldest text, including instructions).
    kept, used = [], 0
    for m in reversed(messages):
        used += len(m["content"])
        if kept and used > HISTORY_CHARS:
            break
        kept.append(m)
    messages = list(reversed(kept))
    while len(messages) > 1 and messages[0]["role"] != "user":
        messages.pop(0)

    if agent is None:
        return messages

    system = agent.system_prompt
    chosen = [skill_lib.get(i) for i in agent.skills if skill_lib.exists(i)]
    if chosen:
        system += "\n\nYou have the following skills. Use them when relevant.\n"
        system += "\n".join(f"\n## {s['name']}\n{s['instructions']}" for s in chosen)
    if tool_names(agent):
        system += "\n\n" + skill_lib.WORKSPACE_NOTE

    if agent.config:
        rules = "\n".join(f"- {line}" for line in agent.config)
        messages[-1] = {
            "role": "user",
            "content": f"{messages[-1]['content']}\n\n---\nFollow these instructions:\n{rules}",
        }
    return [{"role": "system", "content": system}] + messages


def tool_names(agent: Optional[Agent]) -> List[str]:
    """The actions an agent may take, from its built-in skills."""
    if agent is None:
        return []
    names: List[str] = []
    for skill_id in agent.skills:
        if skill_id in skill_lib.BUILTIN_IDS:
            for t in skill_lib.get(skill_id)["tools"]:
                if t not in names:
                    names.append(t)
    return names


def remove_skill_everywhere(skill_id: str) -> None:
    for agent in list_all():
        if skill_id in agent.skills:
            agent.skills = [s for s in agent.skills if s != skill_id]
            agent.save()


def list_workspace(agent: Agent, limit: int = 300) -> List[Dict[str, Any]]:
    root = agent.workspace
    if not root.is_dir():
        return []
    files: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".axis-tmp")
        for f in sorted(filenames):
            full = Path(dirpath) / f
            try:
                size = full.stat().st_size
            except OSError:
                continue
            files.append({"path": full.relative_to(root).as_posix(), "size": size})
            if len(files) >= limit:
                return files
    return files
