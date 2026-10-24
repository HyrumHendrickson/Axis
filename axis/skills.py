"""The skill library shared by all agents.

Built-in skills give an agent actions (tools): working with files in its own
folder, running Python, searching the web and reading Wikipedia. The Shared
folder skill adds no tools of its own; it lets the file actions an agent already
has also reach ~/Axis/shared, which every agent with that skill can use.
Custom skills are named instructions written by the user; they are stored as
~/Axis/skills/<id>.json. Agents choose skills by id.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import axis_home

MAX_TEXT = 20000
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

TOOLS_NOTE = (
    "Use a tool only when the request needs one; for ordinary conversation, just reply. "
    "After using tools, tell the user what you did and name any files you created."
)
WORKSPACE_NOTE = (
    "You have your own folder for files. All paths are relative to that folder "
    "(for example: notes/todo.txt). You cannot reach files outside it. " + TOOLS_NOTE
)
SHARED_WORKSPACE_NOTE = (
    "You have your own folder for files, and a shared folder that other agents also use. "
    "Paths are relative to your own folder (for example: notes/todo.txt). To use the shared folder, "
    "start the path with shared/ (for example: shared/plan.md; use shared to list it). "
    "You cannot reach any other files. " + TOOLS_NOTE
)
SHARED_PYTHON_NOTE = (
    "In run_python, your own folder is the working directory and the shared folder's full path is "
    "os.environ['AXIS_SHARED_DIR']."
)
SHARED_SKILL = "shared-folder"

BUILTIN: List[Dict[str, Any]] = [
    {
        "id": "run-python",
        "name": "Run Python",
        "description": "Write and run Python code in the agent's folder, for calculations, data work and file processing.",
        "instructions": "When a task needs calculation, data processing or checking an answer, write a complete Python "
        "program and run it with run_python. Print the results you need. Starting other programs is not allowed.",
        "tools": ["run_python"],
    },
    {
        "id": "list-files",
        "name": "List files",
        "description": "See which files and folders are in the agent's folder.",
        "instructions": "Use list_files to see what is in your folder before reading or changing files.",
        "tools": ["list_files"],
    },
    {
        "id": "read-files",
        "name": "Read files",
        "description": "Open and read text files in the agent's folder.",
        "instructions": "Use read_file to read a file before summarizing or editing it.",
        "tools": ["read_file"],
    },
    {
        "id": "write-files",
        "name": "Make files",
        "description": "Create and overwrite text files and folders in the agent's folder.",
        "instructions": "Use write_file to save work (notes, code, reports) and make_folder to organize it. "
        "Tell the user the file names you created.",
        "tools": ["write_file", "make_folder"],
    },
    {
        "id": "delete-files",
        "name": "Delete files",
        "description": "Delete files and empty folders in the agent's folder.",
        "instructions": "Use delete_file only when the user asks you to remove something.",
        "tools": ["delete_file"],
    },
    {
        "id": "web-search",
        "name": "Web search",
        "description": "Search the web and read web pages. The agent chooses how much text to get back.",
        "instructions": "Use web_search for current events, facts you are unsure of, or anything after your training. "
        "Then use fetch_page on the most relevant link to read it. Both take max_chars: ask for only as much "
        "text as you need (small for a quick fact, larger for detail), and use start to read further into a long "
        "page. Say which sites your answer came from.",
        "tools": ["web_search", "fetch_page"],
    },
    {
        "id": "wikipedia",
        "name": "Wikipedia",
        "description": "Look up and read Wikipedia articles through the Wikipedia API.",
        "instructions": "Use wikipedia_search to find the exact article title, then wikipedia_article to read it. "
        "Set intro_only to true when a summary is enough. Use max_chars to limit how much text comes back and "
        "start to continue a long article. Mention the article you used.",
        "tools": ["wikipedia_search", "wikipedia_article"],
    },
    {
        "id": SHARED_SKILL,
        "name": "Shared folder",
        "description": "Let the agent's file actions also reach a folder shared by every agent with this skill, "
        "so agents can pass files and messages to each other.",
        "instructions": "There is a shared folder that other agents can read and change. Put paths in it under "
        "shared/ (for example shared/notes.md). Use it to leave files or messages for other agents and to pick "
        "up theirs. Other agents may change shared files at any time, so read a shared file again before "
        "relying on it, and do not overwrite another agent's file unless asked. Name messages clearly, "
        "for example shared/inbox/to-writer.md.",
        "tools": [],
        "needs_files": True,
    },
]
BUILTIN_IDS = {s["id"] for s in BUILTIN}


class SkillError(Exception):
    pass


def skills_root() -> Path:
    return axis_home() / "skills"


def _text(value: Any, label: str, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SkillError(f"{label} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise SkillError(f"{label} is too long (limit {limit} characters).")
    return value


def _public(skill: Dict[str, Any], builtin: bool) -> Dict[str, Any]:
    return {
        "id": skill["id"],
        "name": skill.get("name", skill["id"]),
        "description": skill.get("description", ""),
        "instructions": skill.get("instructions", ""),
        "tools": list(skill.get("tools", [])),
        "builtin": builtin,
        "created": skill.get("created", ""),
    }


def _path(skill_id: str) -> Path:
    if not ID_RE.fullmatch(skill_id or ""):
        raise SkillError(f"Skill '{skill_id}' does not exist.")
    return skills_root() / f"{skill_id}.json"


def list_all() -> List[Dict[str, Any]]:
    result = [_public(s, True) for s in BUILTIN]
    root = skills_root()
    if root.is_dir():
        custom = []
        for f in root.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == f.stem and f.stem not in BUILTIN_IDS:
                    custom.append(_public(data, False))
            except (OSError, json.JSONDecodeError):
                continue
        result += sorted(custom, key=lambda s: s["name"].lower())
    return result


def get(skill_id: str) -> Dict[str, Any]:
    for s in BUILTIN:
        if s["id"] == skill_id:
            return _public(s, True)
    path = _path(skill_id)
    if not path.is_file():
        raise SkillError(f"Skill '{skill_id}' does not exist.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillError(f"Could not read {path}: {exc}") from exc
    return _public(data, False)


def exists(skill_id: str) -> bool:
    try:
        get(skill_id)
        return True
    except SkillError:
        return False


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "skill"
    return slug if slug[0].isalnum() else "s" + slug


def _save(data: Dict[str, Any]) -> None:
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{data['id']}.json.tmp"
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, root / f"{data['id']}.json")


def _fields(data: Dict[str, Any]) -> Dict[str, str]:
    name = _text(data.get("name"), "Name", 80)
    if not name:
        raise SkillError("Give the skill a name.")
    instructions = _text(data.get("instructions"), "Instructions")
    if not instructions:
        raise SkillError("Give the skill some instructions.")
    return {"name": name, "description": _text(data.get("description"), "Description", 300), "instructions": instructions}


def create(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise SkillError("Request body must be a JSON object.")
    fields = _fields(data)
    taken = {s["id"] for s in list_all()}
    if any(s["name"].lower() == fields["name"].lower() for s in list_all()):
        raise SkillError(f"A skill named '{fields['name']}' already exists.")
    base = _slug(fields["name"])
    skill_id, n = base, 2
    while skill_id in taken:
        skill_id, n = f"{base}-{n}", n + 1
    record = {"id": skill_id, **fields, "created": datetime.now().isoformat(timespec="seconds")}
    _save(record)
    return _public(record, False)


def update(skill_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if skill_id in BUILTIN_IDS:
        raise SkillError("Built-in skills cannot be changed.")
    current = get(skill_id)
    fields = _fields({**current, **(data if isinstance(data, dict) else {})})
    if any(s["id"] != skill_id and s["name"].lower() == fields["name"].lower() for s in list_all()):
        raise SkillError(f"A skill named '{fields['name']}' already exists.")
    record = {"id": skill_id, **fields, "created": current.get("created", "")}
    _save(record)
    return _public(record, False)


def delete(skill_id: str) -> None:
    if skill_id in BUILTIN_IDS:
        raise SkillError("Built-in skills cannot be deleted.")
    path = _path(skill_id)
    if not path.is_file():
        raise SkillError(f"Skill '{skill_id}' does not exist.")
    path.unlink()


def find_or_create(name: str, instructions: str) -> Optional[str]:
    """Used when upgrading agents from Axis 2.0, whose skills were stored inside each agent."""
    name, instructions = (name or "").strip()[:80], (instructions or "").strip()
    if not name or not instructions:
        return None
    for s in list_all():
        if s["name"].lower() == name.lower():
            return s["id"]
    return create({"name": name, "instructions": instructions})["id"]
