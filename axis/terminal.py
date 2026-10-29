"""Shell sessions for the Terminal tab.

Each session is the user's own shell (bash or sh on macOS and Linux, cmd.exe on
Windows) started in a folder and connected through pipes, not a pseudo-terminal.
That keeps Axis free of extra dependencies and works the same everywhere, but
full-screen programs (editors, pagers, top) do not work; ordinary commands,
scripts and line-based programs do.

Axis echoes what the user types into the output (a terminal on a pipe has no line
editor to do it), except on Windows, where cmd prints each command it reads.

Unlike Run Python, a terminal is not guarded: it runs with the user's own
rights. Only the user types into it. Agents may be shown its recent output
(snapshot) but never write to it.

Output is kept in a rolling buffer. Clients read it by offset (read), so the
page can poll for what is new and an agent can be shown the newest part.
"""

from __future__ import annotations

import atexit
import codecs
import itertools
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_BUFFER = 400_000        # characters kept per session
MAX_INPUT = 64 * 1024
MAX_SESSIONS = 12
SNAPSHOT_CHARS = 6000       # what an agent is shown
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class TerminalError(Exception):
    pass


def clean(text: str) -> str:
    """Terminal output as plain text: escape sequences removed, carriage returns resolved."""
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1] or line.replace("\r", "")
        lines.append(line)
    return "\n".join(lines).replace("\x07", "")


def default_shell_name() -> str:
    """The shell used by an automatic terminal action on this computer."""
    if os.name == "nt":
        return "cmd"
    for candidate in (os.environ.get("SHELL", ""), "/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash",
                      "/opt/homebrew/bin/bash", shutil.which("bash") or ""):
        if candidate and Path(candidate).name == "bash" and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return "bash"
    return Path(shutil.which("sh") or "/bin/sh").name


def _shell(requested: str = "") -> Dict[str, Any]:
    """The command to start and whether Axis must echo typed input into the output."""
    requested = str(requested or "auto").strip().lower()
    if requested == "auto":
        requested = default_shell_name()
    if os.name == "nt":
        if requested == "cmd":
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            # cmd prints its prompt and each command it reads, so typed input is not echoed again.
            return {"argv": [comspec, "/D", "/K", "chcp 65001>nul"], "name": "cmd", "echo": False}
        if requested in ("powershell", "pwsh"):
            exe = shutil.which(requested) or (shutil.which("powershell") if requested == "pwsh" else shutil.which("pwsh"))
            if not exe:
                raise TerminalError(f"The {requested} shell is not installed on this computer.")
            return {"argv": [exe, "-NoLogo", "-NoProfile", "-NoExit"], "name": requested, "echo": True}
        raise TerminalError("Windows terminals use cmd, powershell, or pwsh.")
    if requested not in ("bash", "sh"):
        exe = shutil.which(requested)
        if not exe:
            raise TerminalError(f"The {requested} shell is not installed on this computer.")
        return {"argv": [exe, "-i"], "name": requested, "echo": True}
    # bash is preferred (also on macOS, where the login shell is zsh): run with -i it shows a prompt and loads
    # the user's aliases. --noediting stops its line editor, which would echo only the lines it reads itself:
    # Axis echoes what is typed instead, as it does for sh, so a program that reads input (python, read)
    # shows the answers typed to it as a terminal would. The option must come before -i.
    for candidate in (os.environ.get("SHELL", ""), "/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash",
                      "/opt/homebrew/bin/bash", shutil.which("bash") or ""):
        if candidate and Path(candidate).name == "bash" and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return {"argv": [candidate, "--noediting", "-i"], "name": "bash", "echo": True}
    sh = shutil.which("sh") or "/bin/sh"
    return {"argv": [sh, "-i"], "name": Path(sh).name, "echo": True}   # sh does not echo through pipes


# What shells print when they have no terminal of their own; true, but only noise here.
_NO_TTY = re.compile(r"^[\w./-]+: (?:cannot set terminal process group \(-?\d+\): [^\n]*|no job control in this shell|"
                     r"\d+: can't access tty; job control turned off)\n", re.MULTILINE)


class Session:
    def __init__(self, sid: str, cwd: Path, title: str, shell: str = "") -> None:
        self.id = sid
        self.cwd = cwd
        self.title = title
        self.started = time.time()
        self._lock = threading.Lock()
        self._text = ""
        self._base = 0                       # characters dropped from the front of the buffer
        self.exit_code: Optional[int] = None
        shell_info = _shell(shell)
        self.shell = shell_info["name"]
        self.echo = shell_info["echo"]
        env = dict(os.environ)
        env.update({"TERM": "dumb", "PAGER": "cat", "GIT_PAGER": "cat", "PYTHONUNBUFFERED": "1",
                    "PYTHONIOENCODING": "utf-8", "CLICOLOR": "0", "NO_COLOR": "1"})
        if self.shell == "bash":
            env.setdefault("PS1", "\\w$ ")
        kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(shell_info["argv"], cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, **kwargs)
        except OSError as exc:
            raise TerminalError(f"The shell could not be started: {exc.strerror or exc}") from exc
        threading.Thread(target=self._pump, name=f"terminal-{sid}", daemon=True).start()

    def _append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._text += text
            extra = len(self._text) - MAX_BUFFER
            if extra > 0:
                self._text = self._text[extra:]
                self._base += extra

    def _pump(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        out = self.proc.stdout
        assert out is not None
        while True:
            try:
                chunk = out.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            self._append(_NO_TTY.sub("", decoder.decode(chunk)))
        self._append(decoder.decode(b"", final=True))
        code = self.proc.wait()
        self.exit_code = code
        self._append(f"\n[The shell exited with code {code}.]\n")

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def write(self, text: str) -> None:
        if not isinstance(text, str):
            raise TerminalError("Input must be text.")
        if len(text) > MAX_INPUT:
            raise TerminalError("That input is too long.")
        if not self.alive:
            raise TerminalError("This terminal has closed. Open a new one.")
        if self.echo:
            self._append(text)
        data = text.replace("\r\n", "\n")
        if os.name == "nt":
            data = data.replace("\n", "\r\n")
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(data.encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise TerminalError("This terminal has closed. Open a new one.") from exc

    def interrupt(self) -> None:
        """Stop the command that is running (like Ctrl+C), keeping the shell."""
        if not self.alive:
            return
        if os.name == "nt":
            # The shell itself would be stopped too; Windows has no way to reach only its children here.
            raise TerminalError("Stopping a command is not available on Windows. Close this terminal and open a new one.")
        try:
            os.killpg(self.proc.pid, signal.SIGINT)
        except OSError:
            pass
        self._append("^C\n")

    def close(self) -> None:
        if self.alive:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                self.proc.kill()
            except OSError:
                pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def read(self, since: int = 0) -> Dict[str, Any]:
        with self._lock:
            start = max(int(since), self._base)
            text = self._text[start - self._base:]
            end = self._base + len(self._text)
            reset = int(since) < self._base
        return {"id": self.id, "text": text, "next": end, "reset": reset, "alive": self.alive, "exit": self.exit_code}

    def snapshot(self, chars: int = SNAPSHOT_CHARS) -> str:
        with self._lock:
            text = self._text[-chars * 2:]
        text = clean(text)
        return text[-chars:]

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "cwd": str(self.cwd), "shell": self.shell,
                "alive": self.alive, "exit": self.exit_code, "started": self.started}


_sessions: Dict[str, Session] = {}
_lock = threading.Lock()
_ids = itertools.count(1)


def start(cwd: Path, title: str = "", shell: str = "") -> Session:
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise TerminalError(f"Folder '{cwd}' does not exist.")
    with _lock:
        if len([s for s in _sessions.values() if s.alive]) >= MAX_SESSIONS:
            raise TerminalError(f"At most {MAX_SESSIONS} terminals can be open. Close one first.")
        sid = f"t{next(_ids)}"
    session = Session(sid, cwd, title or cwd.name or str(cwd), shell=shell)
    with _lock:
        _sessions[sid] = session
    return session


def get(sid: str) -> Session:
    with _lock:
        session = _sessions.get(sid)
    if session is None:
        raise TerminalError(f"Terminal '{sid}' does not exist.")
    return session


def list_all() -> List[Dict[str, Any]]:
    with _lock:
        sessions = list(_sessions.values())
    return [s.public() for s in sorted(sessions, key=lambda s: s.started)]


def close(sid: str) -> None:
    session = get(sid)
    with _lock:
        _sessions.pop(sid, None)
    session.close()


def close_all() -> None:
    with _lock:
        sessions = list(_sessions.values())
        _sessions.clear()
    for s in sessions:
        s.close()


atexit.register(close_all)
