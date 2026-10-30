"""One agent calling another (the Call agents skill).

An agent with the call_agent action hands a task to another agent and gets the answer back as the
action's result. The called agent runs a complete reply of its own: its own system prompt, skills,
model (or the caller's model when its own is not loaded) and the same folders as the caller, so it
cannot reach anything the caller cannot.

An agent may call only the agents the user allowed it to (the "Agents it can call" list in its settings,
agents.callable_agents). The model is told those names and the descriptions the user wrote, nothing else about
them, and a call to any other agent is refused here as well.

An agent that was called this way never has the ability to call agents. This is enforced three ways:
its action list and its skill text leave call_agent out (agents.tool_names, agents.skills_prompt), it
gets no agent caller (runner.Session refuses the action without one), and a caller cannot call itself.
At most runner.MAX_AGENT_CALLS calls are made in one reply. A called agent works under its own
"actions per reply" setting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import agents, backend, runner, tools

MAX_TASK = 8000


def make_caller(model_url: Optional[str], conn: backend.Backend, model: str, caller: str, workspace: Path,
                shared: Optional[Path], allowed: Sequence[str] = ()) -> Callable[[Any, Any, Callable[[str], None]], str]:
    """The function runner.Session uses for call_agent: (agent name, message, on_event) -> the answer.
    allowed is the names of the agents the caller may call; with none, every call is refused."""

    def call(agent_name: Any, message: Any, on_event: Callable[[str], None]) -> str:
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise tools.ToolError("Say which agent to call.")
        if not isinstance(message, str) or not message.strip():
            raise tools.ToolError("Give the agent a task in message.")
        if len(message) > MAX_TASK:
            raise tools.ToolError(f"The task is too long (limit {MAX_TASK} characters).")
        if agent_name.strip() == caller:
            raise tools.ToolError("An agent cannot call itself.")
        if agent_name.strip() not in allowed:
            raise tools.ToolError(f"You are not allowed to call '{agent_name.strip()}'. "
                                  + (f"The agents you may call: {', '.join(allowed)}." if allowed else "You may not call any agent."))
        try:
            agent = agents.load(agent_name.strip())
        except agents.AgentError as exc:
            raise tools.ToolError(str(exc)) from exc
        use_conn, use_model = conn, model
        if agent.model:
            try:
                use_conn, use_model = backend.pick(model_url, agent.model)
            except backend.BackendError:
                pass   # its own model is not loaded: use the caller's
        messages = agents.build_messages(agent, [{"role": "user", "content": message.strip()}], called_by=caller)
        return run(agent, use_conn, use_model, messages, workspace, shared, on_event)

    return call


def run(agent: agents.Agent, conn: backend.Backend, model: str, messages: List[Dict[str, Any]], workspace: Path,
        shared: Optional[Path], on_event: Callable[[str], None]) -> str:
    """Run a called agent's reply and return its final text. It is given no way to call agents."""
    rounds: List[str] = [""]

    def emit(event: Dict[str, Any]) -> None:
        if "round" in event:
            rounds.append("")
        elif "t" in event:
            rounds[-1] += event["t"]
            on_event("")
        elif "rewrite" in event:
            rounds[-1] = event["rewrite"]
        elif "tool" in event:
            on_event(f"{agent.name} is using {event['tool'].get('name')}")

    runner.reply(conn, model, messages, emit, tool_names=agents.tool_names(agent, nested=True),
                 workspace=workspace, shared=shared, agent_caller=None, max_actions=agent.max_actions)
    text = next((r.strip() for r in reversed(rounds) if r.strip()), "")
    if len(text) > tools.MAX_READ:
        text = text[:tools.MAX_READ] + "\n... (the answer was cut off)"
    return f"Answer from {agent.name}:\n{text or '(the agent gave no answer)'}"
