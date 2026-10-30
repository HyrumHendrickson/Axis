"""Computer algebra (CAS) for agents and the CAS page: Maxima and SymPy.

Systems
  * Maxima is a program. Axis uses a copy it finds (the path on the CAS page, AXIS_MAXIMA,
    the PATH, or the usual install folders) and can install it: on Windows with winget
    (package MaximaTeam.Maxima; winget checks the installer against the SHA-256 in its
    manifest), on macOS with Homebrew, and on Linux with the system's package manager
    (pkexec asks for the password). Axis removes a Maxima only when Axis installed it, with the
    same package manager; a Maxima that was already on the computer is never removed.
  * SymPy is a Python library. Axis installs it with pip into a Python environment of its
    own, ~/Axis/engines/cas-python, made with venv. Axis itself stays standard-library only.

Running code
  Maxima reads the code on its standard input with --very-quiet, so each result is printed
  as one line (display2d is off) and an error does not stop the commands after it. When
  Maxima stops to ask a question ("Is n positive?"), Axis stops it and says so, because
  nobody is there to answer. SymPy code is Python and runs under the same guard as Run Python.

  Code from an agent is read before it runs: Maxima code that starts programs (system), drops
  into Lisp, or names a file outside the agent's folder is refused. Like the other guards in
  tools.py, this keeps ordinary code in its folder; it is not a sandbox.

Files
    ~/Axis/cas-settings.json       the Maxima path and the CAS agents use by default
    ~/Axis/engines/cas.json        what Axis installed
    ~/Axis/engines/cas-python/     the SymPy environment
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import engines, jobs
from .paths import axis_home, remove_tree

SYSTEMS = ("maxima", "sympy")
LABELS = {"maxima": "Maxima", "sympy": "SymPy"}
RUN_TIMEOUT = float(os.environ.get("AXIS_CAS_TIMEOUT", "60"))
MAX_CODE = 100_000
MAX_OUTPUT = 1024 * 1024
WINGET_ID = "MaximaTeam.Maxima"
QUESTION = "Acceptable answers are"


class CasError(Exception):
    """A problem with the CAS that the user (or an agent) can act on."""

    user_facing = True


# ------------------------------------------------------------------ files and settings


def _read_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _settings_path() -> Path:
    return axis_home() / "cas-settings.json"


def _record_path() -> Path:
    return engines.root() / "cas.json"


def env_dir() -> Path:
    return engines.root() / "cas-python"


_LOCK = threading.RLock()


def load_settings() -> Dict[str, Any]:
    data = _read_json(_settings_path(), {})
    default = data.get("default") if data.get("default") in SYSTEMS else ""
    return {"maxima": str(data.get("maxima") or ""), "default": default}


def save_settings(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise CasError("Settings must be a JSON object.")
    current = load_settings()
    if "maxima" in body:
        value = body["maxima"] if body["maxima"] is not None else ""
        if not isinstance(value, str):
            raise CasError("The Maxima path must be text.")
        value = value.strip().strip('"').strip()
        if len(value) > 1000:
            raise CasError("The Maxima path is too long.")
        current["maxima"] = value
    if "default" in body:
        if body["default"] not in SYSTEMS + ("", None):
            raise CasError("Choose Maxima or SymPy.")
        current["default"] = body["default"] or ""
    with _LOCK:
        _write_json(_settings_path(), current)
    _PROBE.clear()
    return status()


def _remember(key: str, record: Optional[Dict[str, Any]]) -> None:
    with _LOCK:
        data = _read_json(_record_path(), {})
        if record is None:
            data.pop(key, None)
        else:
            data[key] = record
        _write_json(_record_path(), data)


# ------------------------------------------------------------------ Maxima: finding it


def _usual_maxima() -> List[Path]:
    if os.name == "nt":
        bases: List[Path] = [Path(os.environ.get("SystemDrive", "C:") + "\\")]
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if os.environ.get(var):
                bases.append(Path(os.environ[var]))
                if var == "LOCALAPPDATA":
                    bases.append(Path(os.environ[var]) / "Programs")
        found: List[Path] = []
        for base in bases:
            try:
                dirs = sorted(base.glob("[Mm]axima*"), reverse=True)   # the newest version first
            except OSError:
                continue
            found += [d / "bin" / "maxima.bat" for d in dirs]
        return found
    return [Path(p) for p in ("/opt/homebrew/bin/maxima", "/usr/local/bin/maxima", "/opt/local/bin/maxima",
                              "/usr/bin/maxima")]


def maxima_binary() -> Optional[Path]:
    """Maxima from the settings, AXIS_MAXIMA, the PATH, or a usual install location."""
    for value in (load_settings()["maxima"], os.environ.get("AXIS_MAXIMA", "")):
        if value:
            p = Path(value).expanduser()
            if p.is_dir():
                for cand in (p / "maxima", p / "maxima.bat", p / "bin" / "maxima", p / "bin" / "maxima.bat"):
                    if cand.is_file():
                        return cand.resolve()
            if p.is_file():
                return p.resolve()
            found = shutil.which(value)
            return Path(found).resolve() if found else None
    found = shutil.which("maxima")
    if found:
        return Path(found).resolve()
    for p in _usual_maxima():
        try:
            if p.is_file():
                return p.resolve()
        except OSError:
            pass
    return None


_PROBE: Dict[str, Any] = {}
_PROBE_TTL = 60.0


def maxima_info(binary: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """{version, works} for a Maxima program, remembered for a minute."""
    binary = binary or maxima_binary()
    if binary is None:
        return None
    key = str(binary)
    hit = _PROBE.get(key)
    if hit and time.time() - hit[0] < _PROBE_TTL:
        return hit[1]
    code, out = engines._quiet_run([str(binary), "--version"], timeout=60)
    m = re.search(r"Maxima\s+(\d+(?:\.\d+)+\S*)", out)
    info = {"version": m.group(1) if m else "", "works": code == 0 and bool(m)}
    _PROBE[key] = (time.time(), info)
    return info


# ------------------------------------------------------------------ Maxima: installing it


def _linux_install_command() -> Optional[List[str]]:
    for prog, cmd in (("apt-get", ["apt-get", "install", "-y", "maxima"]),
                      ("dnf", ["dnf", "install", "-y", "maxima"]),
                      ("pacman", ["pacman", "-S", "--noconfirm", "--needed", "maxima"]),
                      ("zypper", ["zypper", "--non-interactive", "install", "maxima"])):
        if shutil.which(prog):
            return cmd
    return None


def _linux_remove_command() -> Optional[List[str]]:
    for prog, cmd in (("apt-get", ["apt-get", "remove", "-y", "maxima"]),
                      ("dnf", ["dnf", "remove", "-y", "maxima"]),
                      ("pacman", ["pacman", "-R", "--noconfirm", "maxima"]),
                      ("zypper", ["zypper", "--non-interactive", "remove", "maxima"])):
        if shutil.which(prog):
            return cmd
    return None


def _brew() -> Optional[str]:
    found = shutil.which("brew")
    if found:
        return found
    for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if Path(p).is_file():
            return p
    return None


def _winget_command() -> List[str]:
    return [shutil.which("winget") or "winget", "install", "--id", WINGET_ID, "--exact", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]


def maxima_install_plan() -> Dict[str, Any]:
    """How Axis would install Maxima here: {how, note, command?}."""
    osname, _ = engines.this_platform()
    if osname == "windows":
        if shutil.which("winget"):
            return {"how": "winget", "command": "winget install --id " + WINGET_ID + " --exact",
                    "note": "Axis installs Maxima with winget, which checks the installer before it runs. "
                            "Windows may ask for permission."}
        return {"how": None, "note": "Install Maxima from maxima.sourceforge.io, or get App Installer (winget) from "
                                     "the Microsoft Store, then click Refresh."}
    if osname == "macos":
        if _brew():
            return {"how": "brew", "command": "brew install maxima",
                    "note": "Axis installs Maxima with Homebrew (brew install maxima)."}
        return {"how": None, "note": "Install Homebrew from brew.sh, then click Install Maxima. Or install Maxima "
                                     "yourself and set its path below."}
    cmd = _linux_install_command()
    if cmd is None:
        return {"how": None, "note": "Install Maxima with your system's package manager, then click Refresh."}
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    gui = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if root:
        return {"how": "package", "command": " ".join(cmd), "note": f"Axis runs {' '.join(cmd)}."}
    if shutil.which("pkexec") and gui:
        return {"how": "package", "command": "sudo " + " ".join(cmd),
                "note": f"Axis runs {' '.join(cmd)}; your system asks for your password."}
    return {"how": None, "command": "sudo " + " ".join(cmd),
            "note": f"Run this in a terminal, then click Refresh: sudo {' '.join(cmd)}"}


def _run_install(cmd: List[str], job: jobs.Job, env: Dict[str, str], what: str) -> None:
    from . import ocr   # its program runner stops the program when the job is cancelled
    job.progress(phase="Running " + " ".join(Path(cmd[0]).name if i == 0 else c for i, c in enumerate(cmd)))
    try:
        code, lines = ocr._run(cmd, job, on_line=lambda line: job.progress(phase=line[:160]), env=env, timeout=3600)
    except ocr.OcrError as exc:
        raise CasError(str(exc)) from exc
    if code != 0:
        tail = lines.tail(30)
        hint = ""
        if code in (126, 127) and cmd[0] == "pkexec":
            hint = "The password was not given. "
        elif "unable to locate package" in tail.lower():
            hint = "Your package list is out of date: run sudo apt-get update in a terminal first. "
        elif "no package found" in tail.lower():
            hint = "winget could not find Maxima. Run winget source update in a terminal, then try again. "
        raise CasError(f"{hint}Installing {what} failed (code {code}).\n{lines.tail()}")


def install_maxima(job: jobs.Job) -> Dict[str, Any]:
    plan = maxima_install_plan()
    if plan["how"] is None:
        raise CasError(plan["note"])
    if plan["how"] == "winget":
        job.cancellable = False   # the installer cannot be stopped halfway safely
        _run_install(_winget_command(), job, {}, "Maxima")
    elif plan["how"] == "brew":
        _run_install([_brew() or "brew", "install", "maxima"], job, {"HOMEBREW_NO_ENV_HINTS": "1"}, "Maxima")
    else:
        cmd = _linux_install_command() or []
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            cmd = ["pkexec"] + cmd
        _run_install(cmd, job, {"DEBIAN_FRONTEND": "noninteractive"}, "Maxima")
    _PROBE.clear()
    binary = maxima_binary()
    if binary is None:
        raise CasError("Maxima was installed but Axis cannot find it. Set its path on the CAS page.")
    info = maxima_info(binary) or {}
    _remember("maxima", {"method": plan["how"], "binary": str(binary), "version": info.get("version"),
                         "installed": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {"system": "maxima", "model": "Maxima " + (info.get("version") or "")}


def maxima_record() -> Optional[Dict[str, Any]]:
    """What Axis recorded when it installed Maxima, or None when Axis did not install it."""
    record = _read_json(_record_path(), {}).get("maxima")
    return record if isinstance(record, dict) and record.get("method") else None


def remove_maxima(job: jobs.Job) -> Dict[str, Any]:
    """Uninstall the Maxima that Axis installed, with the package manager that installed it."""
    record = maxima_record()
    if record is None:
        raise CasError("Axis did not install this Maxima, so it does not remove it. Remove it the way you remove "
                       "other programs.")
    method = record["method"]
    if method == "winget":
        job.cancellable = False
        cmd = [shutil.which("winget") or "winget", "uninstall", "--id", WINGET_ID, "--exact", "--silent",
               "--accept-source-agreements"]
        env: Dict[str, str] = {}
    elif method == "brew":
        cmd, env = [_brew() or "brew", "uninstall", "maxima"], {"HOMEBREW_NO_ENV_HINTS": "1"}
    else:
        cmd = _linux_remove_command() or []
        if not cmd:
            raise CasError("Axis cannot tell which package manager installed Maxima. Remove it yourself.")
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            cmd = ["pkexec"] + cmd
        env = {"DEBIAN_FRONTEND": "noninteractive"}
    _run_install(cmd, job, env, "the removal of Maxima")
    _PROBE.clear()
    _remember("maxima", None)
    return {"system": "maxima", "model": "Maxima"}


# ------------------------------------------------------------------ SymPy


def env_python() -> Optional[Path]:
    folder = env_dir()
    for cand in (folder / "Scripts" / "python.exe", folder / "bin" / "python3", folder / "bin" / "python"):
        if cand.is_file():
            return cand
    return None


def sympy_info() -> Optional[Dict[str, Any]]:
    py = env_python()
    record = _read_json(_record_path(), {}).get("sympy")
    if py is None or not isinstance(record, dict) or not record.get("version"):
        return None
    return {**record, "python": str(py)}


def _base_python() -> str:
    from . import tools
    return tools.python_executable()


def install_sympy(job: jobs.Job) -> Dict[str, Any]:
    folder = env_dir()
    folder.parent.mkdir(parents=True, exist_ok=True)
    py = env_python()
    if py is None:
        job.progress(phase="Making a Python environment for SymPy")
        code, out = engines._quiet_run([_base_python(), "-m", "venv", str(folder)], timeout=600)
        py = env_python()
        if code != 0 or py is None:
            remove_tree(folder)
            hint = ""
            if "ensurepip" in out or "venv" in out.lower():
                hint = ("This Python cannot make environments. On Debian and Ubuntu install python3-venv "
                        "(sudo apt install python3-venv). ")
            raise CasError(f"{hint}Making the SymPy environment failed.\n{out.strip()[-800:]}")
    job.check()
    _run_install([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "sympy"],
                 job, {"PIP_NO_INPUT": "1", "PYTHONUTF8": "1"}, "SymPy")
    job.progress(phase="Checking that SymPy works")
    code, out = engines._quiet_run([str(py), "-c", "import sympy; print(sympy.__version__)"], timeout=120)
    version = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    if not version:
        raise CasError("SymPy was installed but cannot be loaded.\n" + out.strip()[-800:])
    _remember("sympy", {"version": version, "installed": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {"system": "sympy", "model": "SymPy " + version}


# ------------------------------------------------------------------ what the page asks for


def _system(body: Any) -> str:
    if not isinstance(body, dict) or body.get("system") not in SYSTEMS:
        raise CasError("Choose Maxima or SymPy.")
    return body["system"]


def installed(system: str) -> bool:
    if system == "maxima":
        info = maxima_info()
        return bool(info and info.get("works"))
    return sympy_info() is not None


def install(body: Any) -> Dict[str, Any]:
    system = _system(body)
    if installed(system):
        raise CasError(f"{LABELS[system]} is already installed.")
    work = install_maxima if system == "maxima" else install_sympy
    job = jobs.submit("install", "cas", system, f"Installing {LABELS[system]}", work)
    return {"job": job.public()}


def remove(body: Any) -> Dict[str, Any]:
    """Uninstall SymPy (delete its environment) or the Maxima that Axis installed."""
    system = _system(body)
    if jobs.busy("cas", system):
        raise CasError(f"{LABELS[system]} is being installed or removed. Wait for it to finish, or cancel it.")
    if system == "maxima":
        if maxima_record() is None:
            raise CasError("Axis did not install this Maxima, so it does not remove it. Remove it the way you "
                           "remove other programs.")
        return {"job": jobs.submit("delete", "cas", system, "Removing Maxima", remove_maxima).public()}
    remove_tree(env_dir())
    if env_dir().exists():
        raise CasError("Some files of the SymPy environment are in use. Try again in a moment.")
    _remember("sympy", None)
    return status()


def default_system() -> Optional[str]:
    """The CAS agents use when they do not name one: the chosen one if installed, else the first installed."""
    chosen = load_settings()["default"]
    if chosen and installed(chosen):
        return chosen
    return next((s for s in SYSTEMS if installed(s)), None)


def status() -> Dict[str, Any]:
    binary = maxima_binary()
    info = maxima_info(binary) if binary else None
    record = _read_json(_record_path(), {})
    sym = sympy_info()
    return {
        "systems": [
            {"id": "maxima", "name": "Maxima", "installed": bool(info and info.get("works")),
             "version": (info or {}).get("version") or None, "path": str(binary) if binary else None,
             "by_axis": isinstance(record.get("maxima"), dict), "plan": maxima_install_plan(),
             "removable": bool(info and info.get("works") and maxima_record() is not None)},
            {"id": "sympy", "name": "SymPy", "installed": sym is not None, "version": (sym or {}).get("version"),
             "path": str(env_dir()) if sym else None, "by_axis": True,
             "plan": {"how": "pip", "note": "Axis makes a Python environment of its own and installs SymPy into "
                                           "it with pip. No administrator rights are needed."},
             "removable": sym is not None},
        ],
        "settings": load_settings(),
        "default": default_system(),
        "jobs": [j for j in jobs.listing() if j["engine"] == "cas"],
    }


# ------------------------------------------------------------------ running code


_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
_REFUSED = (
    (re.compile(r"\bsystem\s*\("), "system() starts other programs"),
    (re.compile(r":lisp\b|\bto_lisp\b"), "Lisp code can do anything on the computer"),
    (re.compile(r"(?:^|[^\w?])\?\s*[A-Za-z%*\\]"), "?name reaches Lisp directly (use describe(name) for help)"),
)


def check_maxima_code(where: Any, code: str, cwd: Optional[Path] = None) -> None:
    """Refuse Maxima code that starts programs, uses Lisp, or names a file outside the folder."""
    from . import tools
    p = tools.places(where)
    roots = [p.personal.resolve()] + ([p.shared.resolve()] if p.shared is not None else [])
    text = _COMMENT.sub(" ", code)
    bare = _STRING.sub('""', text)
    for pattern, why in _REFUSED:
        if pattern.search(bare):
            raise CasError(f"The code was not run: {why}. Use plain Maxima commands.")
    base = Path(cwd or p.personal).resolve()
    for m in _STRING.finditer(text):
        literal = m.group(1).replace("\\\\", "\\")
        if literal.strip().lower().endswith((".lisp", ".lsp", ".fasl", ".fas")):
            raise CasError("The code was not run: Lisp files can do anything on the computer. Use plain Maxima commands.")
        bad = tools._outside_in(literal, roots, base)
        if bad:
            raise CasError(f"The code names '{bad}', which is outside your folder. Only {roots[0]} and the folders "
                           "inside it can be used. Use paths inside your folder.")


def _maxima_program(code: str) -> str:
    body = code.rstrip()
    bare = _COMMENT.sub("", body).rstrip()
    if bare and bare[-1] not in ";$":
        body += ";"   # without a final ; Maxima waits for more and then reports the end of the input
    return "display2d:false$ linel:10000$\n" + body + "\n"


def _clean_maxima(out: str) -> Tuple[str, Optional[str]]:
    """Maxima's output without its start-up warnings, cut where it asked a question (returned too)."""
    lines = out.replace("\r\n", "\n").split("\n")
    kept: List[str] = []
    skip = False
    for line in lines:
        if "unable to set up the help system" in line:
            skip = True
            continue
        if skip and (line.startswith("(Details:") or not line.strip()):
            skip = line.startswith("(Details:")
            continue
        skip = False
        kept.append(line)
    question = None
    for i, line in enumerate(kept):
        if QUESTION in line:
            j = i - 1
            while j >= 0 and not kept[j].strip():
                j -= 1
            question = kept[j].strip() if j >= 0 else ""
            kept = kept[:max(j, 0)]
            break
    return "\n".join(kept).strip("\n"), question


def run_maxima(code: str, folder: Path, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Run Maxima code in folder. Returns {exit, stdout, stderr, timed_out, question}."""
    from . import ocr, tools
    binary = maxima_binary()
    if binary is None or not installed("maxima"):
        raise CasError("Maxima is not installed. Install it on the CAS page (Tools, CAS).")
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / ".axis-tmp"
    with tools._TMP_LOCK:
        base.mkdir(exist_ok=True)
        temp = Path(tempfile.mkdtemp(dir=str(base)))
    run_env = dict(env or os.environ)
    run_env.update({"MAXIMA_TEMPDIR": str(temp), "TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)})
    kept = bytearray()
    asked = threading.Event()
    try:
        try:
            proc = ocr._popen([str(binary), "--very-quiet"], env=run_env, cwd=folder, stdin=subprocess.PIPE)
        except OSError as exc:
            raise CasError(f"Could not start Maxima: {exc.strerror or exc}") from exc

        def read() -> None:
            try:
                while True:
                    chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
                    if not chunk:
                        break
                    if len(kept) < MAX_OUTPUT:
                        kept.extend(chunk[:MAX_OUTPUT - len(kept)])
                    if QUESTION.encode() in kept[-len(chunk) - 64:]:
                        asked.set()
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            proc.stdin.write(_maxima_program(code).encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass
        deadline = time.time() + RUN_TIMEOUT
        timed_out = False
        while proc.poll() is None:
            if asked.is_set():
                break
            if time.time() > deadline:
                timed_out = True
                break
            time.sleep(0.05)
        if proc.poll() is None:
            ocr._kill(proc)
        else:
            _settle(proc)   # a plot is drawn by gnuplot, which can still be running
            ocr._kill(proc)
        reader.join(5)
        ocr._close(proc)
        saved = _collect(temp, folder)
    finally:
        with tools._TMP_LOCK:
            shutil.rmtree(temp, ignore_errors=True)
            try:
                base.rmdir()   # only succeeds when no other run is using it
            except OSError:
                pass
    text, question = _clean_maxima(bytes(kept).decode("utf-8", "replace"))
    if len(kept) >= MAX_OUTPUT:
        text += f"\n... (output cut off after {MAX_OUTPUT // 1024} KB)"
    for old_name, new_name in saved:
        text = text.replace(str(temp / old_name), new_name)   # Maxima prints the full scratch path of a plot
    return {"exit": None if timed_out else proc.returncode, "stdout": text, "stderr": "", "timed_out": timed_out,
            "question": question, "saved": [n for _, n in saved]}


SETTLE_TIMEOUT = 15.0


def _settle(proc: subprocess.Popen) -> None:
    """Wait (a little) for programs Maxima started, such as gnuplot, which finish after Maxima itself."""
    if os.name == "nt":
        time.sleep(0.5)
        return
    deadline = time.time() + SETTLE_TIMEOUT
    while time.time() < deadline:
        try:
            os.killpg(proc.pid, 0)   # is anything left in Maxima's process group?
        except OSError:
            return
        time.sleep(0.1)


def _collect(temp: Path, folder: Path) -> List[Tuple[str, str]]:
    """Move files Maxima wrote to its scratch folder (plots with a relative name land there) into the folder.

    Maxima's own scratch files are left behind. An existing file is never replaced: the new one gets a
    number (plot-2.png). Returns (name in scratch, name in folder) pairs.
    """
    moved: List[Tuple[str, str]] = []
    try:
        entries = sorted(temp.iterdir())
    except OSError:
        return moved
    for f in entries:
        if not f.is_file() or f.name.startswith("maxout") or f.suffix.lower() in (".gnuplot", ".gnuplot_pipes"):
            continue
        dest, n = folder / f.name, 2
        while dest.exists():
            dest, n = folder / f"{f.stem}-{n}{f.suffix}", n + 1
        try:
            shutil.move(str(f), str(dest))
        except OSError:
            continue
        moved.append((f.name, dest.name))
    return moved


def run(where: Any, system: Optional[str], code: Any, checked: bool = True) -> Dict[str, Any]:
    """Run CAS code with the agent's folder (or the workspace) as the working folder.

    checked=True (an agent's code) refuses Maxima code that leaves the folder first. Returns
    {system, exit, stdout, stderr, timed_out, question}.
    """
    from . import tools
    if not isinstance(code, str) or not code.strip():
        raise CasError("Give some code to run.")
    if len(code) > MAX_CODE:
        raise CasError(f"The code is too long (limit {MAX_CODE // 1000} KB).")
    if system in (None, "", "auto"):
        system = default_system()
        if system is None:
            raise CasError("No computer algebra system is installed. Install Maxima or SymPy on the CAS page "
                           "(Tools, CAS).")
    system = str(system).strip().lower()
    if system not in SYSTEMS:
        raise CasError("system must be maxima or sympy.")
    if not installed(system):
        raise CasError(f"{LABELS[system]} is not installed. Install it on the CAS page (Tools, CAS).")
    folder = tools.places(where).personal
    if system == "maxima":
        if checked:
            check_maxima_code(where, code)
        result = run_maxima(code, folder, tools._run_env(where))
    else:
        py = env_python()
        assert py is not None
        try:
            result = tools.run_code(where, tools.fix_escaped_code(code), python=str(py))
        except tools.ToolError as exc:
            raise CasError(str(exc)) from exc
        result["question"] = None
    return {"system": system, **result}


def report(result: Dict[str, Any]) -> str:
    """The result as an agent reads it."""
    label = LABELS[result["system"]]
    if result["timed_out"]:
        return f"Stopped: {label} ran longer than {int(RUN_TIMEOUT)} seconds." + (
            "\nOutput so far:\n" + result["stdout"] if result["stdout"] else "")
    parts = [f"{label} finished." if result["system"] == "maxima" else f"{label} exit code: {result['exit']}"]
    if result["stdout"]:
        parts.append("Output:\n" + result["stdout"].rstrip())
    if result["stderr"]:
        parts.append("Errors:\n" + result["stderr"].rstrip())
    if result.get("saved"):
        parts.append("Saved in your folder: " + ", ".join(result["saved"]))
    if result.get("question") is not None:
        parts.append(f"Maxima stopped to ask: {result['question'] or 'a question'} Nobody can answer it here, so "
                     "state the facts first with assume() or declare() (for example assume(n > 0)) and run the "
                     "code again.")
    if not result["stdout"] and not result["stderr"] and result.get("question") is None and not result.get("saved"):
        parts.append("(Nothing was printed. End a command with ; to show its result, or use print().)")
    text = "\n".join(parts)
    return text if len(text) <= 8000 else text[:8000] + f"\n... ({len(text) - 8000} more characters not shown)"
