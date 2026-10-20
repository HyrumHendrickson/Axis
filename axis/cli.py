from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .ollama import OllamaError, chat, list_downloaded_models, list_online_models, pull_model, remove_model
from .runtime import is_running, load_processes, save_processes, start_agent_process, stop_agent_process, utc_now
from .storage import agent_dir, ensure_layout, get_paths, read_json, validate_agent_name, write_json


def _agents(paths) -> list[str]:
    names: list[str] = []
    for child in sorted(paths.home.iterdir()):
        if child.name == ".Axis":
            continue
        if child.is_dir():
            names.append(child.name)
    return names


def cmd_create(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)
    root = agent_dir(paths, name)
    if root.exists():
        print(f"Agent '{name}' already exists")
        return 1

    (root / "workshop" / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "workshop" / "results").mkdir(parents=True, exist_ok=True)
    write_json(
        root / "config.json",
        {
            "name": name,
            "model": "llama3.2:3b",
            "personality": "You are a concise and helpful assistant.",
        },
    )
    write_json(root / "tools.json", {"tools": []})
    print(f"Created agent '{name}' at {root}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)
    root = agent_dir(paths, name)
    if not root.exists():
        print(f"Agent '{name}' does not exist")
        return 1

    processes = load_processes(paths)
    info = processes.get(name)
    if isinstance(info, dict):
        pid = int(info.get("pid", 0))
        if is_running(pid):
            stop_agent_process(pid)
        processes.pop(name, None)
        save_processes(paths, processes)

    shutil.rmtree(root)
    print(f"Deleted agent '{name}'")
    return 0


def cmd_list_agents(_: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    names = _agents(paths)
    if not names:
        print("No agents created")
        return 0
    for name in names:
        print(name)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)
    root = agent_dir(paths, name)
    if not root.exists():
        print(f"Agent '{name}' does not exist")
        return 1

    processes = load_processes(paths)
    current = processes.get(name)
    if isinstance(current, dict):
        pid = int(current.get("pid", 0))
        if is_running(pid):
            print(f"Agent '{name}' already running (pid {pid})")
            return 0

    pid = start_agent_process(paths, name)
    processes[name] = {"pid": pid, "started_at": utc_now()}
    save_processes(paths, processes)
    print(f"Started agent '{name}' (pid {pid})")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)

    processes = load_processes(paths)
    info = processes.get(name)
    if not isinstance(info, dict):
        print(f"Agent '{name}' is not running")
        return 0

    pid = int(info.get("pid", 0))
    stop_agent_process(pid)
    processes.pop(name, None)
    save_processes(paths, processes)
    print(f"Stopped agent '{name}'")
    return 0


def cmd_ps(_: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    processes = load_processes(paths)
    if not processes:
        print("No running agents")
        return 0

    stale: list[str] = []
    print("AGENT\tPID\tSTATUS\tSTARTED")
    for name, info in sorted(processes.items()):
        if not isinstance(info, dict):
            stale.append(name)
            continue
        pid = int(info.get("pid", 0))
        started = str(info.get("started_at", "-"))
        running = is_running(pid)
        status = "running" if running else "stale"
        print(f"{name}\t{pid}\t{status}\t{started}")
        if not running:
            stale.append(name)

    if stale:
        for name in stale:
            processes.pop(name, None)
        save_processes(paths, processes)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)
    root = agent_dir(paths, name)
    if not root.exists():
        print(f"Agent '{name}' does not exist")
        return 1

    print(f"config: {root / 'config.json'}")
    print(f"tools: {root / 'tools.json'}")
    print(f"workshop: {root / 'workshop'}")
    return 0


def _load_agent_config(paths, name: str) -> tuple[Path, dict]:
    root = agent_dir(paths, name)
    if not root.exists():
        raise ValueError(f"Agent '{name}' does not exist")
    config_path = root / "config.json"
    config = read_json(config_path, {})
    if not isinstance(config, dict):
        config = {}
    return config_path, config


def cmd_model_set(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)
    model = args.model.strip()
    if not model:
        print("Model name is required")
        return 1
    try:
        config_path, config = _load_agent_config(paths, name)
    except ValueError as exc:
        print(str(exc))
        return 1
    config["name"] = name
    config["model"] = model
    config.setdefault("personality", "You are a concise and helpful assistant.")
    write_json(config_path, config)
    print(f"Agent '{name}' model set to '{model}'")
    return 0


def cmd_model_show(args: argparse.Namespace) -> int:
    paths = get_paths()
    ensure_layout(paths)
    name = validate_agent_name(args.name)
    try:
        _, config = _load_agent_config(paths, name)
    except ValueError as exc:
        print(str(exc))
        return 1
    model = str(config.get("model") or "llama3.2:3b")
    print(model)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    model = args.model.strip()
    prompt = " ".join(args.prompt).strip()
    if not model:
        print("Model name is required")
        return 1
    if not prompt:
        print("Prompt is required")
        return 1
    try:
        response = chat(model=model, messages=[{"role": "user", "content": prompt}])
    except OllamaError as exc:
        print(f"Error: {exc}")
        return 1
    print(response)
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    model = args.model.strip()
    if not model:
        print("Model name is required")
        return 1
    print(f"Starting chat with '{model}'. Type 'exit' or 'quit' to stop.")
    messages: list[dict[str, str]] = []
    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            break
        messages.append({"role": "user", "content": prompt})
        try:
            response = chat(model=model, messages=messages)
        except OllamaError as exc:
            print(f"Error: {exc}")
            return 1
        messages.append({"role": "assistant", "content": response})
        print(f"{model}> {response}")
    return 0


def cmd_list_downloaded(_: argparse.Namespace) -> int:
    try:
        models = list_downloaded_models()
    except OllamaError as exc:
        print(f"Error: {exc}")
        return 1

    if not models:
        print("No downloaded models")
        return 0

    for model in models:
        name = model.get("name", "unknown")
        size = model.get("size", 0)
        print(f"{name}\t{size}")
    return 0


def cmd_list_online(_: argparse.Namespace) -> int:
    try:
        models = list_online_models(limit=100)
    except OllamaError as exc:
        print(f"Error: {exc}")
        return 1

    if not models:
        print("No models found")
        return 0

    for model in models:
        print(model)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    name = args.name.strip()
    if not name:
        print("Model name is required")
        return 1
    try:
        pull_model(name)
    except OllamaError as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Downloaded model '{name}'")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    name = args.name.strip()
    if not name:
        print("Model name is required")
        return 1
    try:
        remove_model(name)
    except OllamaError as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Removed model '{name}'")
    return 0


def cmd_update(_: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "axis-agent-manager"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print("Axis update failed")
        print(result.stderr.strip() or result.stdout.strip())
        return result.returncode
    print("Axis updated successfully")
    return 0


def cmd_uninstall(_: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", "axis-agent-manager"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print("Axis uninstall failed")
        print(result.stderr.strip() or result.stdout.strip())
        return result.returncode
    print("Axis uninstalled")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="axis", description="Axis lightweight AI agent manager")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("update", help="Update Axis to latest version").set_defaults(func=cmd_update)
    sub.add_parser("uninstall", help="Uninstall Axis").set_defaults(func=cmd_uninstall)
    sub.add_parser("ps", help="List running agents").set_defaults(func=cmd_ps)

    list_parser = sub.add_parser("list", help="List models or agents")
    list_sub = list_parser.add_subparsers(dest="list_command", required=True)
    list_sub.add_parser("online", help="List models available online").set_defaults(func=cmd_list_online)
    list_sub.add_parser("downloaded", help="List downloaded models").set_defaults(func=cmd_list_downloaded)
    list_sub.add_parser("agents", help="List created agents").set_defaults(func=cmd_list_agents)

    get_parser = sub.add_parser("get", help="Download a model")
    get_parser.add_argument("name")
    get_parser.set_defaults(func=cmd_get)
    download_parser = sub.add_parser("download", help="Download a model")
    download_parser.add_argument("name")
    download_parser.set_defaults(func=cmd_get)

    remove_parser = sub.add_parser("remove", help="Remove a downloaded model")
    remove_parser.add_argument("name")
    remove_parser.set_defaults(func=cmd_remove)

    create_parser = sub.add_parser("create", help="Create an agent")
    create_parser.add_argument("name")
    create_parser.set_defaults(func=cmd_create)

    delete_parser = sub.add_parser("delete", help="Delete an agent")
    delete_parser.add_argument("name")
    delete_parser.set_defaults(func=cmd_delete)

    start_parser = sub.add_parser("start", help="Start an agent")
    start_parser.add_argument("name")
    start_parser.set_defaults(func=cmd_start)

    stop_parser = sub.add_parser("stop", help="Stop an agent")
    stop_parser.add_argument("name")
    stop_parser.set_defaults(func=cmd_stop)

    edit_parser = sub.add_parser("edit", help="Print paths to agent config and tools")
    edit_parser.add_argument("name")
    edit_parser.set_defaults(func=cmd_edit)

    model_parser = sub.add_parser("model", help="Set or show an agent model")
    model_sub = model_parser.add_subparsers(dest="model_command", required=True)
    model_set_parser = model_sub.add_parser("set", help="Set an agent model")
    model_set_parser.add_argument("name")
    model_set_parser.add_argument("model")
    model_set_parser.set_defaults(func=cmd_model_set)
    model_show_parser = model_sub.add_parser("show", help="Show an agent model")
    model_show_parser.add_argument("name")
    model_show_parser.set_defaults(func=cmd_model_show)

    ask_parser = sub.add_parser("ask", help="Send one prompt to a model")
    ask_parser.add_argument("model")
    ask_parser.add_argument("prompt", nargs="+")
    ask_parser.set_defaults(func=cmd_ask)

    chat_parser = sub.add_parser("chat", help="Open an interactive conversation with a model")
    chat_parser.add_argument("model")
    chat_parser.set_defaults(func=cmd_chat)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
