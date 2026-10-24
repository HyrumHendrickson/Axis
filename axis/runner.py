"""Runs one chat reply, letting an agent use its tools along the way.

Native tool calling is used when the server and model support it. If they
reject tools, Axis switches to a plain-text format the model writes itself:

    ```tool
    {"name": "write_file", "arguments": {"path": "a.txt", "content": "hi"}}
    ```

Small models (Llama 3.x in particular) sometimes write a tool call as JSON text
instead of a structured call. Those are recognised and run too, and the page is
told to replace the raw JSON with the action.

Events sent to `emit` (one dict each):
    {"round": n}                                  a new model turn starts
    {"t": text}                                   reply text as it is written
    {"rewrite": text}                             replace this round's text (a tool call was removed)
    {"tool": {"id", "name", "args"}}              a tool is about to run
    {"tool_result": {"id", "ok", "output"}}       the tool finished
    {"stats": {"prompt_tokens", "output_tokens", "seconds"}}   token counts for the turn just finished
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import backend, tools

MAX_STEPS = 10
TOOL_TEMPERATURE = 0.2  # steadier tool calls from small models
_TEXT_MODE: Set[Tuple[str, str]] = set()  # (server url, model) pairs that rejected native tools

FENCE = "```tool"
TOOL_BLOCK = re.compile(r"```tool[^\n]*\n?(.*?)(?:```|$)", re.DOTALL)
FINAL_REQUEST = ("Stop using tools now. Using the results above, give the user your final answer, "
                 "including the names of any files you created.")


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


def _loads_maybe(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


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
    return data["name"], _loads_maybe(data.get("arguments", data.get("parameters", {})))


def _call_from_object(obj: Any, allowed: List[str]) -> Optional[Tuple[str, Any]]:
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
    name = fn.get("name") or obj.get("tool") or obj.get("tool_name")
    if not isinstance(name, str):
        return None
    args = fn.get("arguments", fn.get("parameters", fn.get("args", fn.get("input", {}))))
    name, args = tools.normalize_call(name, _loads_maybe(args), allowed)
    return (name, args) if name in allowed else None


def find_text_call(text: str, allowed: List[str]) -> Optional[Tuple[str, Any, str]]:
    """Find a tool call written as JSON text. Returns (name, arguments, text without the call)."""
    decoder = json.JSONDecoder()
    starts = [i for i, ch in enumerate(text) if ch == "{"][:200]
    for i in starts:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        call = _call_from_object(obj, allowed)
        if call:
            rest = text[:i] + text[end:]
            rest = rest.replace("<|python_tag|>", "")
            rest = re.sub(r"```[a-zA-Z]*\s*```", "", rest)
            return call[0], call[1], rest.strip()
    return None


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


class Session:
    def __init__(self, workspace: Path, allowed: List[str], emit, shared: Optional[Path] = None):
        self.workspace, self.allowed, self.emit, self.shared = workspace, allowed, emit, shared
        self.done: Dict[str, str] = {}  # call signature -> result
        self.repeats = 0

    def run(self, name: str, args: Any, call_id: str) -> str:
        name, args = tools.normalize_call(name, args, self.allowed)
        shown = args if isinstance(args, dict) else {"raw": str(args)}
        sig = name + json.dumps(shown, sort_keys=True, default=str)
        self.emit({"tool": {"id": call_id, "name": name, "args": shown}})
        if sig in self.done:
            self.repeats += 1
            ok, output = True, ("You already did exactly this. The result was:\n" + self.done[sig]
                                + "\nDo not repeat it. Use the result to continue or to answer the user.")
        elif not isinstance(args, dict):
            ok, output = False, "Error: the arguments were not valid JSON. Call the tool again with a JSON object."
        else:
            ok, output = tools.execute(self.workspace, name, args, self.allowed, shared=self.shared)
            if ok:
                self.done[sig] = output
        self.emit({"tool_result": {"id": call_id, "ok": ok, "output": output}})
        return output


def reply(
    conn: backend.Backend,
    model: str,
    messages: List[Dict[str, Any]],
    emit: Callable[[Dict[str, Any]], None],
    tool_names: Optional[List[str]] = None,
    workspace: Optional[Path] = None,
    shared: Optional[Path] = None,
) -> None:
    """Produce one reply, running tools as the model asks for them.

    shared is the folder shared between agents, or None if this agent may not use it.
    """
    say = lambda t: emit({"t": t})  # noqa: E731

    def complete(*args, **kwargs) -> backend.Turn:
        turn = backend.complete(*args, **kwargs)
        if turn.stats:
            emit({"stats": turn.stats})
        return turn

    if not tool_names or workspace is None:
        emit({"round": 0})
        complete(conn, model, messages, say)
        return

    messages = list(messages)
    key = (conn.url, model)
    text_mode = key in _TEXT_MODE

    def use_text_mode() -> None:
        messages[0] = {**messages[0], "content": messages[0]["content"] + "\n\n" + text_protocol(tool_names)}

    if text_mode:
        use_text_mode()
    specs = tools.specs(tool_names)
    session = Session(workspace, tool_names, emit, shared=shared)

    for step in range(MAX_STEPS):
        emit({"round": step})
        if not text_mode:
            try:
                turn = complete(conn, model, messages, say, tools=specs, temperature=TOOL_TEMPERATURE)
            except backend.ToolsUnsupported:
                _TEXT_MODE.add(key)
                text_mode = True
                use_text_mode()
                emit({"rewrite": ""})
                turn = None
            if turn is not None:
                if turn.tool_calls:
                    messages.append(backend.assistant_message(conn, turn))
                    for i, call in enumerate(turn.tool_calls):
                        output = session.run(call.name, call.arguments, f"{step}-{i}")
                        messages.append(backend.tool_message(conn, call, output))
                else:
                    found = find_text_call(turn.text, tool_names)
                    if not found:
                        return
                    name, args, rest = found
                    emit({"rewrite": rest})
                    messages.append({"role": "assistant", "content": turn.text})
                    output = session.run(name, args, f"{step}-text")
                    messages.append({"role": "user", "content": f"Result of {name}:\n{output}\n\n"
                                     "Continue. Call another tool if needed, otherwise give your answer."})
                if session.repeats >= 2:
                    break
                continue

        hider = HideToolBlocks(say)
        turn = complete(conn, model, messages, hider.feed, temperature=TOOL_TEMPERATURE)
        hider.flush()
        found_block = parse_tool_block(turn.text)
        if found_block is not None:
            name, args = found_block
            rest = TOOL_BLOCK.sub("", turn.text).strip()
        else:
            loose = find_text_call(turn.text, tool_names)
            if not loose:
                return
            name, args, rest = loose
        emit({"rewrite": rest})
        messages.append({"role": "assistant", "content": turn.text})
        output = session.run(name, args, f"{step}-text") if name else str(args)
        messages.append({"role": "user", "content": f"Result of {name or 'tool'}:\n{output}\n\n"
                         "Continue. Use another tool block if needed, otherwise give your answer."})
        if session.repeats >= 2:
            break

    # Step limit reached or the agent is going in circles: ask for a final answer without tools.
    emit({"round": MAX_STEPS})
    messages.append({"role": "user", "content": FINAL_REQUEST})
    hider = HideToolBlocks(say)
    turn = complete(conn, model, messages, hider.feed, temperature=TOOL_TEMPERATURE)
    hider.flush()
    if find_text_call(turn.text, tool_names) or parse_tool_block(turn.text):
        emit({"rewrite": "I stopped here because the task needed more steps than allowed in one reply. "
                         "Send another message to let me continue."})
