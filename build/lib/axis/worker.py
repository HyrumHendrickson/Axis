from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .ollama import OllamaError, chat
from .storage import agent_dir, ensure_layout, get_paths, read_json, write_json


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_agent_config(agent_path: Path) -> dict:
    config_path = agent_path / "config.json"
    config = read_json(config_path, {})
    if not isinstance(config, dict):
        config = {}
    return config


def process_tasks(agent_path: Path, config: dict) -> int:
    workshop = agent_path / "workshop"
    tasks_dir = workshop / "tasks"
    results_dir = workshop / "results"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    model = str(config.get("model") or "llama3.2:3b")
    personality = str(config.get("personality") or "You are a concise, reliable assistant.")

    handled = 0
    for task_file in sorted(tasks_dir.glob("*.txt")):
        result_file = results_dir / f"{task_file.stem}.json"
        if result_file.exists():
            continue

        prompt = task_file.read_text(encoding="utf-8", errors="replace")
        payload = {
            "agent": agent_path.name,
            "task": task_file.name,
            "processed_at": now_utc(),
        }
        try:
            response = chat(
                model=model,
                messages=[
                    {"role": "system", "content": personality},
                    {"role": "user", "content": prompt},
                ],
            )
            payload["ok"] = True
            payload["response"] = response
        except OllamaError as exc:
            payload["ok"] = False
            payload["error"] = str(exc)

        result_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        handled += 1

    return handled


def heartbeat(agent_name: str) -> None:
    paths = get_paths()
    state_dir = paths.state / "status"
    state_dir.mkdir(parents=True, exist_ok=True)
    status_file = state_dir / f"{agent_name}.json"
    status_file.write_text(json.dumps({"running": True, "updated_at": now_utc()}, indent=2), encoding="utf-8")


def run_loop(agent_name: str, interval: float) -> None:
    paths = get_paths()
    ensure_layout(paths)
    agent_path = agent_dir(paths, agent_name)
    if not agent_path.exists():
        raise SystemExit(f"Agent '{agent_name}' does not exist")

    while True:
        config = load_agent_config(agent_path)
        process_tasks(agent_path, config)
        heartbeat(agent_name)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Axis worker process")
    parser.add_argument("--agent", required=True, help="agent name")
    parser.add_argument("--interval", type=float, default=2.0, help="poll interval seconds")
    args = parser.parse_args()
    run_loop(args.agent, args.interval)


if __name__ == "__main__":
    main()
