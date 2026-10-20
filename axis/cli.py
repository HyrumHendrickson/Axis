"""Axis command-line interface."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__, agents, backend, worker

MAX_HISTORY = 40  # chat messages kept in context (system prompt is always kept)

CHAT_HELP = """Commands:
  /help    show this help
  /clear   forget the conversation so far
  /model   show the model in use
  /exit    leave the chat (also /quit or Ctrl+D)
Press Ctrl+C while a reply is printing to stop that reply."""


def _out(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _setup_console() -> None:
    # Windows consoles may not support every character a model emits.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _connect_for(args, agent: Optional[agents.Agent]):
    conn = backend.connect(args.url)
    requested = args.model or (agent.model if agent else "") or None
    model = backend.resolve_model(conn, requested)
    prompt = agent.system_prompt if agent else agents.DEFAULT_PROMPT
    return conn, model, prompt


# ------------------------------------------------------------------ commands


def cmd_status(args) -> int:
    conn = backend.connect(args.url)
    print(f"Server:  {conn.label} at {conn.url}")
    print("Loaded models:")
    for i, name in enumerate(conn.models):
        print(f"  {name}" + ("   (default)" if i == 0 else ""))
    print("Axis is ready.")
    return 0


def cmd_ask(args) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        print('Error: give a prompt, e.g. axis ask "What is a monad?"', file=sys.stderr)
        return 2
    agent = agents.load(args.agent) if args.agent else None
    conn, model, system = _connect_for(args, agent)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    backend.chat(conn, model, messages, on_token=_out)
    _out("\n")
    return 0


def cmd_chat(args) -> int:
    agent = agents.load(args.agent) if args.agent else None
    conn, model, system = _connect_for(args, agent)
    who = f"agent '{agent.name}' using " if agent else ""
    print(f"Chatting with {who}{model} on {conn.label}. Type /help for commands, /exit to leave.")

    history: List[dict] = []
    while True:
        try:
            line = input("\nyou> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 0
        if not line:
            continue
        cmd = line.lower()
        if cmd in ("/exit", "/quit", "exit", "quit"):
            return 0
        if cmd == "/help":
            print(CHAT_HELP)
            continue
        if cmd == "/clear":
            history.clear()
            print("Conversation cleared.")
            continue
        if cmd == "/model":
            print(model)
            continue

        history.append({"role": "user", "content": line})
        history = history[-MAX_HISTORY:]
        _out("\nai> ")
        try:
            reply = backend.chat(conn, model, [{"role": "system", "content": system}] + history, on_token=_out)
        except KeyboardInterrupt:
            _out("\n[reply stopped]\n")
            history.pop()
            continue
        except backend.BackendError as exc:
            _out("\n")
            print(f"Error: {exc}", file=sys.stderr)
            history.pop()
            if isinstance(exc, backend.ServerUnavailable):
                return 1
            continue
        _out("\n")
        history.append({"role": "assistant", "content": reply})


def cmd_agent_create(args) -> int:
    agent = agents.create(args.name, args.prompt or "", args.model or "")
    print(f"Created agent '{agent.name}'.")
    print(f"  Folder: {agent.folder}")
    print(f"  Tasks:  put .txt or .md files in {agent.tasks_dir}")
    return 0


def cmd_agent_list(args) -> int:
    items = agents.list_all()
    if not items:
        print("No agents yet. Create one with: axis agent create <name>")
        return 0
    width = max(len(a.name) for a in items)
    for a in items:
        pending = len(worker.pending_tasks(a))
        model = a.model or "(loaded model)"
        print(f"{a.name.ljust(width)}  model: {model}  pending tasks: {pending}")
    return 0


def cmd_agent_show(args) -> int:
    a = agents.load(args.name)
    print(f"Name:          {a.name}")
    print(f"Model:         {a.model or '(whichever model is loaded)'}")
    print(f"Created:       {a.created or '-'}")
    print(f"Folder:        {a.folder}")
    print(f"Pending tasks: {len(worker.pending_tasks(a))}")
    print("System prompt:")
    for line in a.system_prompt.splitlines() or [""]:
        print(f"  {line}")
    return 0


def cmd_agent_edit(args) -> int:
    a = agents.load(args.name)
    changed = False
    if args.prompt is not None:
        a.system_prompt = args.prompt.strip() or agents.DEFAULT_PROMPT
        changed = True
    if args.clear_model:
        a.model = ""
        changed = True
    elif args.model is not None:
        a.model = args.model.strip()
        changed = True
    if not changed:
        print("Nothing to change. Use --prompt, --model, or --clear-model.")
        print(f"You can also edit {a.folder / 'agent.json'} directly.")
        return 1
    a.save()
    print(f"Updated agent '{a.name}'.")
    return 0


def cmd_agent_delete(args) -> int:
    a = agents.load(args.name)
    if not args.yes:
        try:
            answer = input(f"Delete agent '{a.name}' and all its tasks and results? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 1
    agents.delete(a.name)
    print(f"Deleted agent '{a.name}'.")
    return 0


def cmd_run(args) -> int:
    if args.interval <= 0:
        print("Error: --interval must be greater than 0.", file=sys.stderr)
        return 2
    try:
        return worker.run(args.name, args.url, interval=args.interval, once=args.once)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


# -------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axis",
        description="Axis: chat with and run agents on a local model that you have already started "
        "(Ollama or llama.cpp).",
    )
    p.add_argument("--version", action="version", version=f"axis {__version__}")
    p.add_argument("--url", help="model server address (default: auto-detect; env: AXIS_URL)")
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    s = sub.add_parser("status", help="check the server and list loaded models")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("ask", help="ask a single question")
    s.add_argument("prompt", nargs="*", help="the question (or pipe text in)")
    s.add_argument("-a", "--agent", help="answer as this agent")
    s.add_argument("-m", "--model", help="loaded model to use")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("chat", help="start an interactive conversation")
    s.add_argument("-a", "--agent", help="chat with this agent")
    s.add_argument("-m", "--model", help="loaded model to use")
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("run", help="process an agent's task files until Ctrl+C")
    s.add_argument("name", help="agent name")
    s.add_argument("--once", action="store_true", help="process pending tasks once, then exit")
    s.add_argument("--interval", type=float, default=2.0, help="seconds between checks (default 2)")
    s.set_defaults(func=cmd_run)

    ag = sub.add_parser("agent", help="create and manage agents")
    asub = ag.add_subparsers(dest="agent_command", metavar="<action>")
    asub.required = True

    s = asub.add_parser("create", help="create an agent")
    s.add_argument("name")
    s.add_argument("-p", "--prompt", help="system prompt (personality and instructions)")
    s.add_argument("-m", "--model", help="preferred model (default: whichever is loaded)")
    s.set_defaults(func=cmd_agent_create)

    s = asub.add_parser("list", help="list agents")
    s.set_defaults(func=cmd_agent_list)

    s = asub.add_parser("show", help="show an agent's settings")
    s.add_argument("name")
    s.set_defaults(func=cmd_agent_show)

    s = asub.add_parser("edit", help="change an agent's prompt or model")
    s.add_argument("name")
    s.add_argument("-p", "--prompt", help="new system prompt")
    s.add_argument("-m", "--model", help="new preferred model")
    s.add_argument("--clear-model", action="store_true", help="use whichever model is loaded")
    s.set_defaults(func=cmd_agent_edit)

    s = asub.add_parser("delete", help="delete an agent and its files")
    s.add_argument("name")
    s.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    s.set_defaults(func=cmd_agent_delete)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (backend.BackendError, agents.AgentError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
