"""Agent storage. Each agent is a folder under ~/Axis/agents/<name>/:

    agent.json        name, system prompt, optional preferred model
    tasks/            drop .txt or .md files here for `axis run`
    tasks/done/       processed task files are moved here
    tasks/failed/     task files that could not be processed
    results/          one result file per task
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List

DEFAULT_PROMPT = "You are a concise and helpful assistant."
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class AgentError(Exception):
    pass


def axis_home() -> Path:
    override = os.environ.get("AXIS_HOME")
    return Path(override).expanduser() if override else Path.home() / "Axis"


def agents_root() -> Path:
    return axis_home() / "agents"


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise AgentError(
            f"Invalid agent name '{name}'. Use letters, numbers, '-' or '_' "
            "(must start with a letter or number, max 64 characters)."
        )
    return name


@dataclass
class Agent:
    name: str
    system_prompt: str = DEFAULT_PROMPT
    model: str = ""  # empty means "use whichever model is loaded"
    created: str = ""

    @property
    def folder(self) -> Path:
        return agents_root() / self.name

    @property
    def tasks_dir(self) -> Path:
        return self.folder / "tasks"

    @property
    def results_dir(self) -> Path:
        return self.folder / "results"

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for sub in (self.tasks_dir, self.tasks_dir / "done", self.tasks_dir / "failed", self.results_dir):
            sub.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "agent.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, self.folder / "agent.json")


def create(name: str, system_prompt: str = "", model: str = "") -> Agent:
    name = validate_name(name)
    if (agents_root() / name).exists():
        raise AgentError(f"Agent '{name}' already exists.")
    agent = Agent(
        name=name,
        system_prompt=system_prompt.strip() or DEFAULT_PROMPT,
        model=model.strip(),
        created=datetime.now().isoformat(timespec="seconds"),
    )
    agent.save()
    return agent


def load(name: str) -> Agent:
    name = validate_name(name)
    path = agents_root() / name / "agent.json"
    if not path.is_file():
        raise AgentError(f"Agent '{name}' does not exist. Create it with: axis agent create {name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError(f"{path} is not a valid agent file.")
    return Agent(
        name=name,
        system_prompt=str(data.get("system_prompt") or DEFAULT_PROMPT),
        model=str(data.get("model") or ""),
        created=str(data.get("created") or ""),
    )


def list_all() -> List[Agent]:
    root = agents_root()
    if not root.is_dir():
        return []
    agents = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and (child / "agent.json").is_file() and NAME_RE.fullmatch(child.name):
            try:
                agents.append(load(child.name))
            except AgentError:
                continue
    return agents


def delete(name: str) -> None:
    agent = load(name)
    folder = agent.folder.resolve()
    root = agents_root().resolve()
    if folder.parent != root:
        raise AgentError("Refusing to delete a folder outside the Axis agents directory.")
    shutil.rmtree(folder)
