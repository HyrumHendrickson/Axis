"""Runs one chat reply, letting an agent use its tools along the way.

Native tool calling is used when the server and model support it. If they
reject tools, Axis switches to a plain-text format the model writes itself:

    ```tool
    {"name": "write_file", "arguments": {"path": "a.txt", "content": "hi"}}
    ```

Events sent to `emit` (one dict each):
    {"t": text}                                   reply text as it is written
    {"tool": {"id", "name", "args"}}              a tool is about to run
    {"tool_result": {"id", "ok", "output"}}       the tool finished
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import backend, tools

MAX_STEPS = 10
_TEXT_MODE: Set[Tuple[str, str]] = set()  # (server url, model) pairs that rejected native tools

FENCE = "```tool"
TOOL_BLOCK = re.compile(r"```tool[^\n]*\n?(.*?)(?:```|$)", re.DOTALL)


def text_protocol(tool_names: List[str]) -> str:
    lines = [
        "To use a tool, reply with exactly one block in this format and nothing after it:",
        "```tool",
        '{"name": "TOOL_NAME", "arguments": {...}}',
        "```",
        "The arguments must be valid JSON (escape newlines in strings as \\n). "
        "You will then receive the result and can continue. When you are finished, answer normally without a tool block.",
        "Available tools:",
    ]
    for name in tool_names:
        spec = tools.TOOL_SPECS[name]
        props = spec["parameters"]["properties"]
        args = ", ".join(f'"{k}": {v["description"]}' for k, v in props.items()) or "no arguments"
        lines.append(f"- {name}: {spec['description']} Arguments: {args}")
    return "\n".join(lines)


def parse_tool_block(text: str) -> Optional[Tuple[str, Any]]:
    """Find a ```tool block. Returns (name, arguments) or ("", error message) if malformed."""
    m = TOOL_BLOCK.search(text)
    if not m:
        return None
    body = m.group(1).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return "", f"Your tool block was not valid JSON ({exc.msg}). Write it again as valid JSON."
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return "", 'A tool block must be a JSON object like {"name": "...", "arguments": {...}}.'
    args = data.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    return data["name"], args


class HideToolBlocks:
    """Passes text through to the user but holds back a ```tool block."""

    def __init__(self, out: Callable[[str], None]):
        self.out, self.buf, self.sent, self.hidden = out, "", 0, False

    def feed(self, text: str) -> None:
        self.buf += text
        if self.hidden:
            return
        i = self.buf.find(FENCE, self.sent)
        if i >= 0:
            self._send(i)
            self.hidden = True
            return
        keep = 0  # hold back a partial "```tool" at the end
        for k in range(min(len(FENCE) - 1, len(self.buf) - self.sent), 0, -1):
            if FENCE.startswith(self.buf[-k:]):
                keep = k
                break
        self._send(len(self.buf) - keep)

    def flush(self) -> None:
        if not self.hidden:
            self._send(len(self.buf))

    def _send(self, end: int) -> None:
        if end > self.sent:
            self.out(self.buf[self.sent:end])
            self.sent = end


def _run_tool(workspace: Path, name: str, args: Any, allowed: List[str], call_id: str, emit) -> str:
    emit({"tool": {"id": call_id, "name": name, "args": args if isinstance(args, dict) else {"raw": str(args)}}})
    if not isinstance(args, dict):
        ok, output = False, "Error: the arguments were not valid JSON. Call the tool again with a JSON object."
    else:
        ok, output = tools.execute(workspace, name, args, allowed)
    emit({"tool_result": {"id": call_id, "ok": ok, "output": output}})
    return output


def reply(
    conn: backend.Backend,
    model: str,
    messages: List[Dict[str, Any]],
    emit: Callable[[Dict[str, Any]], None],
    tool_names: Optional[List[str]] = None,
    workspace: Optional[Path] = None,
) -> None:
    """Produce one reply, running tools as the model asks for them."""
    say = lambda t: emit({"t": t})  # noqa: E731
    if not tool_names or workspace is None:
        backend.complete(conn, model, messages, say)
        return

    messages = list(messages)
    key = (conn.url, model)
    text_mode = key in _TEXT_MODE
    if text_mode:
        messages[0] = {**messages[0], "content": messages[0]["content"] + "\n\n" + text_protocol(tool_names)}
    specs = tools.specs(tool_names)

    step = 0
    while step < MAX_STEPS:
        if not text_mode:
            try:
                turn = backend.complete(conn, model, messages, say, tools=specs)
            except backend.ToolsUnsupported:
                _TEXT_MODE.add(key)
                text_mode = True
                messages[0] = {**messages[0], "content": messages[0]["content"] + "\n\n" + text_protocol(tool_names)}
                continue
            if not turn.tool_calls:
                return
            messages.append(backend.assistant_message(conn, turn))
            for call in turn.tool_calls:
                output = _run_tool(workspace, call.name, call.arguments, tool_names, f"{step}-{call.id}", emit)
                messages.append(backend.tool_message(conn, call, output))
        else:
            hider = HideToolBlocks(say)
            turn = backend.complete(conn, model, messages, hider.feed)
            hider.flush()
            found = parse_tool_block(turn.text)
            if found is None:
                return
            name, args = found
            messages.append({"role": "assistant", "content": turn.text})
            if not name:
                output = str(args)
            else:
                output = _run_tool(workspace, name, args, tool_names, f"{step}-text", emit)
            messages.append({"role": "user", "content": f"Result of {name or 'tool'}:\n{output}\n\n"
                             "Continue. Use another tool block if needed, otherwise give your answer."})
        step += 1
    say(f"\n\n(Stopped after {MAX_STEPS} tool steps. Send another message to let the agent continue.)")
