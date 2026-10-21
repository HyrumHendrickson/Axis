"""Actions an agent can take. Every action works inside the agent's own folder.

The file tools refuse any path outside the folder. Python code runs with the
folder as its working directory, and a guard blocks writing, deleting or
renaming files outside it and starting other programs. The guard keeps
ordinary code contained; it is not a security sandbox against deliberately
hostile code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

MAX_OUTPUT = 8000
MAX_READ = 16000
PYTHON_TIMEOUT = 60


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- definitions

TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "list_files": {
        "description": "List files and folders in your folder. Use path to look inside a subfolder.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Subfolder to list, relative to your folder. Omit for the top level."}},
            "required": [],
        },
    },
    "read_file": {
        "description": "Read a text file from your folder.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path relative to your folder, e.g. notes/todo.txt"}},
            "required": ["path"],
        },
    },
    "write_file": {
        "description": "Create or overwrite a text file in your folder. Missing folders are created.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to your folder, e.g. report.md"},
                "content": {"type": "string", "description": "The full text to write"},
            },
            "required": ["path", "content"],
        },
    },
    "make_folder": {
        "description": "Create a folder (and any missing parent folders) in your folder.",
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
        "description": "Run Python 3 code with your folder as the working directory and return what it prints. "
        "Use print() to show results. Files the code creates are saved in your folder.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Complete Python program to run"}},
            "required": ["code"],
        },
    },
}


def specs(names: List[str]) -> List[Dict[str, Any]]:
    """Tool definitions in the OpenAI/Ollama function format."""
    return [{"type": "function", "function": {"name": n, **TOOL_SPECS[n]}} for n in names if n in TOOL_SPECS]


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


def list_files(ws: Path, args: Dict[str, Any]) -> str:
    folder = resolve(ws, args.get("path"), allow_root=True)
    if not folder.exists():
        raise ToolError(f"{args.get('path')} does not exist.")
    if not folder.is_dir():
        raise ToolError(f"{args.get('path')} is a file, not a folder.")
    entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    entries = [e for e in entries if e.name != ".axis-tmp"]
    if not entries:
        return f"{_rel(ws, folder)} is empty."
    lines = []
    for e in entries[:500]:
        lines.append(f"{e.name}/" if e.is_dir() else f"{e.name}  ({e.stat().st_size} bytes)")
    if len(entries) > 500:
        lines.append(f"... and {len(entries) - 500} more")
    return "\n".join(lines)


def read_file(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve(ws, _require(args, "path"))
    if not path.is_file():
        raise ToolError(f"{args['path']} is not a file in your folder.")
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        raise ToolError(f"{args['path']} is a binary file and cannot be shown as text.")
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_READ:
        return text[:MAX_READ] + (f"\n... (file continues for {len(text) - MAX_READ} more characters; "
                                  "use run_python to read a specific part if you need it)")
    return text


def write_file(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve(ws, _require(args, "path"))
    content = _require(args, "content")
    if not isinstance(content, str):
        content = str(content)
    if path.is_dir():
        raise ToolError(f"{args['path']} is a folder.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Wrote {_rel(ws, path)} ({len(content.encode('utf-8'))} bytes)."


def make_folder(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve(ws, _require(args, "path"))
    if path.is_file():
        raise ToolError(f"{args['path']} is already a file.")
    path.mkdir(parents=True, exist_ok=True)
    return f"Folder {_rel(ws, path)} is ready."


def delete_file(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve(ws, _require(args, "path"))
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
# guard, then runs the program.
BOOTSTRAP = r'''
import os, sys
_code = sys.stdin.read()
_WS = os.path.normcase(os.path.realpath(os.getcwd()))
sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())
def _inside(p):
    if isinstance(p, int):
        return True
    try:
        full = os.path.normcase(os.path.realpath(os.fsdecode(p)))
    except Exception:
        return False
    return full == _WS or full.startswith(_WS.rstrip(os.sep) + os.sep)
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
sys.argv = ["agent_code.py"]
_g = {"__name__": "__main__", "__builtins__": __builtins__}
try:
    exec(compile(_code, "agent_code.py", "exec"), _g)
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


def run_python(ws: Path, args: Dict[str, Any]) -> str:
    code = _require(args, "code")
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code must be a non-empty Python program.")
    code = fix_escaped_code(code)
    ws.mkdir(parents=True, exist_ok=True)
    tmp = ws / ".axis-tmp"
    tmp.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update({"TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp), "MPLCONFIGDIR": str(tmp),
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg"})
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = None
    try:
        for exe in dict.fromkeys([python_executable(), sys.executable]):
            try:
                proc = subprocess.run(
                    [exe, "-c", BOOTSTRAP], input=code, cwd=str(ws), env=env,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=PYTHON_TIMEOUT, **kwargs,
                )
                break
            except OSError:  # this interpreter cannot be started; try the next one
                continue
    except subprocess.TimeoutExpired:
        return f"Stopped: the code ran longer than {PYTHON_TIMEOUT} seconds."
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if proc is None:
        raise ToolError("Python could not be started on this computer.")
    parts = [f"Exit code: {proc.returncode}"]
    if proc.stdout:
        parts.append("Output:\n" + proc.stdout.rstrip())
    if proc.stderr:
        parts.append("Errors:\n" + proc.stderr.rstrip())
    if not proc.stdout and not proc.stderr:
        parts.append("(The code printed nothing. Use print() to show results.)")
    return _clip("\n".join(parts))


def fix_escaped_code(code: str) -> str:
    """Small models sometimes send code with literal \\n instead of line breaks."""
    if "\n" in code or "\\n" not in code:
        return code
    try:
        compile(code, "agent_code.py", "exec")
        return code
    except SyntaxError:
        pass
    fixed = code.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    try:
        compile(fixed, "agent_code.py", "exec")
        return fixed
    except SyntaxError:
        return code


# Names small models (Llama in particular) commonly use instead of the real ones.
TOOL_ALIASES = {
    "python": "run_python", "ipython": "run_python", "run_code": "run_python", "execute_python": "run_python",
    "python_exec": "run_python", "code_interpreter": "run_python", "run_python_code": "run_python", "exec_python": "run_python",
    "ls": "list_files", "list_dir": "list_files", "list_directory": "list_files", "listdir": "list_files",
    "list_folder": "list_files", "list": "list_files",
    "cat": "read_file", "open_file": "read_file", "view_file": "read_file", "get_file": "read_file",
    "create_file": "write_file", "save_file": "write_file", "make_file": "write_file", "write": "write_file",
    "write_to_file": "write_file", "edit_file": "write_file",
    "mkdir": "make_folder", "create_folder": "make_folder", "create_directory": "make_folder",
    "make_directory": "make_folder", "make_dir": "make_folder",
    "rm": "delete_file", "delete": "delete_file", "remove_file": "delete_file", "remove": "delete_file",
    "delete_folder": "delete_file",
}
ARG_ALIASES = {
    "path": ("path", "filename", "file_name", "file", "file_path", "filepath", "name", "directory", "dir",
             "folder", "folder_path", "folder_name", "dirname", "location"),
    "content": ("content", "contents", "text", "data", "body", "file_content", "value"),
    "code": ("code", "script", "source", "python_code", "program", "python", "source_code", "cmd", "command"),
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
    if name == "run_python" and isinstance(out.get("code"), str):
        out["code"] = fix_escaped_code(out["code"])
    return name, out


HANDLERS: Dict[str, Callable[[Path, Dict[str, Any]], str]] = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "make_folder": make_folder,
    "delete_file": delete_file,
    "run_python": run_python,
}


def execute(workspace: Path, name: str, args: Any, allowed: List[str]) -> tuple:
    """Run one tool. Returns (ok, text). Never raises for tool-level problems."""
    name, args = normalize_call(name, args, allowed)
    if name not in allowed or name not in HANDLERS:
        return False, f"Unknown tool '{name}'. Available tools: {', '.join(allowed) or 'none'}."
    if not isinstance(args, dict):
        return False, "Tool arguments must be a JSON object."
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        return True, HANDLERS[name](workspace, args)
    except ToolError as exc:
        return False, f"Error: {exc}"
    except OSError as exc:
        return False, f"Error: {exc.strerror or exc}"
