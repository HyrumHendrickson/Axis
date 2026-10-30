"""Actions an agent can take. File actions work inside the agent's folder: the workspace
folder (~/Axis/shared, or the folder the user chose), or the folder chosen for one chat.

Paths that start with "shared/" also point into the workspace folder for compatibility.
Python code and program files run from the folder. A guard blocks writing, deleting,
renaming or listing files outside the folder, changing to another folder, reading
outside it (except Python's own files and the system's program folders) and starting
other programs. Terminal and Rust code from an agent is read before it runs and refused
when it names a place outside the folder. Both keep ordinary code contained; neither is
a security sandbox against deliberately hostile code.

Pictures made with generate_image go to ~/Axis/images, not into the folder.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from . import images, procs, web_tools
from .paths import images_folder

SHARED_PREFIX = "shared"
SHARED_ENV = "AXIS_SHARED_DIR"
MAX_OUTPUT = 8000
MAX_READ = 16000
PYTHON_TIMEOUT = 60
MAX_CAPTURE = 1024 * 1024   # bytes of output kept from agent code, per stream; the rest is read and thrown away
_TMP_LOCK = threading.Lock()
# Axis bookkeeping files that agents and the Workspace tab do not show.
HIDDEN_NAMES = {".axis-tmp", images.INDEX_NAME}


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- definitions

TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "list_files": {
        "description": "List the files and folders in your folder or in one of its subfolders.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Subfolder, relative to your folder (omit for the top level)"}},
            "required": [],
        },
    },
    "read_file": {
        "description": "Read a text file in your folder.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to your folder, e.g. notes/todo.txt"},
                "start": {"type": "integer", "description": "Character to start from (default 0); a cut-off result says what to use next"},
            },
            "required": ["path"],
        },
    },
    "write_file": {
        "description": "Create or overwrite a text file in your folder. Missing folders are made.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to your folder, e.g. report.md"},
                "content": {"type": "string", "description": "The whole text of the file. A JSON object or list is saved as JSON."},
            },
            "required": ["path", "content"],
        },
    },
    "make_folder": {
        "description": "Make a folder, and any missing parent folders, in your folder.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Folder path relative to your folder"}},
            "required": ["path"],
        },
    },
    "delete_file": {
        "description": "Delete a file or an empty folder in your folder.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to your folder"}},
            "required": ["path"],
        },
    },
    "run_python": {
        "description": "Run a Python 3 program in your folder and return what it prints. Use print() to show results.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The complete program"}},
            "required": ["code"],
        },
    },
    "run_python_file": {
        "description": "Run a .py file from your folder and return what it prints. The file's own folder is the working directory.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The .py file relative to your folder, e.g. scripts/report.py"}},
            "required": ["path"],
        },
    },
    "run_rust": {
        "description": "Compile and run a Rust program in your folder and return what it prints.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "The complete program, with fn main"}},
            "required": ["code"],
        },
    },
    "run_rust_file": {
        "description": "Compile and run a .rs file from your folder and return what it prints.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The .rs file relative to your folder"}},
            "required": ["path"],
        },
    },
    "run_terminal": {
        "description": "Run shell code in your folder and return what it prints.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The shell commands to run"},
                "shell": {"type": "string", "description": "Optional shell: auto, bash, sh, powershell, pwsh or cmd"},
            },
            "required": ["code"],
        },
    },
    "run_terminal_file": {
        "description": "Run a shell program file from your folder and return what it prints.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file relative to your folder"},
                "shell": {"type": "string", "description": "Optional shell: auto, bash, sh, powershell, pwsh or cmd"},
            },
            "required": ["path"],
        },
    },
}


TOOL_SPECS.update(web_tools.SPECS)
TOOL_SPECS["generate_image"] = {
    "description": "Make a picture from a text description with a local image model. It is saved in Axis's images folder "
    "(not your folder) and shown to the user.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The picture in detail: subject, style, lighting, composition"},
            "negative_prompt": {"type": "string", "description": "What to keep out of the picture (optional)"},
            "width": {"type": "integer", "description": "Pixels, a multiple of 64 (optional)"},
            "height": {"type": "integer", "description": "Pixels, a multiple of 64 (optional)"},
            "steps": {"type": "integer", "description": "Sampling steps (optional)"},
            "seed": {"type": "integer", "description": "For a repeatable picture (optional)"},
            "model": {"type": "string", "description": "Image model name (optional)"},
        },
        "required": ["prompt"],
    },
}
TOOL_SPECS["run_cas"] = {
    "description": "Run code in a computer algebra system for exact symbolic math: algebra, calculus, equations, series, "
    "matrices, number theory. Your folder is the working folder.",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Maxima commands (each ending in ; or $), or a SymPy Python program"},
            "system": {"type": "string", "enum": ["maxima", "sympy"],
                       "description": "Which system (optional; the one chosen on the CAS page)"},
        },
        "required": ["code"],
    },
}
# What a model is shown is one concrete action, run_maxima or run_sympy, chosen from what is installed and
# what the user picked on the CAS page (see model_tools). run_cas stays the name of the action in Axis
# (allowed lists, the handler) and the one a model sees only when no CAS is installed yet.
TOOL_SPECS["run_maxima"] = {
    "description": "Run Maxima code for exact symbolic math. Each command ends in ; (show the result) or $ (hide it). "
    "Your folder is the working folder.",
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "The Maxima commands"}},
        "required": ["code"],
    },
}
TOOL_SPECS["run_sympy"] = {
    "description": "Run a SymPy program for exact symbolic math. It imports sympy and prints its results. "
    "Your folder is the working folder.",
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "The complete Python program"}},
        "required": ["code"],
    },
}
TOOL_SPECS["read_text"] = {
    "description": "Read the text in a picture (OCR). The picture is a PNG, JPEG, BMP, TIFF or WebP file in your folder.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The picture relative to your folder, e.g. scans/page1.png"},
            "model": {"type": "string", "description": "OCR model, e.g. tesseract:eng (optional)"},
            "languages": {"type": "string", "description": "Tesseract languages joined with +, e.g. eng+lat (optional)"},
        },
        "required": ["path"],
    },
}

CALL_AGENT = "call_agent"
TOOL_SPECS[CALL_AGENT] = {
    "description": "Give a task to another agent and get its answer. It works in the same folder but cannot see this "
    "conversation, so the message must say everything it needs.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Name of the agent to call"},
            "message": {"type": "string", "description": "The complete task"},
        },
        "required": ["agent", "message"],
    },
}

SHARE_FILE = "share_file"
TOOL_SPECS[SHARE_FILE] = {
    "description": "Give the user a file from your folder to download. It appears in the chat with buttons to download "
    "it or open the folder it is in. The file must already exist.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "The file relative to your folder, e.g. reports/summary.pdf"}},
        "required": ["path"],
    },
}

RUN_CRAFT = "run_craft"
TOOL_SPECS[RUN_CRAFT] = {
    "description": "Run a Craft app's command-line program (photocraft-cli etc.) without a shell, so nothing needs "
    "quoting. One item of args for each argument; write JSON as an object or list, not text. "
    "{\"join\": [\"ID=\", {...}]} makes one argument from text and JSON.",
    "parameters": {
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "The Craft app, e.g. photocraft"},
            "args": {"type": "array", "items": {},
                     "description": "Arguments after the program name, e.g. [\"info\", \"a.png\"]"},
            "find": {"type": "string", "description": "Only return output lines (or JSON items) containing this text (optional)"},
        },
        "required": ["app", "args"],
    },
}


def _shells_here() -> List[str]:
    """The shells run_terminal can use on this computer, the default first."""
    names = ["cmd", "powershell", "pwsh"] if os.name == "nt" else ["bash", "sh", "pwsh"]
    found = [n for n in names if n == "cmd" or shutil.which(n)]
    try:
        from . import terminal
        default = terminal.default_shell_name()
    except ImportError:
        default = found[0] if found else "sh"
    return [default] + [n for n in found if n != default]


def tool_spec(name: str, callable_agents: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """One action's definition as the model should read it now: the static text in TOOL_SPECS plus what is
    set up on this computer (the image models, the OCR models and languages, the shells, the agents it may call,
    the installed Craft apps). A copy is returned; TOOL_SPECS itself never changes, so a model that was removed is
    not mentioned later. The lists live here and not in the skills, so a model is told each of them once.

    callable_agents is the list of {name, description} an agent was allowed to call (agents.callable_agents). Only
    those names and the descriptions the user wrote are shown; never another agent's skills or system prompt."""
    spec = copy.deepcopy(TOOL_SPECS[name])
    props = spec["parameters"]["properties"]
    try:
        if name == "generate_image":
            models = images.list_models()
            usable = [m for m in models if not m.get("missing")]
            if usable:
                default = images.default_model_id(models)
                names = ", ".join(m["name"] + (" (default)" if m["id"] == default else "") for m in usable)
                spec["description"] += f" Models: {names}."
                props["model"]["description"] = "Image model name, one of: " + ", ".join(m["name"] for m in usable) + " (optional)"
        elif name == "read_text":
            from . import ocr
            usable = [m for m in ocr.list_models() if m.get("usable")]
            if usable:
                props["model"]["description"] = "OCR model, one of: " + ", ".join(m["id"] for m in usable) + " (optional)"
                langs = [m["model"] for m in usable if m.get("engine") == "tesseract"]
                if langs:
                    props["languages"]["description"] = ("Tesseract languages joined with +, from: " + ", ".join(langs)
                                                         + " (optional)")
        elif name == CALL_AGENT:
            if callable_agents is None:
                from . import agents   # no agent in particular (the Skills page): every agent, by name only
                callable_agents = [{"name": a.name, "description": ""} for a in agents.list_all()]
            if callable_agents:
                props["agent"]["enum"] = [a["name"] for a in callable_agents]
                props["agent"]["description"] = "Name of the agent to call, one of: " + "; ".join(
                    f"{a['name']} ({a['description']})" if a.get("description") else a["name"] for a in callable_agents)
        elif name in ("run_terminal", "run_terminal_file"):
            shells = _shells_here()
            props["shell"]["description"] = f"Optional shell, one of {', '.join(shells)} (default {shells[0]})"
        elif name == RUN_CRAFT:
            from . import artcraft
            have = [a for a in artcraft.APP_IDS if artcraft.cli_program(a) is not None]
            props["app"]["enum"] = have or list(artcraft.APP_IDS)
            if have:
                props["app"]["description"] = "The Craft app, one of: " + ", ".join(have)
    except Exception:   # a setup problem must never stop a reply; the static text is still correct
        return copy.deepcopy(TOOL_SPECS[name])
    return spec


def specs(names: List[str], callable_agents: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Tool definitions in the OpenAI/Ollama function format."""
    return [{"type": "function", "function": {"name": n, **tool_spec(n, callable_agents)}} for n in names if n in TOOL_SPECS]


def cas_tool_name() -> str:
    """The name of the CAS action a model is shown: run_maxima or run_sympy for the system that is in use
    (the one chosen on the CAS page, else the first installed), run_cas when none is installed."""
    try:
        from . import cas
        selected = cas.default_system()
    except Exception:
        selected = None
    return "run_" + selected if selected in ("maxima", "sympy") else "run_cas"


def model_tools(names: List[str]) -> List[str]:
    """The names of the actions a model is shown for an agent that has `names`, chosen from the current setup:
    the CAS action becomes run_maxima or run_sympy."""
    out: List[str] = []
    cas_name = cas_tool_name() if "run_cas" in names else "run_cas"
    for name in names:
        shown = cas_name if name == "run_cas" else name
        if shown not in out:
            out.append(shown)
    return out


# -------------------------------------------------------------------- helpers


def resolve(workspace: Path, rel: Any, allow_root: bool = False) -> Path:
    if rel is None or rel == "":
        rel = "."
    if not isinstance(rel, str):
        raise ToolError("path must be text.")
    rel = rel.strip().replace("\\", "/")
    if Path(rel).anchor or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise ToolError("Use a path inside your folder, like notes/todo.txt, not an absolute path.")
    root = workspace.resolve()
    full = (root / rel).resolve()
    if full != root and root not in full.parents:
        raise ToolError("That path is outside your folder. Only paths inside your folder are allowed.")
    if full == root and not allow_root:
        raise ToolError("Give a file or folder name inside your folder.")
    return full


@dataclass
class Places:
    """The folders an agent may use: its own, and the shared one when enabled."""

    personal: Path
    shared: Optional[Path] = None


Where = Union[Path, Places]


def places(ws: Where) -> Places:
    return ws if isinstance(ws, Places) else Places(Path(ws))


def locate(ws: Where, rel: Any, allow_root: bool = False) -> Tuple[Path, Path, str]:
    """Resolve a tool path. Returns (root folder, full path, prefix for display).

    With a shared folder, "shared" and "shared/..." point into it; everything
    else is inside the agent's own folder.
    """
    p = places(ws)
    if p.shared is not None and isinstance(rel, str):
        norm = rel.strip().replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        head, _, tail = norm.partition("/")
        if head == SHARED_PREFIX:
            p.shared.mkdir(parents=True, exist_ok=True)
            if not tail.strip("/") and not allow_root:
                raise ToolError("Give a file or folder name inside the shared folder, like shared/notes.txt.")
            return p.shared.resolve(), resolve(p.shared, tail.strip("/"), allow_root=True), SHARED_PREFIX + "/"
    return p.personal.resolve(), resolve(p.personal, rel, allow_root), ""


def _show(root: Path, full: Path, prefix: str) -> str:
    rel = full.relative_to(root).as_posix()
    if rel == ".":
        return prefix.rstrip("/") or "."
    return prefix + rel


def _rel(workspace: Path, p: Path) -> str:
    return p.relative_to(workspace.resolve()).as_posix() or "."


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} more characters not shown)"


def _require(args: Dict[str, Any], key: str) -> Any:
    if key not in args:
        raise ToolError(f"Missing required argument '{key}'.")
    return args[key]


# ---------------------------------------------------------------------- tools


def list_files(ws: Where, args: Dict[str, Any]) -> str:
    root, folder, prefix = locate(ws, args.get("path"), allow_root=True)
    if not folder.exists():
        raise ToolError(f"{args.get('path')} does not exist.")
    if not folder.is_dir():
        raise ToolError(f"{args.get('path')} is a file, not a folder.")
    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    entries = [e for e in entries if e.name not in HIDDEN_NAMES]
    lines = []
    if places(ws).shared is not None and not prefix and folder == root:
        # The shared folder appears at the top of the agent's own folder.
        entries = [e for e in entries if e.name != SHARED_PREFIX]
        lines.append(f"{SHARED_PREFIX}/  (shared with other agents)")
    if not entries and not lines:
        return f"{_show(root, folder, prefix)} is empty."
    for e in entries[:500]:
        if e.is_dir():
            lines.append(f"{e.name}/")
            continue
        try:
            lines.append(f"{e.name}  ({e.stat().st_size} bytes)")
        except OSError:  # a broken link, or a file removed while listing
            lines.append(f"{e.name}  (unreadable)")
    if len(entries) > 500:
        lines.append(f"... and {len(entries) - 500} more")
    return "\n".join(lines)


def read_file(ws: Where, args: Dict[str, Any]) -> str:
    _, path, _ = locate(ws, _require(args, "path"))
    if not path.is_file():
        raise ToolError(f"{args['path']} is not a file in your folder.")
    try:
        start = max(0, int(float(args.get("start") or 0)))
    except (TypeError, ValueError):
        raise ToolError("start must be a whole number.")
    total = path.stat().st_size
    with path.open("rb") as f:  # never load a huge file just to show a part of it
        data = f.read(min(4 * (start + MAX_READ) + 4, 16 * 1024 * 1024))
    if b"\0" in data[:4096]:
        raise ToolError(f"{args['path']} is a binary file and cannot be shown as text.")
    text = data.decode("utf-8", errors="replace")
    if start and start >= len(text) and total > len(data):
        raise ToolError("start is further into the file than can be read at once. Use run_python to read that part.")
    shown = text[start:start + MAX_READ]
    if start + MAX_READ < len(text) or total > len(data):
        return shown + f"\n... (the file continues; call read_file again with start={start + len(shown)})"
    return shown


def _as_text(content: Any, path: Any) -> str:
    """The text to save. A JSON object or list sent as content is written as JSON (one item a line in a .jsonl
    file), never as Python's own notation with single quotes."""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        if isinstance(content, list) and str(path).lower().endswith(".jsonl"):
            return "\n".join(json.dumps(i, ensure_ascii=False) for i in content) + "\n"
        return json.dumps(content, ensure_ascii=False, indent=2) + "\n"
    return str(content)


def write_file(ws: Where, args: Dict[str, Any]) -> str:
    root, path, prefix = locate(ws, _require(args, "path"))
    content = _as_text(_require(args, "content"), args["path"])
    if path.is_dir():
        raise ToolError(f"{args['path']} is a folder.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Wrote {_show(root, path, prefix)} ({len(content.encode('utf-8'))} bytes)."


def make_folder(ws: Where, args: Dict[str, Any]) -> str:
    root, path, prefix = locate(ws, _require(args, "path"))
    if path.is_file():
        raise ToolError(f"{args['path']} is already a file.")
    path.mkdir(parents=True, exist_ok=True)
    return f"Folder {_show(root, path, prefix)} is ready."


def delete_file(ws: Where, args: Dict[str, Any]) -> str:
    _, path, _ = locate(ws, _require(args, "path"))
    if path.is_symlink() or path.is_file():
        path.unlink()
        return f"Deleted {args['path']}."
    if path.is_dir():
        try:
            path.rmdir()
        except OSError:
            raise ToolError(f"{args['path']} is a folder that is not empty. Delete its files first.")
        return f"Deleted folder {args['path']}."
    raise ToolError(f"{args['path']} does not exist.")


# Runs inside the child Python. Reads the program from stdin, installs the
# guard, then runs the program. AXIS_ROOT is the folder the guard protects (the
# working directory when unset); AXIS_SCRIPT names the program when it is a file.
#
# The guard keeps the program in its folder: it may not write, delete, rename or
# list anything outside it, change to another folder, or start programs. It may
# read outside the folder only from Python's own installation and packages and
# from the operating system's program folders, which code needs to run at all.
BOOTSTRAP = r'''
import os, sys
_code = sys.stdin.read()
_norm = lambda p: os.path.normcase(os.path.realpath(p))
_ROOTS = [_norm(os.environ.get("AXIS_ROOT") or os.getcwd())]
if os.environ.get("AXIS_SHARED_DIR"):
    _ROOTS.append(_norm(os.environ["AXIS_SHARED_DIR"]))
_READ = [p for p in dict.fromkeys([sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(sys.executable)]
                                   + [p for p in sys.path if p]) if p]
try:
    import site
    _READ += site.getsitepackages() + [site.getusersitepackages()]
except Exception:
    pass
if os.name == "nt":
    _READ += [os.environ.get(k, "") for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles")]
else:
    _READ += ["/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/etc", "/opt", "/dev", "/proc", "/sys",
              "/System", "/Library", "/private/etc", "/nix/store"]
_READ = [_norm(p) for p in _READ if p]
sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())
def _under(p, roots):
    if isinstance(p, int):
        return True
    try:
        full = _norm(os.fsdecode(p))
    except Exception:
        return False
    return any(full == r or full.startswith(r.rstrip(os.sep) + os.sep) for r in roots)
_inside = lambda p: _under(p, _ROOTS)
_readable = lambda p: _under(p, _ROOTS) or _under(p, _READ)
_WRITE = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_TRUNC", 0)
_ONE = {"os.remove", "os.rmdir", "os.mkdir", "os.truncate", "os.chmod", "os.chown", "os.utime", "shutil.rmtree", "os.removexattr", "os.setxattr"}
_TWO = {"os.rename", "os.link"}
_RUN = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile", "os.fork", "os.forkpty", "pty.spawn"}
def _deny(what):
    raise PermissionError("Axis: " + what)
def _guard(event, args):
    if event == "open":
        path, mode, flags = args
        writing = any(c in mode for c in "wax+") if isinstance(mode, str) else bool((flags or 0) & _WRITE)
        if writing and not _inside(path):
            _deny("writing outside your folder is not allowed: " + str(path))
        if not writing and path is not None and not _readable(path):
            _deny("reading files outside your folder is not allowed: " + str(path))
    elif event in ("os.listdir", "os.scandir"):
        if args and args[0] is not None and not _readable(args[0]):
            _deny("looking inside folders outside your folder is not allowed: " + str(args[0]))
    elif event == "os.chdir":
        if args and not _inside(args[0]):
            _deny("changing to a folder outside your folder is not allowed: " + str(args[0]))
    elif event in _ONE:
        if args and not _inside(args[0]):
            _deny("changing files outside your folder is not allowed: " + str(args[0]))
    elif event in _TWO:
        if not (_inside(args[0]) and _inside(args[1])):
            _deny("moving or linking files outside your folder is not allowed")
    elif event == "os.symlink":
        if not _inside(args[1]):
            _deny("creating links outside your folder is not allowed")
    elif event in _RUN:
        _deny("starting other programs is not allowed in Run Python")
sys.addaudithook(_guard)
_script = os.environ.get("AXIS_SCRIPT") or "agent_code.py"
sys.argv = [_script]
_g = {"__name__": "__main__", "__builtins__": __builtins__}
if os.environ.get("AXIS_SCRIPT"):
    _g["__file__"] = os.path.abspath(_script)
try:
    exec(compile(_code, _script, "exec"), _g)
except SystemExit:
    raise
except BaseException as _e:
    import traceback
    _tb = _e.__traceback__.tb_next if _e.__traceback__ else None
    traceback.print_exception(type(_e), _e, _tb)
    sys.exit(1)
'''


def python_executable() -> str:
    """The user's own Python, so installed packages are available to agent code."""
    exe = _find_python()
    # The Axis app runs under pythonw.exe, which has no output; agent code needs python.exe.
    if os.name == "nt" and Path(exe).name.lower() == "pythonw.exe":
        console = Path(exe).with_name("python.exe")
        if console.is_file():
            return str(console)
    return exe


def _find_python() -> str:
    override = os.environ.get("AXIS_PYTHON")
    if override:
        return override
    if sys.prefix == sys.base_prefix:
        return sys.executable
    candidates = [getattr(sys, "_base_executable", "")]
    base = Path(sys.base_prefix)
    if os.name == "nt":
        candidates.append(str(base / "python.exe"))
    else:
        v = sys.version_info
        candidates += [str(base / "bin" / f"python{v.major}.{v.minor}"), str(base / "bin" / "python3")]
    for c in candidates:
        if c and Path(c).is_file() and Path(c).resolve() != Path(sys.executable).resolve():
            return c
    return sys.executable


def run_code(where: Where, code: str, cwd: Optional[Path] = None, script: str = "",
             python: Optional[str] = None) -> Dict[str, Any]:
    """Run Python code under the guard. Returns {exit, stdout, stderr, timed_out}.

    The guard protects the personal folder (and the shared folder, when given);
    cwd, which must be inside one of them, is the working directory (the personal
    folder by default). script is the file name shown in tracebacks and sys.argv.
    python is another interpreter to use (the CAS page's SymPy environment); by
    default the user's own Python runs the code.
    """
    p = places(where)
    ws = p.personal
    ws.mkdir(parents=True, exist_ok=True)
    root = ws.resolve()
    # Each run gets its own scratch folder inside .axis-tmp, so a run that finishes cannot delete the
    # scratch files of another one that is still going (several chats can use the same agent at once).
    with _TMP_LOCK:
        base = ws / ".axis-tmp"
        base.mkdir(exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=str(base)))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and k not in (SHARED_ENV, "AXIS_ROOT", "AXIS_SCRIPT")}
    if p.shared is not None:
        p.shared.mkdir(parents=True, exist_ok=True)
        env[SHARED_ENV] = str(p.shared.resolve())
    env.update({"TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp), "MPLCONFIGDIR": str(tmp),
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
                "AXIS_ROOT": str(root)})
    if script:
        env["AXIS_SCRIPT"] = script
    kwargs: Dict[str, Any] = _run_kwargs()
    result = None
    try:
        for exe in ([python] if python else dict.fromkeys([python_executable(), sys.executable])):
            try:
                result = _run_capped([exe, "-c", BOOTSTRAP], code, str(cwd or ws), env, PYTHON_TIMEOUT, kwargs)
                break
            except OSError:  # this interpreter cannot be started; try the next one
                continue
    finally:
        with _TMP_LOCK:
            shutil.rmtree(tmp, ignore_errors=True)
            try:
                base.rmdir()   # only succeeds when no other run is using it
            except OSError:
                pass
    if result is None:
        raise ToolError("Python could not be started on this computer.")
    return result


def _run_capped(cmd: Union[str, List[str]], code: str, cwd: str, env: Dict[str, str], timeout: float,
                kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Run cmd with code on its standard input. Returns {exit, stdout, stderr, timed_out}.

    Only the first MAX_CAPTURE bytes of each output stream are kept. The rest is still read, so the
    program is never blocked, but it is not stored: code that prints without end cannot fill Axis's memory.
    """
    proc = procs.track(subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=cwd, env=env, **kwargs))
    kept = {"out": bytearray(), "err": bytearray()}
    cut = {"out": False, "err": False}

    def drain(stream, key: str) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                room = MAX_CAPTURE - len(kept[key])
                if room > 0:
                    kept[key] += chunk[:room]
                if len(chunk) > room:
                    cut[key] = True
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def feed() -> None:
        try:
            proc.stdin.write(code.encode("utf-8"))
        except OSError:  # the program ended without reading its code
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    threads = [threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True),
               threading.Thread(target=feed, daemon=True)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        procs.kill_tree(proc)   # the program and anything it started in the background
        proc.wait()
    except BaseException:
        procs.kill_tree(proc)
        proc.wait()
        raise
    finally:
        procs.untrack(proc)
    for t in threads:
        t.join(5)

    def text(key: str) -> str:
        out = bytes(kept[key]).decode("utf-8", "replace").replace("\r\n", "\n")
        return out + (f"\n... (output cut off after {MAX_CAPTURE // 1024} KB)" if cut[key] else "")

    return {"exit": None if timed_out else proc.returncode, "stdout": text("out"), "stderr": text("err"),
            "timed_out": timed_out}


def _report(result: Dict[str, Any], quiet: str = "(The code printed nothing. Use print() to show results.)") -> str:
    if result["timed_out"]:
        return f"Stopped: the code ran longer than {PYTHON_TIMEOUT} seconds."
    parts = [f"Exit code: {result['exit']}"]
    if result["stdout"]:
        parts.append("Output:\n" + result["stdout"].rstrip())
    if result["stderr"]:   # a program that succeeded often writes its notes ("wrote a.docx") to this stream
        parts.append(("Errors:\n" if result["exit"] else "Messages:\n") + result["stderr"].rstrip())
    if not result["stdout"] and not result["stderr"]:
        parts.append(quiet)
    return _clip("\n".join(parts))


def run_python(where: Where, args: Dict[str, Any]) -> str:
    code = _require(args, "code")
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code must be a non-empty Python program.")
    return _report(run_code(where, fix_escaped_code(code)))


MAX_SCRIPT = 1024 * 1024
RUST_TIMEOUT = 60
TERMINAL_TIMEOUT = 60


def run_file(where: Where, rel: Any) -> Dict[str, Any]:
    """Run a .py file from the personal (or shared) folder with its own folder as the working directory.

    Returns run_code's result plus the file's path as shown to the agent.
    """
    root, path, prefix = locate(where, rel)
    if not path.is_file():
        raise ToolError(f"{rel} is not a file in your folder.")
    if path.suffix.lower() not in (".py", ".pyw"):
        raise ToolError(f"{rel} is not a Python file (.py).")
    if path.stat().st_size > MAX_SCRIPT:
        raise ToolError(f"{rel} is too large to run (limit {MAX_SCRIPT // 1024} KB).")
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        raise ToolError(f"{rel} is not a text file.")
    code = data.decode("utf-8", errors="replace")
    if code.startswith("\ufeff"):
        code = code[1:]
    result = run_code(where, code, cwd=path.parent, script=path.name)
    return {**result, "path": _show(root, path, prefix)}


def run_python_file(where: Where, args: Dict[str, Any]) -> str:
    result = run_file(where, _require(args, "path"))
    return f"Ran {result['path']}.\n" + _report(result)


def _run_env(where: Where, temp: Optional[Path] = None) -> Dict[str, str]:
    """Build the environment for a program started by an agent. With temp, scratch files go there."""
    p = places(where)
    env = dict(os.environ)
    if temp is not None:
        env.update({"TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)})
    if p.shared is not None:
        p.shared.mkdir(parents=True, exist_ok=True)
        env[SHARED_ENV] = str(p.shared.resolve())
    # Art Craft owns the portable Rust installation. Import it lazily so ordinary
    # file and Python actions do not initialize the engine/download modules.
    try:
        from . import artcraft
        env.update(artcraft.rust_build_env())
        # The headless <app>-cli programs of installed Art Craft apps, so an agent can run them by name.
        dirs = artcraft.cli_dirs()
        if dirs:
            env["PATH"] = os.pathsep.join(dirs + ([env["PATH"]] if env.get("PATH") else []))
    except (ImportError, OSError):
        pass
    return env


# ------------------------------------------------- keeping programs in their folder
# A shell or a compiled Rust program cannot be watched while it runs the way Python can, so before an
# agent's terminal or Rust code runs, Axis reads it and refuses code that names a place outside the
# folder: a full path, a ".." that climbs out, the home folder, or a bare "cd". This catches ordinary
# mistakes and ordinary attempts; like the Python guard it is not a sandbox against hostile code.

_DEVICES = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero", "/dev/urandom",
            "/dev/random", "nul", "con"}
_URL = re.compile(r"\b[a-zA-Z][\w+.-]*://\S+")
_STOP = r"""\s'"`;|&<>(){}\[\],"""            # characters that end a path in code
_LEAD = r"""(?:^|(?<=[\s='"`(:;,>|<\[{]))"""    # what may come just before a path
_POSIX_ABS = re.compile(_LEAD + "(/[^" + _STOP + "]*)")
_WIN_ABS = re.compile(r"""(?:^|(?<=[\s='"`(;,>|<\[{]))([A-Za-z]:(?:[\\/][^""" + _STOP + r"""]*)?|\\\\[^""" + _STOP
                      + "]+)(?=$|[" + _STOP + "])")
_DOTDOT = re.compile(_LEAD + r"((?:[^" + _STOP + r"]*[\\/])?\.\.(?:[\\/][^" + _STOP + "]*)?)(?=$|[" + _STOP + "])")
_HOME = re.compile(_LEAD + r"~(?=$|[\w/\\" + _STOP + r"])|\$\{?(?:HOME|OLDPWD|USERPROFILE)\b|"
                   r"%(?:USERPROFILE|HOMEPATH|HOMEDRIVE|SystemDrive|APPDATA|LOCALAPPDATA)%|"
                   r"\$env:(?:USERPROFILE|HOME|HOMEPATH|HOMEDRIVE|SystemDrive|APPDATA|LOCALAPPDATA)\b", re.IGNORECASE)
_BARE_CD = re.compile(r"(?:^|[;&|(\n{])\s*(?:cd|chdir|pushd|set-location|sl)(?:\s+(?:-|/d|~))?\s*(?=$|[;&|)\n}])",
                      re.IGNORECASE | re.MULTILINE)
_RUST_STRING = re.compile(r'r(#*)"(.*?)"\1|"((?:[^"\\]|\\.)*)"', re.DOTALL)
_RUST_HOME = re.compile(r"\bhome_dir\b|\bset_current_dir\b")


def _within(path: Path, roots: List[Path]) -> bool:
    try:
        full = path.resolve()
    except (OSError, RuntimeError):
        return False
    return any(full == r or r in full.parents for r in roots)


def _outside_in(text: str, roots: List[Path], cwd: Path) -> Optional[str]:
    """The first place outside roots that text names, or None."""
    text = _URL.sub(" ", text)
    found = _HOME.search(text)
    if found:
        return found.group(0)
    candidates: List[str] = []
    if os.name == "nt":
        candidates += _WIN_ABS.findall(text)
        candidates += [m for m in _POSIX_ABS.findall(text) if m in ("/", "\\")]   # /x is usually an option there
    else:
        candidates += _POSIX_ABS.findall(text)
    for ref in candidates:
        if ref in ("/", "\\") and re.search(r"\s[/\\]\s+[\d$(]", text):
            continue   # division in arithmetic, like $((4 / 2))
        if ref.rstrip(":").lower() in _DEVICES or ref.lower() in _DEVICES:
            continue
        if not _within(Path(ref), roots):
            return ref
    for ref in _DOTDOT.findall(text):
        target = Path(ref.replace("\\", "/"))
        if not _within(target if target.is_absolute() else cwd / target, roots):
            return ref
    return None


def _strip_comments(code: str) -> str:
    kept = []
    for line in code.splitlines():
        bare = line.strip().lower()
        if bare.startswith("#") or bare.startswith("::") or bare.startswith("rem ") or bare == "rem":
            continue
        kept.append(line)
    return "\n".join(kept)


def check_terminal_code(where: Where, code: str, cwd: Optional[Path] = None) -> None:
    """Refuse terminal code from an agent that names a place outside its folder."""
    p = places(where)
    roots = [p.personal.resolve()] + ([p.shared.resolve()] if p.shared is not None else [])
    body = _strip_comments(code)
    if _BARE_CD.search(body):
        raise ToolError("A cd without a folder leaves your folder. Give a folder inside it, like cd notes.")
    bad = _outside_in(body, roots, Path(cwd or p.personal).resolve())
    if bad:
        raise ToolError(f"The code names '{bad}', which is outside your folder. Only {roots[0]} and the folders "
                        "inside it can be used. Use paths inside your folder.")


def check_rust_code(where: Where, code: str, cwd: Optional[Path] = None) -> None:
    """Refuse Rust code from an agent that names a place outside its folder in its text."""
    p = places(where)
    roots = [p.personal.resolve()] + ([p.shared.resolve()] if p.shared is not None else [])
    if _RUST_HOME.search(code):
        raise ToolError("The program asks for the home folder or changes its folder, which would leave your folder. "
                        "Use paths inside your folder.")
    for m in _RUST_STRING.finditer(code):
        literal = m.group(2) if m.group(2) is not None else m.group(3)
        bad = _outside_in(literal.replace("\\\\", "\\") if m.group(3) is not None else literal, roots, Path(cwd or p.personal).resolve())
        if bad:
            raise ToolError(f"The program names '{bad}', which is outside your folder. Only {roots[0]} and the "
                            "folders inside it can be used. Use paths inside your folder.")


def _run_kwargs() -> Dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def _temp_dir(folder: Path) -> Tuple[Path, Path]:
    """Make a private build folder below the working folder."""
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / ".axis-tmp"
    with _TMP_LOCK:
        base.mkdir(exist_ok=True)
        return base, Path(tempfile.mkdtemp(prefix="run-", dir=str(base)))


def _finish_temp(base: Path, temp: Path) -> None:
    with _TMP_LOCK:
        shutil.rmtree(temp, ignore_errors=True)
        try:
            base.rmdir()
        except OSError:
            pass


def _rustc(env: Dict[str, str]) -> str:
    path = shutil.which("rustc", path=env.get("PATH"))
    if path:
        return path
    raise ToolError("Rust is not installed. Open Art Craft and click Install Rust, then try again.")


def _rust_code(where: Where, code: str, cwd: Optional[Path] = None, script: str = "program.rs") -> Dict[str, Any]:
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code must be a non-empty Rust program.")
    folder = Path(cwd or places(where).personal).resolve()
    base, temp = _temp_dir(folder)
    env = _run_env(where, temp)
    source = temp / script
    binary = temp / ("program.exe" if os.name == "nt" else "program")
    try:
        source.write_text(code, encoding="utf-8")
        if os.name == "nt":
            from . import artcraft
            try:
                artcraft.ensure_cpp_tools()
            except artcraft.ArtCraftError as exc:
                raise ToolError(str(exc)) from exc
            env = _run_env(where, temp)
        compiler = _rustc(env)
        compile_result = _run_capped([compiler, "--edition", "2021", str(source), "-o", str(binary)], "",
                                     str(folder), env, RUST_TIMEOUT, _run_kwargs())
        if compile_result["timed_out"] or compile_result["exit"] != 0:
            return compile_result
        return _run_capped([str(binary)], "", str(folder), env, RUST_TIMEOUT, _run_kwargs())
    finally:
        _finish_temp(base, temp)


def run_rust(where: Where, args: Dict[str, Any]) -> str:
    code = _require(args, "code")
    if isinstance(code, str):
        check_rust_code(where, code)
    return _report(_rust_code(where, code))


def run_rust_file_result(where: Where, rel: Any, checked: bool = False) -> Dict[str, Any]:
    """Compile and run a .rs file. checked=True (an agent ran it) first refuses code that leaves the folder."""
    root, path, prefix = locate(where, rel)
    if not path.is_file():
        raise ToolError(f"{rel} is not a file in your folder.")
    if path.suffix.lower() != ".rs":
        raise ToolError(f"{rel} is not a Rust file (.rs).")
    if path.stat().st_size > MAX_SCRIPT:
        raise ToolError(f"{rel} is too large to run (limit {MAX_SCRIPT // 1024} KB).")
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        raise ToolError(f"{rel} is not a text file.")
    code = data.decode("utf-8", errors="replace")
    if code.startswith("\ufeff"):
        code = code[1:]
    if checked:
        check_rust_code(where, code, cwd=path.parent)
    result = _rust_code(where, code, cwd=path.parent, script=path.name)
    return {**result, "path": _show(root, path, prefix)}


def run_rust_file(where: Where, args: Dict[str, Any]) -> str:
    result = run_rust_file_result(where, _require(args, "path"), checked=True)
    return f"Ran {result['path']}.\n" + _report(result)


def _shell_name(value: Any) -> str:
    name = str(value or "auto").strip().lower()
    aliases = {"powershell.exe": "powershell", "pwsh.exe": "pwsh", "cmd.exe": "cmd"}
    return aliases.get(name, name)


def _shell_executable(name: Any, env: Dict[str, str]) -> Tuple[str, str]:
    requested = _shell_name(name)
    if requested in ("", "auto"):
        try:
            from . import terminal
            requested = terminal.default_shell_name()
        except ImportError:
            requested = "cmd" if os.name == "nt" else "bash"
    choices = {"bash": "bash", "sh": "sh", "cmd": "cmd", "powershell": "powershell", "pwsh": "pwsh"}
    if requested not in choices:
        raise ToolError("Unknown shell. Use auto, bash, sh, powershell, pwsh, or cmd.")
    program = choices[requested]
    if requested == "cmd":
        program = env.get("COMSPEC") or "cmd.exe"
    else:
        program = shutil.which(program, path=env.get("PATH")) or ""
    if not program:
        raise ToolError(f"The {requested} shell is not installed on this computer.")
    return program, requested


def _shell_command(program: str, shell: str, script: Path) -> Union[str, List[str]]:
    if shell in ("bash", "sh"):
        return [program, str(script)]
    if shell in ("powershell", "pwsh"):
        return [program, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if shell == "cmd":
        # One finished command line: Python would quote a list entry holding quotes with backslash escapes,
        # which cmd.exe cannot read. /S removes the outer quotes and keeps the inner ones (a path with spaces).
        return f'"{program}" /D /Q /S /C "call "{script}""'
    raise ToolError("Unknown shell.")


def _terminal_code(where: Where, code: str, shell: Any = "auto", cwd: Optional[Path] = None,
                  suffix: str = ".sh") -> Dict[str, Any]:
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code must be a non-empty terminal program.")
    folder = Path(cwd or places(where).personal).resolve()
    env = _run_env(where)
    program, selected = _shell_executable(shell, env)
    if selected == "cmd":
        suffix = ".cmd"
    elif selected in ("powershell", "pwsh"):
        suffix = ".ps1"
    base, temp = _temp_dir(folder)
    env.update({"TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)})   # scratch files stay in the folder
    script = temp / ("program" + suffix)
    try:
        with script.open("w", encoding="utf-8", newline="\r\n" if selected == "cmd" else "\n") as stream:
            stream.write(code)
        return _run_capped(_shell_command(program, selected, script), "", str(folder), env,
                           TERMINAL_TIMEOUT, _run_kwargs())
    finally:
        _finish_temp(base, temp)


def run_terminal(where: Where, args: Dict[str, Any]) -> str:
    code = _require(args, "code")
    if isinstance(code, str):
        check_terminal_code(where, code)
    return _report(_terminal_code(where, code, args.get("shell", "auto")))


def run_terminal_file_result(where: Where, rel: Any, shell: Any = "auto", checked: bool = False) -> Dict[str, Any]:
    """Run a shell program file. checked=True (an agent ran it) first refuses code that leaves the folder."""
    root, path, prefix = locate(where, rel)
    if not path.is_file():
        raise ToolError(f"{rel} is not a file in your folder.")
    if path.stat().st_size > MAX_SCRIPT:
        raise ToolError(f"{rel} is too large to run (limit {MAX_SCRIPT // 1024} KB).")
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        raise ToolError(f"{rel} is not a text file.")
    code = data.decode("utf-8", errors="replace")
    selected = shell
    if _shell_name(selected) == "auto":
        ext = path.suffix.lower()
        selected = {".ps1": "powershell", ".bat": "cmd", ".cmd": "cmd", ".sh": "bash", ".bash": "bash",
                    ".zsh": "sh", ".fish": "sh"}.get(ext, "auto")
    if checked:
        check_terminal_code(where, code, cwd=path.parent)
    result = _terminal_code(where, code, selected, cwd=path.parent, suffix=path.suffix or ".sh")
    return {**result, "path": _show(root, path, prefix)}


def run_terminal_file(where: Where, args: Dict[str, Any]) -> str:
    result = run_terminal_file_result(where, _require(args, "path"), args.get("shell", "auto"), checked=True)
    return f"Ran {result['path']}.\n" + _report(result)


# ------------------------------------------------------- repairing escaped code
# Models often escape code twice: line breaks sent as a backslash and an n, quotes sent as a backslash and a
# quote, the whole program wrapped in a Markdown fence. Each repair below changes the code only when it is
# clearly wrong as sent (it does not compile, or a break or quote that must be real sits outside any quotes),
# so code that was fine is never touched.

_FENCE = re.compile(r"^\s*```[\w+-]*[ \t]*\r?\n(.*?)\r?\n?[ \t]*```\s*$", re.DOTALL)
_ESCAPED = {"n": "\n", "r": "", "t": "\t"}
_RUST_CHAR = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]+\}|.)|[^\\'])'")
_JSON_TEXT = re.compile(r"^((?:[\w.-]+[= ])?)\s*([\[{].*[\]}])$", re.DOTALL)
_SINGLE_QUOTED = re.compile(r"'([^']*)'")


def strip_fence(code: str) -> str:
    """The code without a Markdown fence around it, which a model sometimes puts inside the argument."""
    m = _FENCE.match(code)
    return m.group(1) if m else code


def _compiles(code: str) -> bool:
    try:
        compile(code, "agent_code.py", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def _unescape_outside(code: str, kind: str) -> str:
    """Turn a backslash-n (also -r and -t) that sits outside every string into the real character. kind is
    sh (single and double quotes), rust (strings, raw strings, characters) or maxima (double quotes)."""
    out: List[str] = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == "\\" and i + 1 < n:
            nxt = code[i + 1]
            out.append(_ESCAPED[nxt] if nxt in _ESCAPED else code[i:i + 2])
            i += 2
        elif c == "'" and kind == "sh":
            j = code.find("'", i + 1)
            j = n - 1 if j < 0 else j
            out.append(code[i:j + 1])
            i = j + 1
        elif c == "'" and kind == "rust":
            m = _RUST_CHAR.match(code, i)
            out.append(m.group(0) if m else c)
            i = m.end() if m else i + 1
        elif kind == "rust" and c == "r" and (i == 0 or not (code[i - 1].isalnum() or code[i - 1] == "_")) \
                and re.match(r'r#*"', code[i:]):
            hashes = re.match(r"r(#*)\"", code[i:]).group(1)
            end = code.find('"' + hashes, i + 2 + len(hashes))
            end = n if end < 0 else end + 1 + len(hashes)
            out.append(code[i:end])
            i = end
        elif c == '"':
            j = i + 1
            while j < n and code[j] != '"':
                j += 2 if code[j] == "\\" else 1
            out.append(code[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _literal_breaks(code: str, kind: str) -> str:
    """Code sent as one line with backslash-n where its line breaks belong, as real line breaks. Code that
    already has a line break is left alone: its backslash-n are inside strings, where they belong."""
    if "\n" in code or "\\n" not in code:
        return code
    fixed = _unescape_outside(code, kind)
    return fixed if "\n" in fixed else code


def fix_escaped_code(code: str) -> str:
    """Python code that does not compile because it was escaped twice: a fence around it, literal \\n for line
    breaks, or \\" for quotes. The first change that makes it compile is used; otherwise the code is returned as sent."""
    code = strip_fence(code)
    if _compiles(code):
        return code
    breaks = code.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t") if "\n" not in code and "\\n" in code else code
    for candidate in (breaks, code.replace('\\"', '"'), breaks.replace('\\"', '"')):
        if candidate != code and _compiles(candidate):
            return candidate
    return code


def _json_without_backslashes(text: str) -> Optional[str]:
    """text (JSON, or an id and JSON like 'text.insert={...}') with the backslash taken off each quote, when it
    is not valid JSON as sent and is once they are off. Otherwise None."""
    m = _JSON_TEXT.match(text.strip())
    if not m:
        return None
    try:
        json.loads(m.group(2))
        return None
    except ValueError:
        pass
    fixed = m.group(2).replace('\\"', '"')
    try:
        json.loads(fixed)
    except ValueError:
        return None
    return m.group(1) + fixed


def _shell_kind(shell: Any) -> str:
    name = _shell_name(shell)
    if name in ("", "auto"):
        name = _shells_here()[0]
    return "cmd" if name == "cmd" else "sh"


def fix_shell_code(code: str, shell: Any = "auto") -> str:
    """Terminal code, repaired where a model escaped it twice: a fence around it, literal \\n between commands
    outside any quotes, and JSON in single quotes whose quotes carry a backslash (the shell passes the backslash
    on, and the program reports bad JSON). In cmd, which does not read single quotes, JSON in single quotes is
    rewritten with double quotes."""
    code = strip_fence(code)
    kind = _shell_kind(shell)
    if kind != "cmd":
        code = _literal_breaks(code, "sh")

    def quoted(m: "re.Match[str]") -> str:
        inner = m.group(1)
        fixed = _json_without_backslashes(inner)
        if kind != "cmd":
            return f"'{fixed}'" if fixed is not None else m.group(0)
        body = fixed if fixed is not None else inner
        parsed = _JSON_TEXT.match(body.strip())
        if parsed:
            try:
                json.loads(parsed.group(2))
            except ValueError:
                return m.group(0)
            return '"' + body.strip().replace('"', '\\"') + '"'
        return m.group(0)

    return _SINGLE_QUOTED.sub(quoted, code)


def fix_rust_code(code: str) -> str:
    return _literal_breaks(strip_fence(code), "rust")


def fix_cas_code(code: str, system: Any = None) -> str:
    """CAS code repaired like the other languages: SymPy is Python; Maxima has its line breaks restored and
    its string quotes unescaped when every quote was sent with a backslash."""
    code = strip_fence(code)
    if system == "sympy" or (system != "maxima" and re.match(r"\s*(?:import |from \w+ import |print\()", code)):
        return fix_escaped_code(code)
    code = _literal_breaks(code, "maxima")
    quotes = [m.start() for m in re.finditer('"', code)]
    if quotes and all(i > 0 and code[i - 1] == "\\" for i in quotes):
        code = code.replace('\\"', '"')
    return code


# Names small models (Llama in particular) commonly use instead of the real ones.
TOOL_ALIASES = {
    "python": "run_python", "ipython": "run_python", "run_code": "run_python", "execute_python": "run_python",
    "python_exec": "run_python", "code_interpreter": "run_python", "run_python_code": "run_python", "exec_python": "run_python",
    "run_file": "run_python_file", "run_script": "run_python_file", "run_python_script": "run_python_file",
    "execute_file": "run_python_file", "execute_python_file": "run_python_file", "python_file": "run_python_file",
    "run_py": "run_python_file", "exec_file": "run_python_file",
    "rust": "run_rust", "run_rust_code": "run_rust", "execute_rust": "run_rust",
    "run_rs": "run_rust_file", "rust_file": "run_rust_file", "execute_rust_file": "run_rust_file",
    "shell": "run_terminal", "terminal": "run_terminal", "run_shell": "run_terminal", "run_command": "run_terminal",
    "execute_command": "run_terminal", "run_terminal_code": "run_terminal",
    "shell_file": "run_terminal_file", "terminal_file": "run_terminal_file", "run_script_file": "run_terminal_file",
    "ls": "list_files", "list_dir": "list_files", "list_directory": "list_files", "listdir": "list_files",
    "list_folder": "list_files", "list": "list_files",
    "cat": "read_file", "open_file": "read_file", "view_file": "read_file", "get_file": "read_file",
    "create_file": "write_file", "save_file": "write_file", "make_file": "write_file", "write": "write_file",
    "write_to_file": "write_file", "edit_file": "write_file",
    "mkdir": "make_folder", "create_folder": "make_folder", "create_directory": "make_folder",
    "make_directory": "make_folder", "make_dir": "make_folder",
    "rm": "delete_file", "delete": "delete_file", "remove_file": "delete_file", "remove": "delete_file",
    "delete_folder": "delete_file",
    "search": "web_search", "search_web": "web_search", "internet_search": "web_search", "google": "web_search",
    "google_search": "web_search", "bing_search": "web_search", "duckduckgo_search": "web_search", "websearch": "web_search",
    "fetch": "fetch_page", "fetch_url": "fetch_page", "open_url": "fetch_page", "browse": "fetch_page",
    "get_url": "fetch_page", "read_url": "fetch_page", "visit": "fetch_page", "read_page": "fetch_page", "get_page": "fetch_page",
    "wikipedia": "wikipedia_search", "wiki": "wikipedia_search", "search_wikipedia": "wikipedia_search",
    "wiki_search": "wikipedia_search", "wikipedia_page": "wikipedia_article", "get_wikipedia": "wikipedia_article",
    "wiki_article": "wikipedia_article", "wikipedia_summary": "wikipedia_article", "read_wikipedia": "wikipedia_article",
    "get_wikipedia_article": "wikipedia_article", "wikipedia_get": "wikipedia_article",
    "image": "generate_image", "create_image": "generate_image", "make_image": "generate_image",
    "draw": "generate_image", "draw_image": "generate_image", "text_to_image": "generate_image",
    "txt2img": "generate_image", "image_generation": "generate_image", "generate_picture": "generate_image",
    "share": "share_file", "send_file": "share_file", "give_file": "share_file", "present_file": "share_file",
    "download_file": "share_file", "export_file": "share_file", "offer_file": "share_file", "share_files": "share_file",
    "stable_diffusion": "generate_image", "paint": "generate_image",
    "cas": "run_cas", "maxima": "run_cas", "sympy": "run_cas", "run_maxima": "run_cas", "run_sympy": "run_cas",
    "computer_algebra": "run_cas", "symbolic_math": "run_cas", "algebra": "run_cas",
    "ask_agent": "call_agent", "delegate": "call_agent", "delegate_task": "call_agent", "run_agent": "call_agent",
    "call_subagent": "call_agent", "subagent": "call_agent", "agent": "call_agent", "call_agents": "call_agent",
    "craft": "run_craft", "craft_cli": "run_craft", "run_craft_cli": "run_craft", "run_cli": "run_craft",
    "run_app": "run_craft", "run_craft_app": "run_craft", "craft_app": "run_craft",
    "ocr": "read_text", "read_image_text": "read_text", "image_to_text": "read_text", "extract_text": "read_text",
    "read_text_from_image": "read_text", "run_ocr": "read_text", "tesseract": "read_text",
}
ARG_ALIASES = {
    "path": ("path", "filename", "file_name", "file", "file_path", "filepath", "name", "directory", "dir",
             "folder", "folder_path", "folder_name", "dirname", "location", "image", "image_path", "picture"),
    "content": ("content", "contents", "text", "data", "body", "file_content", "value"),
    "code": ("code", "script", "source", "python_code", "program", "python", "source_code", "cmd", "command"),
    "query": ("query", "q", "search", "search_query", "search_term", "term", "terms", "keywords", "topic", "text"),
    "url": ("url", "link", "href", "address", "page", "website", "uri"),
    "title": ("title", "page", "article", "page_title", "topic", "subject", "name", "query"),
    "max_results": ("max_results", "num_results", "number_of_results", "count", "limit", "n", "results", "top_k", "k"),
    "max_chars": ("max_chars", "max_length", "max_characters", "chars", "characters", "length", "size"),
    "start": ("start", "offset", "start_char", "from", "position"),
    "intro_only": ("intro_only", "summary", "summary_only", "intro", "short"),
    "lang": ("lang", "language", "wiki_lang"),
    "prompt": ("prompt", "description", "image_prompt", "text", "query", "positive_prompt"),
    "negative_prompt": ("negative_prompt", "negative", "neg_prompt", "avoid"),
    "steps": ("steps", "num_steps", "sampling_steps", "num_inference_steps"),
    "shell": ("shell", "terminal", "interpreter", "language"),
    "system": ("system", "cas", "engine", "backend"),
    "languages": ("languages", "language", "lang", "langs"),
    "agent": ("agent", "agent_name", "name", "to", "target", "callee", "assistant"),
    "message": ("message", "task", "prompt", "request", "instruction", "instructions", "query", "text", "content"),
    "app": ("app", "program", "cli", "application", "craft", "app_name"),
    "args": ("args", "arguments", "argv", "cli_args", "command_args", "params", "parameters", "options"),
    "find": ("find", "filter", "grep", "contains"),
}


def normalize_call(name: Any, args: Any, allowed: List[str]) -> tuple:
    """Map common variations of tool names and argument names onto the real ones."""
    name = str(name or "").strip()
    if "." in name:  # e.g. "functions.write_file"
        name = name.rsplit(".", 1)[-1]
    key = name.lower().replace("-", "_").replace(" ", "_")
    if key in TOOL_SPECS:
        name = key
    elif key in TOOL_ALIASES and TOOL_ALIASES[key] in allowed:
        name = TOOL_ALIASES[key]
    elif RUN_CRAFT in allowed and _craft_id(key):   # a call named for the program, like photocraft or photocraft_cli
        args = {"args": args} if isinstance(args, str) else dict(args) if isinstance(args, dict) else {}
        args.setdefault("app", _craft_id(key))
        name = RUN_CRAFT
    if name in ("run_maxima", "run_sympy") and "run_cas" in allowed:
        args = dict(args) if isinstance(args, dict) else args
        if isinstance(args, dict):
            args.setdefault("system", name[4:])
        name = "run_cas"
    if not isinstance(args, dict) or name not in TOOL_SPECS:
        return name, args
    out = dict(args)
    for real in TOOL_SPECS[name]["parameters"]["properties"]:
        if real not in out:
            for alt in ARG_ALIASES.get(real, ()):
                if alt in out:
                    out[real] = out.pop(alt)
                    break
        value = out.get(real)
        if isinstance(value, dict) and "value" in value:  # {"type": "string", "value": "..."}
            out[real] = value["value"]
    if name == "run_python" and "code" not in out and "run_python_file" in allowed:
        # {"path": "x.py"} sent to run_python means the file tool.
        target = next((out[k] for k in ARG_ALIASES["path"] if isinstance(out.get(k), str)), None)
        if target and target.strip().lower().endswith(".py"):
            return "run_python_file", {"path": target}
    if name == "run_rust" and "code" not in out and "run_rust_file" in allowed:
        target = next((out[k] for k in ARG_ALIASES["path"] if isinstance(out.get(k), str)), None)
        if target and target.strip().lower().endswith(".rs"):
            return "run_rust_file", {"path": target}
    if name == "run_terminal" and "code" not in out and "run_terminal_file" in allowed:
        target = next((out[k] for k in ARG_ALIASES["path"] if isinstance(out.get(k), str)), None)
        if target:
            return "run_terminal_file", {"path": target, **({"shell": out["shell"]} if "shell" in out else {})}
    if name == RUN_CRAFT:
        line = next((out[k] for k in ("command", "cmd", "code", "script") if isinstance(out.get(k), str)), None)
        if "args" not in out and line:   # a whole command line, like "photocraft-cli info a.png"
            parts = craft_argv(line)
            if "app" not in out and parts and _craft_id(parts[0].lower().replace("-cli", "_cli")):
                out["app"] = _craft_id(parts[0].lower().replace("-cli", "_cli"))
            out["args"] = parts
        if isinstance(out.get("app"), str):
            out["app"] = _craft_id(out["app"].strip().lower().replace("-cli", "_cli")) or out["app"]
        for k in ("command", "cmd", "code", "script"):
            out.pop(k, None)
    elif isinstance(out.get("code"), str):
        fix = {"run_python": fix_escaped_code, "run_rust": fix_rust_code,
               "run_terminal": lambda c: fix_shell_code(c, out.get("shell")),
               "run_cas": lambda c: fix_cas_code(c, out.get("system"))}.get(name)
        if fix:
            out["code"] = fix(out["code"])
    return name, out


# ------------------------------------------------------------------- run_craft
# Runs <app>-cli without a shell. The quoting that breaks agent commands (JSON inside single quotes inside a
# shell command inside a JSON tool call) is not needed: every argument is one list item, and JSON is written as
# an object or list that Axis turns into text. The folder check reads the arguments the way the terminal check
# reads shell code, but looks at whole values (a path, or a string inside JSON), so a document that merely
# contains a slash or a tilde is not refused.

MAX_CRAFT_ARGS = 500


def _craft_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _craft_part(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        raise ToolError("An argument cannot be null.")
    if isinstance(value, bool):
        return "true" if value else "false"
    return _craft_json(value)


def _craft_arg(item: Any) -> str:
    if isinstance(item, dict) and set(item) == {"join"} and isinstance(item["join"], list):
        return "".join(_craft_part(p) for p in item["join"])
    text = _craft_part(item)
    fixed = _json_without_backslashes(text) if isinstance(item, str) else None
    return fixed if fixed is not None else text


def _craft_id(name: str) -> str:
    """The app that a name stands for (photocraft, photocraft_cli), or ''."""
    try:
        from . import artcraft
    except ImportError:
        return ""
    base = name[:-4] if name.endswith("_cli") else name
    return base if base in artcraft.APP_IDS else ""


def craft_argv(value: Any, app: str = "") -> List[str]:
    """The program's arguments from what a model sent: a list (the right form), or a command line as text."""
    if isinstance(value, str):
        text = value.strip()
        parsed = None
        if text.startswith("["):
            try:
                parsed = json.loads(text, strict=False)
            except ValueError:
                parsed = None
        if isinstance(parsed, list):
            value = parsed
        else:
            try:
                parts = shlex.split(text, posix=os.name != "nt")
            except ValueError as exc:
                raise ToolError(f"Could not read args ({exc}). Send args as a list, one item for each argument.")
            value = [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "'\"" else p for p in parts]
    if not isinstance(value, list):
        raise ToolError('args must be a list with one item for each argument, e.g. ["info", "a.png"].')
    if len(value) > MAX_CRAFT_ARGS:
        raise ToolError(f"Too many arguments (limit {MAX_CRAFT_ARGS}). Put the steps in a script file instead.")
    argv = [_craft_arg(v) for v in value]
    if argv and app and argv[0].lower() in (app, app + "-cli", app + "-cli.exe"):
        argv = argv[1:]   # the program's name is already known
    if any("\0" in a for a in argv):
        raise ToolError("An argument has a null character in it.")
    return argv


def _json_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _json_strings(v)]
    return []


def _outside_value(value: str, roots: List[Path], cwd: Path) -> Optional[str]:
    """value if it is one path (no spaces) that names a place outside roots, else None."""
    v = value.strip()
    if not v or len(v) > 500 or re.search(r"\s", v) or _URL.match(v):
        return None
    if v.startswith("~") or _HOME.match(v):
        return v
    absolute = v.startswith(("/", "\\\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", v))
    if not absolute and ".." not in re.split(r"[\\/]", v):
        return None
    if v.lower() in _DEVICES:
        return None
    target = Path(v.replace("\\", "/"))
    return None if _within(target if target.is_absolute() else cwd / target, roots) else v


def check_craft_args(where: Where, argv: List[str]) -> None:
    """Refuse an argument that names a place outside the agent's folder: a path, an option=path, or a string
    inside JSON (alone, or after an id as in text.insert={...})."""
    p = places(where)
    roots = [p.personal.resolve()] + ([p.shared.resolve()] if p.shared is not None else [])
    cwd = p.personal.resolve()
    for arg in argv:
        found = [arg, arg.split("=", 1)[-1]]
        m = _JSON_TEXT.match(arg.strip())
        if m:
            try:
                found += _json_strings(json.loads(m.group(2)))
            except ValueError:
                pass
        for text in found:
            bad = _outside_value(text, roots, cwd)
            if bad:
                raise ToolError(f"The argument '{bad}' is outside your folder. Only {roots[0]} and the folders "
                                "inside it can be used. Use paths inside your folder.")


def find_in_output(text: str, needle: str) -> str:
    """The parts of a program's output that contain needle (not case-sensitive): the items of a JSON list, or
    the lines of text, where an indented line belongs to the line above it."""
    needle = needle.strip().lower()
    if not needle:
        return text
    body = text.strip()
    if body[:1] == "[":
        try:
            items = json.loads(body)
        except ValueError:
            items = None
        if isinstance(items, list):
            lines = [_craft_json(i) for i in items]
            return "\n".join(l for l in lines if needle in l.lower())
    blocks: List[str] = []
    for line in text.splitlines():
        if line[:1].isspace() and blocks:
            blocks[-1] += "\n" + line
        else:
            blocks.append(line)
    return "\n".join(b for b in blocks if needle in b.lower())


def _craft_missing(app: str) -> str:
    from . import craft_skills
    return craft_skills.missing(app)


def run_craft(where: Where, args: Dict[str, Any]) -> str:
    from . import artcraft
    app = str(_require(args, "app") or "").strip().lower()
    for suffix in (".exe", "-cli", "_cli"):
        if app.endswith(suffix):
            app = app[:-len(suffix)]
    if app not in artcraft.APP_IDS:
        raise ToolError("app must be one of: " + ", ".join(artcraft.APP_IDS) + ".")
    argv = craft_argv(_require(args, "args"), app)
    program = artcraft.cli_program(app)
    if program is None:
        raise ToolError(_craft_missing(app))
    check_craft_args(where, argv)
    folder = places(where).personal.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    base, temp = _temp_dir(folder)
    env = _run_env(where, temp)
    try:
        result = _run_capped([str(program)] + argv, "", str(folder), env, TERMINAL_TIMEOUT, _run_kwargs())
    except OSError as exc:
        if getattr(exc, "winerror", None) == 206 or exc.errno == errno.E2BIG:
            raise ToolError("The arguments are too long for one command. Write them to a file with write_file and "
                            "use the app's script option instead.")
        raise ToolError(f"Could not start {program.name}: {exc.strerror or exc}")
    finally:
        _finish_temp(base, temp)
    needle = args.get("find")
    if isinstance(needle, str) and needle.strip() and result["stdout"]:
        kept = find_in_output(result["stdout"], needle)
        result = {**result, "stdout": kept if kept else f"(no line contains '{needle.strip()}')"}
    return _report(result, "(The program printed nothing.)")


def _web(name: str) -> Callable[[Where, Dict[str, Any]], str]:
    def run(_ws: Where, args: Dict[str, Any]) -> str:
        try:
            return web_tools.HANDLERS[name](args)
        except web_tools.WebError as exc:
            raise ToolError(str(exc)) from exc
    return run


def generate_image(ws: Where, args: Dict[str, Any],
                   on_event: Optional[Callable[[Dict[str, Any]], None]] = None, preview: bool = False) -> str:
    """Make a picture. It goes to Axis's images folder, never into the agent's folder."""
    try:
        rec = images.generate(args, images_folder(), source="agent", on_event=on_event, preview=preview)
    except images.ImageError as exc:
        raise ToolError(str(exc)) from exc
    # The page finds the picture from "Saved images/<file>" and "images folder" in this text (see imagePath).
    return (f"Saved images/{rec['path']} ({rec['width']}x{rec['height']}, {rec['steps']} steps, seed {rec['seed']}, "
            f"model {rec['model']}, {rec['seconds']} s) in Axis's images folder, which is not your folder. "
            "The user can see the picture.")


def run_cas(where: Where, args: Dict[str, Any]) -> str:
    """Run Maxima or SymPy code with the agent's folder as the working folder (see cas.py)."""
    from . import cas
    code = _require(args, "code")
    system = args.get("system")
    if isinstance(code, str) and not system and code.lstrip().startswith(("import ", "from ")):
        system = "sympy" if cas.installed("sympy") else system   # Python code sent without saying so
    try:
        return cas.report(cas.run(where, system, code))
    except cas.CasError as exc:
        raise ToolError(str(exc)) from exc


def read_text(where: Where, args: Dict[str, Any]) -> str:
    """Read the text in a picture in the agent's folder with an OCR model (see ocr.py)."""
    from . import ocr
    rel = _require(args, "path")
    root, path, prefix = locate(where, rel)
    if not path.is_file():
        raise ToolError(f"{rel} is not a file in your folder.")
    body: Dict[str, Any] = {}
    if isinstance(args.get("languages"), str) and args["languages"].strip():
        body["languages"] = args["languages"]
    try:
        body["model"] = ocr.pick_model(args.get("model"))
        result = ocr.run(body, path, lambda _event: None)
    except ocr.OcrError as exc:
        raise ToolError(str(exc)) from exc
    facts = [f"{result.get('words')} words" if result.get("words") is not None else "",
             f"confidence {round(result['confidence'])}%" if isinstance(result.get("confidence"), (int, float)) else "",
             f"{result.get('seconds')} s"]
    head = f"Read {_show(root, path, prefix)} with {result.get('name') or body['model']} ({', '.join(f for f in facts if f)})."
    text = (result.get("text") or "").strip()
    return head + ("\n" + _clip(text, MAX_READ) if text else "\nNo text was found in the picture.")


def shared_file(ws: Where, rel: Any) -> Tuple[Path, Path, str]:
    """The file an agent offers the user: (the folder it is in, its full path, how the agent wrote it). It must be a
    file inside the agent's own folders and not one of Axis's bookkeeping files."""
    root, path, prefix = locate(ws, rel)
    shown = _show(root, path, prefix)
    if not path.exists():
        raise ToolError(f"{shown} does not exist in your folder. Make the file first, then share it.")
    if not path.is_file():
        raise ToolError(f"{shown} is a folder. Share one file at a time.")
    if any(part in HIDDEN_NAMES for part in path.relative_to(root).parts):
        raise ToolError(f"{shown} cannot be shared.")
    return root, path, shown


def share_file(ws: Where, args: Dict[str, Any]) -> str:
    _, path, shown = shared_file(ws, _require(args, "path"))
    return f"Shared {shown} ({_size_text(path.stat().st_size)}). The user can download it or open the folder it is in."


# The names the file routes use for where a shared file is (see server.place): the workspace folder, or the one
# folder a chat was given. A shared file is always one of these two, so the page can download it and show it in its folder.
SHARED_PLACE = "_shared"
FOLDER_PLACE = "_folder"


def share_info(ws: Where, rel: Any) -> Dict[str, Any]:
    """What the page needs to offer a shared file: {name, size, place, path} and, for FOLDER_PLACE, {folder}."""
    from .paths import workspace_root
    root, path, _ = shared_file(ws, rel)
    info: Dict[str, Any] = {"name": path.name, "size": path.stat().st_size}
    work = workspace_root().resolve()
    if work in path.parents:
        return {**info, "place": SHARED_PLACE, "path": path.relative_to(work).as_posix()}
    return {**info, "place": FOLDER_PLACE, "folder": str(root), "path": path.relative_to(root).as_posix()}


def _size_text(n: int) -> str:
    for unit, step in (("bytes", 1024), ("KB", 1024), ("MB", 1024), ("GB", 1024)):
        if n < step or unit == "GB":
            return f"{n} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= step
    return f"{n} bytes"


# File management is intentionally not exposed as an agent skill. Agents can use
# the terminal skill for ls/cat/mkdir/rm and the Workspace tab remains available
# to the user.
FILE_TOOLS = ()
EXECUTION_TOOLS = ("run_python", "run_python_file", "run_rust", "run_rust_file", "run_terminal", "run_terminal_file",
                   RUN_CRAFT)
# Actions that work in the agent's folder, so the agent is told about the folder.
FOLDER_TOOLS = EXECUTION_TOOLS + ("run_cas", "read_text", SHARE_FILE)
HANDLERS: Dict[str, Callable[[Where, Dict[str, Any]], str]] = {
    # These handlers remain available to the user-facing Workspace and to
    # compatibility code, but are no longer advertised by any agent skill.
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "make_folder": make_folder,
    "delete_file": delete_file,
    "run_python": run_python,
    "run_python_file": run_python_file,
    "run_rust": run_rust,
    "run_rust_file": run_rust_file,
    "run_terminal": run_terminal,
    "run_terminal_file": run_terminal_file,
    RUN_CRAFT: run_craft,
    "generate_image": generate_image,
    "run_cas": run_cas,
    "read_text": read_text,
    SHARE_FILE: share_file,
    **{n: _web(n) for n in web_tools.HANDLERS},
}


def execute(workspace: Path, name: str, args: Any, allowed: List[str], shared: Optional[Path] = None,
            on_image_event: Optional[Callable[[Dict[str, Any]], None]] = None, image_preview: bool = False) -> tuple:
    """Run one tool. Returns (ok, text). Never raises for tool-level problems.

    shared is the shared folder when the agent may use it, otherwise None. on_image_event
    receives generate_image's progress events (see images.generate), with step previews
    when image_preview is true.
    """
    name, args = normalize_call(name, args, allowed)
    if name == CALL_AGENT:
        return False, "Error: call_agent only works inside a chat reply."
    if name not in allowed or name not in HANDLERS:
        return False, f"Unknown tool '{name}'. Available tools: {', '.join(allowed) or 'none'}."
    if not isinstance(args, dict):
        return False, "Tool arguments must be a JSON object."
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        if name == "generate_image":
            return True, HANDLERS[name](Places(workspace, shared), args, on_event=on_image_event, preview=image_preview)
        return True, HANDLERS[name](Places(workspace, shared), args)
    except ToolError as exc:
        return False, f"Error: {exc}"
    except OSError as exc:
        return False, f"Error: {exc.strerror or exc}"
