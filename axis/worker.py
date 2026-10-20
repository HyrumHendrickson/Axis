"""`axis run <agent>`: process task files in the foreground until Ctrl+C."""

from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import agents, backend

TASK_SUFFIXES = (".txt", ".md")


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def pending_tasks(agent: agents.Agent) -> List[Path]:
    if not agent.tasks_dir.is_dir():
        return []
    return sorted(
        p for p in agent.tasks_dir.iterdir()
        if p.is_file() and p.suffix.lower() in TASK_SUFFIXES and not p.name.startswith(".")
    )


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.stem}-{stamp}{path.suffix}")


def _move(task: Path, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(task), str(_unique(folder / task.name)))


def process_task(agent: agents.Agent, conn: backend.Backend, model: str, task: Path) -> bool:
    """Return True on success. Raises ServerUnavailable so the caller can wait and retry."""
    prompt = task.read_text(encoding="utf-8", errors="replace").strip()
    agent.results_dir.mkdir(parents=True, exist_ok=True)
    if not prompt:
        _unique(agent.results_dir / f"{task.stem}.error.txt").write_text("Task file was empty.\n", encoding="utf-8")
        _move(task, agent.tasks_dir / "failed")
        log(f"{task.name}: empty, moved to tasks/failed")
        return False

    log(f"{task.name}: working ({model})")
    messages = [
        {"role": "system", "content": agent.system_prompt},
        {"role": "user", "content": prompt},
    ]
    try:
        reply = backend.chat(conn, model, messages)
    except backend.ServerUnavailable:
        raise
    except backend.BackendError as exc:
        _unique(agent.results_dir / f"{task.stem}.error.txt").write_text(f"{exc}\n", encoding="utf-8")
        _move(task, agent.tasks_dir / "failed")
        log(f"{task.name}: failed ({exc}), moved to tasks/failed")
        return False

    result = _unique(agent.results_dir / f"{task.stem}.md")
    header = f"<!-- agent: {agent.name} | model: {model} | {datetime.now().isoformat(timespec='seconds')} -->\n\n"
    result.write_text(header + reply.strip() + "\n", encoding="utf-8")
    _move(task, agent.tasks_dir / "done")
    log(f"{task.name}: done -> results/{result.name}")
    return True


def run(agent_name: str, url: Optional[str], interval: float = 2.0, once: bool = False) -> int:
    agent = agents.load(agent_name)
    agent.save()  # make sure all folders exist
    conn = backend.connect(url)  # gate: refuse to start without a loaded model
    backend.resolve_model(conn, agent.model or None)

    log(f"Agent '{agent.name}' watching {agent.tasks_dir}")
    if not once:
        log("Press Ctrl+C to stop.")

    while True:
        tasks = pending_tasks(agent)
        if tasks:
            try:
                agent = agents.load(agent.name)  # pick up edits made while running
                conn = backend.connect(url)
                model = backend.resolve_model(conn, agent.model or None)
                for task in tasks:
                    if task.exists():
                        process_task(agent, conn, model, task)
            except backend.BackendError as exc:
                if once:
                    raise
                log(f"Waiting for model server: {str(exc).splitlines()[0]}")
                time.sleep(max(interval, 5.0))
                continue
        if once:
            return 0
        time.sleep(interval)
