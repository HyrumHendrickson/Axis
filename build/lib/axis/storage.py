from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AxisPaths:
    home: Path
    state: Path
    logs: Path
    agents: Path
    processes_file: Path


def get_paths() -> AxisPaths:
    axis_home = Path.home() / "Axis"
    state = axis_home / ".Axis"
    logs = state / "logs"
    processes_file = state / "processes.json"
    return AxisPaths(
        home=axis_home,
        state=state,
        logs=logs,
        agents=axis_home,
        processes_file=processes_file,
    )


def ensure_layout(paths: AxisPaths) -> None:
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.state.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)
    if not paths.processes_file.exists():
        write_json(paths.processes_file, {})


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def validate_agent_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("agent name cannot be empty")
    if any(ch in cleaned for ch in r'\\/:*?"<>|'):
        raise ValueError("agent name contains invalid characters")
    if cleaned == ".Axis":
        raise ValueError("agent name cannot be .Axis")
    return cleaned


def agent_dir(paths: AxisPaths, name: str) -> Path:
    return paths.agents / validate_agent_name(name)
