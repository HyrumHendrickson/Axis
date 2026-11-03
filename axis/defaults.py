"""The example agents, and the apps they need.

**Generate default agents** (Settings, or the Agents page when there are no agents yet) makes four agents that
work together: MainAgent splits a request into tasks and calls the other three, WebSearchSubAgent, CodeWriterSubAgent
and DocumentEditorSubAgent. It also starts installing the two Art Craft items those agents use, Rust and WordCraft,
unless they are already installed.

An agent that already has one of these names is left exactly as it is: nothing the user made is replaced.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import agents, artcraft, jobs

MAIN = "MainAgent"
APPS = (("rust", "Rust"), ("wordcraft", "WordCraft"))   # installed on the Art Craft page

DEFAULT_AGENTS: List[Dict[str, Any]] = [
    {
        "name": MAIN,
        "system_prompt": "You are an executive assistant who will answer requests by splitting them into small tasks and "
        "calling another agent to do each task. Make sure each agent saves their work in a file of some kind so you can "
        "share the file path with the other agents who need to use that work.",
        "skills": ["share-file", "call-agent"],
        "can_call": [
            {"name": "WebSearchSubAgent", "description": "Call WebSearchSubAgent whenever you need to get information from the web."},
            {"name": "CodeWriterSubAgent", "description": "Call CodeWriterSubAgent whenever you need to have code written or a figure produced."},
            {"name": "DocumentEditorSubAgent", "description": "Call DocumentEditorSubAgent whenever you need to have a professional document or report created."},
        ],
    },
    {
        "name": "WebSearchSubAgent",
        "system_prompt": "You are an assistant who will complete web search tasks for the executive assistant, make sure to "
        "save all the required information in a text file and to return the file path.",
        "skills": ["web-search", "wikipedia", "run-terminal"],
    },
    {
        "name": "CodeWriterSubAgent",
        "system_prompt": "You are an assistant who will complete code writing tasks for the executive assistant, make sure "
        "to return the file path of all files you create.",
        "skills": ["run-python", "run-rust", "run-terminal"],
    },
    {
        "name": "DocumentEditorSubAgent",
        "system_prompt": "You are an assistant who will complete document creation tasks for the executive assistant, make "
        "sure to return the file path to all documents you create.",
        "skills": ["run-terminal", "wordcraft"],
    },
]


def _start_installs() -> List[Dict[str, str]]:
    """Start the install of each app that is missing. One that cannot start is reported, never raised."""
    state = artcraft.status()
    have = {a["id"]: a["installed"] for a in state["apps"]}
    have["rust"] = state["rust"]["installed"]
    results: List[Dict[str, str]] = []
    for app_id, label in APPS:
        if have.get(app_id):
            results.append({"app": app_id, "name": label, "state": "installed"})
            continue
        try:
            artcraft.install({"app": app_id})
            results.append({"app": app_id, "name": label, "state": "started"})
        except jobs.JobError:
            results.append({"app": app_id, "name": label, "state": "started"})   # an install is already running
        except artcraft.ArtCraftError as exc:
            results.append({"app": app_id, "name": label, "state": "unavailable", "message": str(exc)})
    return results


def generate() -> Dict[str, Any]:
    """Make the example agents that do not exist yet, and start installing Rust and WordCraft."""
    created: List[str] = []
    skipped: List[str] = []
    for spec in DEFAULT_AGENTS:
        if (agents.agents_root() / spec["name"]).exists():
            skipped.append(spec["name"])
            continue
        agents.create(spec)
        created.append(spec["name"])
    return {"created": created, "skipped": skipped, "installs": _start_installs()}
