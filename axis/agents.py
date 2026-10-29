"""Agent storage and prompt assembly.

Each agent lives in ~/Axis/agents/<name>/:

    agent.json   settings
    workspace/   the agent's own folder; every file it makes is saved here

Settings:
* system_prompt  who the agent is (sent as the system message)
* config         a list of instructions appended to every message you send
* skills         ids of skills from the skill library (see skills.py)
* model          preferred model ("" means whichever model is loaded)

Deleting an agent deletes its whole folder, including the workspace, and its saved chats.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import chats as chat_lib
from . import runner
from . import skills as skill_lib
from . import tools as tool_lib
from .paths import axis_home, remove_tree, shared_root

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
        chat_lib.rename_agent(agent.name, new_name)
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
    remove_tree(folder)
    if folder.exists():
        raise AgentError(
            "Some of the agent's files could not be deleted. Close any of its files that are open "
            f"in other programs, then delete the agent again. Folder: {folder}"
        )
    chat_lib.delete_for_agent(agent.name)


# ----------------------------------------------------------------- prompts


MAX_MESSAGE_IMAGES = 4
IMAGE_CHARS = 1000  # what one picture counts for against the history budget


def _image_refs(item: Dict[str, Any]) -> List[Dict[str, str]]:
    """The pictures a user message carries, as {place, path}; resolved to files by the server."""
    refs = item.get("images")
    if refs is None or item.get("role") != "user":
        return []
    if not isinstance(refs, list) or len(refs) > MAX_MESSAGE_IMAGES:
        raise AgentError(f"A message can carry at most {MAX_MESSAGE_IMAGES} pictures.")
    out = []
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref["path"].strip():
            raise AgentError("Each picture needs a path.")
        out.append({"place": str(ref.get("place") or ""), "path": ref["path"]})
    return out


def build_messages(agent: Optional[Agent], history: Any) -> List[Dict[str, Any]]:
    """Turn a chat history into the messages sent to the model.

    For an agent, the system message is its system prompt plus enabled skills,
    and its config instructions are appended to the newest user message.
    A user message may carry "images" ({place, path} references), which are kept.
    """
    if not isinstance(history, list) or not history:
        raise AgentError("The conversation is empty.")
    messages: List[Dict[str, Any]] = []
    for item in history[-200:]:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise AgentError("Each message needs a role of 'user' or 'assistant'.")
        content = item.get("content")
        if not isinstance(content, str):
            raise AgentError("Each message needs text content.")
        msg: Dict[str, Any] = {"role": item["role"], "content": content}
        refs = _image_refs(item)
        if refs:
            msg["images"] = refs
        messages.append(msg)
    last = messages[-1]
    if last["role"] != "user" or not (last["content"].strip() or last.get("images")):
        raise AgentError("The last message must be a non-empty user message.")

    # Keep the newest messages that fit the budget so the model's context window
    # is not overflowed (Ollama silently drops the oldest text, including instructions).
    kept, used = [], 0
    for m in reversed(messages):
        used += len(m["content"]) + IMAGE_CHARS * len(m.get("images") or [])
        if kept and used > HISTORY_CHARS:
            break
        kept.append(m)
    messages = list(reversed(kept))
    while len(messages) > 1 and messages[0]["role"] != "user":
        messages.pop(0)

    if agent is None:
        return messages

    names = tool_names(agent)
    uses_files = any(t in names for t in tool_lib.FILE_TOOLS + tool_lib.EXECUTION_TOOLS)
    shared = shared_folder(agent) is not None if uses_files else False
    system = agent.system_prompt + skills_prompt(agent.skills, names, shared)

    if agent.config:
        rules = "\n".join(f"- {line}" for line in agent.config)
        messages[-1] = {
            **messages[-1],
            "content": f"{messages[-1]['content']}\n\n---\nFollow these instructions:\n{rules}",
        }
    return [{"role": "system", "content": system}] + messages


def skills_prompt(skill_ids: List[str], names: List[str], shared: bool = False) -> str:
    """The text an agent's skills add to its system message, after the agent's own system prompt.

    names are the actions the agent has (from its built-in skills); shared says the agent may use the
    shared folder. build_messages and skill_preview both use this, so what the Skills page shows is
    exactly what is sent.
    """
    uses_files = any(t in names for t in tool_lib.FILE_TOOLS + tool_lib.EXECUTION_TOOLS)
    chosen = [skill_lib.get(i) for i in skill_ids if skill_lib.exists(i)]
    # A skill that only changes how file actions work means nothing without them.
    needs_files = {s["id"] for s in skill_lib.BUILTIN if s.get("needs_files")}
    chosen = [s for s in chosen if uses_files or s["id"] not in needs_files]
    text = ""
    if chosen:
        text += "\n\nYou have the following skills. Use them when relevant.\n"
        text += "\n".join(f"\n## {s['name']}\n{s['instructions']}" for s in chosen)
    if uses_files and shared:
        text += "\n\n" + skill_lib.SHARED_WORKSPACE_NOTE
        if "run_python" in names or "run_python_file" in names:
            text += " " + skill_lib.SHARED_PYTHON_NOTE
    elif uses_files:
        text += "\n\n" + skill_lib.WORKSPACE_NOTE
    elif names:
        text += "\n\n" + skill_lib.TOOLS_NOTE
    return text


def skill_preview(skill_id: str) -> Dict[str, Any]:
    """Everything one skill sends to the model, as the page shows it under "What this sends to the model".

    system   the text added to the system message (built by skills_prompt, the same code a chat uses)
    tools    the action definitions sent with the request, in the format Ollama and llama.cpp receive
    tools_text  the same actions written out as text, which is what a model without tool support receives
    notes    plain-language conditions that apply
    """
    skill = skill_lib.get(skill_id)
    actions = list(skill["tools"])
    needs_files = bool(next((s.get("needs_files") for s in skill_lib.BUILTIN if s["id"] == skill_id), False))
    # The Shared folder skill is only sent to an agent that also has a file action, and it changes
    # the folder note; show it the way such an agent receives it.
    names = actions or (["list_files"] if needs_files else [])
    system = skills_prompt([skill_id], names, shared=skill_id == skill_lib.SHARED_SKILL).lstrip("\n")
    notes = []
    if needs_files:
        notes.append("This is only sent to an agent that also has a file or Python skill.")
    if actions:
        notes.append("The closing note about the agent's folder is added once per agent, however many action skills it has.")
    notes.append("The agent's own system prompt comes first, and its config instructions are added to each message "
                 "you send. They are not part of the skill.")
    return {"id": skill["id"], "name": skill["name"], "system": system, "tools": tool_lib.specs(actions),
            "tools_text": runner.text_protocol(actions) if actions else "", "notes": notes}


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


def shared_folder(agent: Optional[Agent]) -> Optional[Path]:
    """The shared folder, if this agent may use it (it has the Shared folder skill)."""
    if agent is None or skill_lib.SHARED_SKILL not in agent.skills:
        return None
    root = shared_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


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
        dirnames[:] = sorted(d for d in dirnames if d not in tool_lib.HIDDEN_NAMES)
        for f in sorted(filenames):
            if f in tool_lib.HIDDEN_NAMES:
                continue
            full = Path(dirpath) / f
            try:
                size = full.stat().st_size
            except OSError:
                continue
            files.append({"path": full.relative_to(root).as_posix(), "size": size})
            if len(files) >= limit:
                return files
    return files
