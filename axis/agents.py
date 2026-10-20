"""Agent storage and prompt assembly.

Each agent is saved as ~/Axis/agents/<name>/agent.json and holds:

* system_prompt  who the agent is (sent as the system message)
* config         a list of instructions appended to every message you send
* skills         named instruction blocks; enabled skills are added to the
                 system message, disabled ones are kept but not sent
* model          preferred model ("" means whichever model is loaded)
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PROMPT = "You are a concise and helpful assistant."
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_TEXT = 20000
MAX_ITEMS = 100


class AgentError(Exception):
    pass


def axis_home() -> Path:
    override = os.environ.get("AXIS_HOME")
    return Path(override).expanduser() if override else Path.home() / "Axis"


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
class Skill:
    name: str
    instructions: str
    enabled: bool = True


@dataclass
class Agent:
    name: str
    system_prompt: str = DEFAULT_PROMPT
    model: str = ""
    config: List[str] = field(default_factory=list)
    skills: List[Skill] = field(default_factory=list)
    created: str = ""

    @property
    def folder(self) -> Path:
        return agents_root() / self.name

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "agent.json.tmp"
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
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
            raise AgentError(f"Skills must be a list of at most {MAX_ITEMS} skills.")
        skills = []
        for raw in items:
            if not isinstance(raw, dict):
                raise AgentError("Each skill needs a name and instructions.")
            name = _text(raw.get("name"), "Skill name", 100)
            instructions = _text(raw.get("instructions"), "Skill instructions")
            if not name and not instructions:
                continue
            if not name:
                raise AgentError("Every skill needs a name.")
            skills.append(Skill(name, instructions, bool(raw.get("enabled", True))))
        agent.skills = skills


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
    try:
        _apply(agent, {k: data[k] for k in ("system_prompt", "model", "config", "skills") if k in data})
    except AgentError as exc:
        raise AgentError(f"{path}: {exc}") from exc
    return agent


def update(name: str, data: Dict[str, Any]) -> Agent:
    agent = load(name)
    new_name = validate_name(data.get("name", agent.name))
    _apply(agent, data)
    if new_name != agent.name:
        if (agents_root() / new_name).exists():
            raise AgentError(f"An agent named '{new_name}' already exists.")
        os.replace(agent.folder, agents_root() / new_name)
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
    shutil.rmtree(folder)


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

    if agent is None:
        return messages

    system = agent.system_prompt
    active = [s for s in agent.skills if s.enabled]
    if active:
        system += "\n\nYou have the following skills. Use them when relevant.\n"
        system += "\n".join(f"\n## {s.name}\n{s.instructions}" for s in active)

    if agent.config:
        rules = "\n".join(f"- {line}" for line in agent.config)
        messages[-1] = {
            "role": "user",
            "content": f"{messages[-1]['content']}\n\n---\nFollow these instructions:\n{rules}",
        }
    return [{"role": "system", "content": system}] + messages
