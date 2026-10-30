"""Installs and opens the Craft App Suite, and a portable copy of Rust.

The Craft App Suite is ArtCraft's set of free, open-source creative apps (PhotoCraft,
VectorCraft, WordCraft and ten more). Each app publishes ready-built programs on its own
GitHub page. Axis downloads the portable build for this computer from the app's latest
release, checks it against the SHA-256 the release publishes, and unpacks it into its own
folder. Nothing is installed system-wide and no administrator rights are needed.

    Windows   the portable zip: unzip and run <app>.exe
    Linux     the release tarball: bin/<app>
    macOS     the .app inside the release .dmg, copied out (never into /Applications)

Rust is installed with rustup into folders of its own, and Axis never edits the PATH:

    ~/Axis/engines/artcraft.json              what is installed
    ~/Axis/engines/artcraft/<app>/<folder>/   one app
    ~/Axis/engines/rust/rustup/, cargo/       RUSTUP_HOME and CARGO_HOME

The ready-built apps do not need Rust to run. It is installed because it was asked for, and
an app opened from Axis is started with this Rust on its PATH.
"""

from __future__ import annotations

import json
import os
import plistlib
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, engines, jobs, procs
from .paths import inside, remove_tree, replace_retry, unlink_quiet

REPO_OWNER = "storytold"

# (id, name, what it is for). The id is the repository name and the start of every file name.
APPS: List[Tuple[str, str, str]] = [
    ("photocraft", "PhotoCraft", "Image editing: layers, masks, type and real PSD files"),
    ("lightcraft", "LightCraft", "Photo library and raw development"),
    ("filmcraft", "FilmCraft", "Video editing, color and sound"),
    ("designcraft", "DesignCraft", "Page layout and publishing"),
    ("vectorcraft", "VectorCraft", "Vector illustration"),
    ("pdfcraft", "PdfCraft", "Reading, organizing and protecting PDFs"),
    ("effectcraft", "EffectCraft", "Motion graphics and visual effects"),
    ("wordcraft", "WordCraft", "Word processing and documents"),
    ("deckcraft", "DeckCraft", "Presentations and slide decks"),
    ("gridcraft", "GridCraft", "Spreadsheets and data"),
    ("cadcraft", "CADCraft", "Computer-aided design"),
    ("soundcraft", "SoundCraft", "Audio recording and editing"),
    ("figcraft", "FigCraft", "Interface design and prototyping"),
]
APP_IDS = [a[0] for a in APPS]
RUST = "rust"

# The CPU names in the release file names, by operating system and by what Axis calls the CPU.
_WINDOWS_ARCH = {"x64": "x64", "arm64": "arm64", "x86": "x86"}
_LINUX_ARCH = {"x64": "x86_64", "arm64": "aarch64"}
# rustup's name for each computer (the folder under /rustup/dist on static.rust-lang.org).
RUSTUP_TRIPLES = {
    ("windows", "x64"): "x86_64-pc-windows-msvc", ("windows", "arm64"): "aarch64-pc-windows-msvc",
    ("windows", "x86"): "i686-pc-windows-msvc",
    ("linux", "x64"): "x86_64-unknown-linux-gnu", ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    ("macos", "x64"): "x86_64-apple-darwin", ("macos", "arm64"): "aarch64-apple-darwin",
}
RUSTUP_TIMEOUT = 45 * 60      # seconds rustup may take to fetch the toolchain
CPP_TOOLS_TIMEOUT = 45 * 60
CPP_TOOLS_URL = "https://aka.ms/vs/17/release/vs_BuildTools.exe"
CPP_WINGET_ID = "Microsoft.VisualStudio.2022.BuildTools"
# What the Visual Studio installer is asked to add: the C++ build tools and the Windows SDK they need.
CPP_TOOLS_ARGS = ["--quiet", "--wait", "--norestart", "--nocache",
                  "--add", "Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended"]
BAD_START = re.compile(r"GLIBC|error while loading shared libraries|cannot execute binary|bad CPU type", re.I)

_LOCK = threading.Lock()      # the record file
_INSTALL_LOCKS = {name: threading.Lock() for name in APP_IDS + [RUST]}
_UPDATES: Dict[str, Dict[str, Any]] = {}   # the last update check: app id (or "rust") -> {update, latest} or {error}


class ArtCraftError(Exception):
    """A problem installing or opening something that the user can act on."""

    user_facing = True


# ----------------------------------------------------------------- the record


def root() -> Path:
    return engines.root() / "artcraft"


def rust_home() -> Path:
    return engines.root() / RUST


def _record_path() -> Path:
    return engines.root() / "artcraft.json"


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data: Dict[str, Any]) -> None:
    path = _record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _remember(key: str, record: Dict[str, Any]) -> None:
    with _LOCK:
        data = _load()
        data[key] = record
        _save(data)


def _app_info(app_id: str) -> Optional[Dict[str, Any]]:
    """What Axis installed for an app, or None when it is not there (or its files are gone)."""
    info = _load().get(app_id)
    if not isinstance(info, dict) or not isinstance(info.get("program"), str):
        return None
    program = engines.root() / info["program"]
    if not program.exists() or not inside(program, root()):
        return None
    return {**info, "path": str(program)}


def _forget(key: str) -> None:
    with _LOCK:
        data = _load()
        data.pop(key, None)
        _save(data)
    _UPDATES.pop(key, None)


def _rust_info() -> Optional[Dict[str, Any]]:
    info = _load().get(RUST)
    if not isinstance(info, dict) or not isinstance(info.get("cargo"), str):
        return None
    cargo = engines.root() / info["cargo"]
    if not cargo.is_file() or not inside(cargo, rust_home()):
        return None
    return {**info, "path": str(cargo)}


def rust_env() -> Dict[str, str]:
    """The environment that makes the portable Rust the one a program finds. Empty when it is not installed."""
    if _rust_info() is None:
        return {}
    bin_dir = str(rust_home() / "cargo" / "bin")
    old = os.environ.get("PATH", "")
    return {"RUSTUP_HOME": str(rust_home() / "rustup"), "CARGO_HOME": str(rust_home() / "cargo"),
            "PATH": bin_dir + (os.pathsep + old if old else "")}


def cli_program(app_id: str) -> Optional[Path]:
    """The headless <app>-cli that came with an installed app, or None.

    The Windows portable zip and the Linux tarball hold it beside the app. The macOS disk image holds
    only the app, so there it is found only if the CLI zip was unpacked into the app's folder.
    """
    if app_id not in APP_IDS:
        return None
    info = _app_info(app_id)
    if info is None:
        return None
    name = app_id + "-cli" + (".exe" if engines.this_platform()[0] == "windows" else "")
    folder = root() / app_id
    try:
        hits = sorted((p for p in folder.rglob(name) if p.is_file() and inside(p, folder)), key=lambda p: len(p.parts))
    except OSError:
        return None
    return hits[0] if hits else None


def cli_dirs() -> List[str]:
    """The folders that hold an installed app's CLI, so a program started by an agent finds it by name."""
    found: List[str] = []
    for app_id in APP_IDS:
        program = cli_program(app_id)
        if program is not None and str(program.parent) not in found:
            found.append(str(program.parent))
    return found


_VC_CACHE: Dict[str, Dict[str, str]] = {}


def _vcvars_env() -> Dict[str, str]:
    """Return the MSVC build environment, or {} when C++ Build Tools are absent.

    Finding it starts vswhere and cmd.exe, which takes seconds, and every program an agent starts asks for it.
    A found environment is therefore kept for the rest of the run; "not found" is never kept, so installing the
    tools is noticed at once.
    """
    if os.name != "nt":
        return {}
    key = str(engines.root())
    if key not in _VC_CACHE:
        found = _find_vcvars_env()
        if not found:
            return {}
        _VC_CACHE[key] = found
    return dict(_VC_CACHE[key])


def _console_text(data: Any) -> str:
    """Decode what cmd.exe printed. It writes in the console's (OEM) code page, not UTF-8 or the ANSI page."""
    if isinstance(data, str):
        return data
    if os.name == "nt":
        try:
            import ctypes
            return data.decode("cp%d" % ctypes.windll.kernel32.GetOEMCP(), errors="replace")   # type: ignore[attr-defined]
        except (ImportError, AttributeError, OSError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def _parse_environment(text: str) -> Dict[str, str]:
    """The lines NAME=value printed by `set`.

    Names are upper-cased, as os.environ does on Windows: `set` prints the name the way it was first written
    (usually `Path`), and everything here looks for PATH. Lines of another form (a banner) are skipped.
    """
    env: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and "\x00" not in key and not key.startswith("=") and not any(c.isspace() for c in key):
            env[key.upper()] = value
    return env


def _find_vcvars_env() -> Dict[str, str]:
    base = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles") or ""
    candidates = [Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"] if base else []
    candidates += [Path(os.environ.get("ProgramFiles", "")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"]
    installs: List[Path] = []
    for vswhere in candidates:
        if not vswhere.is_file():
            continue
        try:
            r = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                installs.extend(Path(line.strip()) for line in r.stdout.splitlines() if line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    # The installer used by Axis has an explicit path. It may not be returned
    # by an older or incomplete vswhere installation, so check it directly.
    installs.append(engines.root() / "msvc")
    seen = set()
    for install in installs:
        install = install.resolve()
        if install in seen:
            continue
        seen.add(install)
        build = install / "VC" / "Auxiliary" / "Build"
        arch = engines.this_platform()[1]
        if arch == "arm64":
            scripts = [("vcvarsamd64_arm64.bat", ()), ("vcvarsall.bat", ("amd64_arm64",)),
                       ("vcvarsall.bat", ("arm64",))]
        elif arch == "x86":
            scripts = [("vcvars32.bat", ()), ("vcvarsall.bat", ("x86",))]
        else:
            # vcvarsall uses `amd64` for native x64. `x64` is not a valid
            # vcvarsall architecture argument; vcvars64.bat is its shortcut.
            scripts = [("vcvars64.bat", ()), ("vcvarsall.bat", ("amd64",)),
                       ("vcvarsall.bat", ("x86_amd64",))]
        for script, args in scripts:
            vcvars = build / script
            if not vcvars.is_file():
                continue
            try:
                # A batch file must be called so cmd.exe returns and executes
                # `set` afterward.
                command = f'call "{vcvars}"' + (" " + " ".join(args) if args else "") + " && set"
                # Hand cmd.exe one finished command line. A list would be quoted by Python with backslash
                # escapes (\"), which cmd.exe does not understand, so the script would never run. With /s the
                # outer quotes are removed and the inner ones are kept.
                comspec = os.environ.get("COMSPEC", "cmd.exe")
                r = subprocess.run(f'"{comspec}" /d /s /c "{command}"',
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode != 0:
                continue
            env = _parse_environment(_console_text(r.stdout))
            if env.get("PATH") and (env.get("VCTOOLSINSTALLDIR") or shutil.which("link.exe", path=env["PATH"])):
                return env
    if shutil.which("cl") and shutil.which("link"):
        return dict(os.environ)
    return {}


def rust_build_env() -> Dict[str, str]:
    """Environment for Rust builds, including MSVC variables when Windows has them."""
    env = rust_env()
    if os.name == "nt":
        vc = _vcvars_env()
        if vc:
            rust_path, vc_path = env.get("PATH", ""), vc.get("PATH", "")
            env.update(vc)
            if rust_path and vc_path:
                env["PATH"] = rust_path + os.pathsep + vc_path
    return env


def _run_elevated(exe: str, args: List[str], timeout: float) -> int:
    """Run a program as administrator on Windows (Windows asks the user to allow it) and wait for it.

    The Visual Studio installer must run as administrator, and Windows refuses to start such a program
    from an ordinary one (error 740). ShellExecuteEx with the "runas" verb is the way to ask for it.
    """
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    kernel32, shell32 = ctypes.windll.kernel32, ctypes.windll.shell32   # type: ignore[attr-defined]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(ShellExecuteInfo)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = subprocess.list2cmdline(args)
    info.nShow = 0   # hidden
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.GetLastError()
        if error == 1223:   # ERROR_CANCELLED: the user chose No
            raise ArtCraftError("Windows asked for permission to install the Microsoft C++ Build Tools and it was "
                                "not given. Click Yes when Windows asks, then try again.")
        raise ArtCraftError(f"Windows could not start the Microsoft C++ Build Tools installer (error {error}).")
    handle = info.hProcess
    try:
        wait_ms = int(min(timeout, 2 ** 31 / 1000) * 1000)
        if kernel32.WaitForSingleObject(handle, wait_ms) != 0:   # 0 is WAIT_OBJECT_0
            raise ArtCraftError("The Microsoft C++ Build Tools installer took too long. It may still be running in "
                                "the background; wait for it to finish, then try again.")
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(handle)


def _signed_by_microsoft(path: Path) -> Optional[bool]:
    """True/False when Windows can say whether the file carries a valid Microsoft signature; None when the
    check itself could not be run."""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
              "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O=Microsoft Corporation') "
              "{ 'signed' } else { 'unsigned' }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.decode("utf-8", errors="replace").strip()
    if r.returncode != 0 or out not in ("signed", "unsigned"):
        return None
    return out == "signed"


def _cpp_via_winget(job: Optional[jobs.Job]) -> None:
    """Ask winget (which shows the Windows permission prompt itself) to install the C++ Build Tools."""
    winget = shutil.which("winget")
    if not winget:
        return
    if job is not None:
        job.progress(phase="Installing the Microsoft C++ Build Tools with winget")
    cmd = [winget, "install", "--id", CPP_WINGET_ID, "--exact", "--silent", "--accept-package-agreements",
           "--accept-source-agreements", "--override", subprocess.list2cmdline(CPP_TOOLS_ARGS)]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=CPP_TOOLS_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass   # not fatal: the next way is tried, and the result is judged by looking for the linker


def _cpp_via_bootstrapper(job: Optional[jobs.Job]) -> None:
    """Download Microsoft's own installer and run it, asking Windows for administrator rights."""
    downloads = engines.root() / "_downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    # A fixed name could still be open from an earlier attempt (Windows Update, Defender): use a unique file.
    fd, name = tempfile.mkstemp(prefix="vs_BuildTools-", suffix=".exe", dir=str(downloads))
    os.close(fd)
    installer = Path(name)
    try:
        if job is not None:
            job.progress(phase="Downloading the Microsoft C++ Build Tools installer")
        req = urllib.request.Request(CPP_TOOLS_URL, headers={"User-Agent": f"axis/{__version__}"})
        with urllib.request.urlopen(req, timeout=60) as source, installer.open("wb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        if _signed_by_microsoft(installer) is False:
            raise ArtCraftError("The Microsoft C++ Build Tools installer that was downloaded is not signed by "
                                "Microsoft, so Axis did not run it.")
        if job is not None:
            job.progress(phase="Installing the Microsoft C++ Build Tools (Windows may ask for permission)")
        args = [str(installer)] + CPP_TOOLS_ARGS + ["--installPath", str(engines.root() / "msvc")]
        try:
            r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               timeout=CPP_TOOLS_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            code = r.returncode
        except OSError as exc:
            if getattr(exc, "winerror", None) != 740:   # 740: "the requested operation requires elevation"
                raise
            code = _run_elevated(str(installer), args[1:], CPP_TOOLS_TIMEOUT)
        if code not in (0, 3010):   # 3010: done, restart wanted
            raise ArtCraftError(f"The Microsoft C++ Build Tools installer stopped with exit code {code}.")
    except (urllib.error.URLError, OSError, subprocess.SubprocessError) as exc:
        raise ArtCraftError(f"Could not download or install Microsoft C++ Build Tools ({getattr(exc, 'reason', exc)}).") from exc
    finally:
        unlink_quiet(installer)


CPP_HELP = ("Install them yourself from https://visualstudio.microsoft.com/visual-cpp-build-tools/ (choose "
            "\"Desktop development with C++\"), then try again.")


def _install_cpp_tools(job: Optional[jobs.Job] = None) -> None:
    """Install the Windows C++ workload needed by Rust's MSVC target when it is absent.

    winget goes first: it asks Windows for permission itself and checks the installer. If that does not
    leave the tools behind (winget is missing, or the tools were already there without the C++ part), Microsoft's own
    installer is downloaded, checked for Microsoft's signature, and run as administrator.
    """
    if os.name != "nt" or _vcvars_env():
        return
    _cpp_via_winget(job)
    if _vcvars_env():
        return
    try:
        _cpp_via_bootstrapper(job)
    except ArtCraftError as exc:
        raise ArtCraftError(f"{exc}\n{CPP_HELP}") from exc
    if not _vcvars_env():
        raise ArtCraftError("Microsoft C++ Build Tools were installed, but Axis cannot find the Rust linker yet. "
                            "Restart Axis and try again. " + CPP_HELP)


def ensure_cpp_tools() -> None:
    """Install the Windows C++ workload when a Rust build needs it."""
    _install_cpp_tools()


# ----------------------------------------------------------------- choosing the download


def _app(app_id: str) -> Tuple[str, str, str]:
    for entry in APPS:
        if entry[0] == app_id:
            return entry
    raise ArtCraftError(f"'{app_id}' is not one of the Craft apps.")


def asset_pattern(app_id: str, plat: Tuple[str, str]) -> Optional[str]:
    """A regular expression for the release file to download on this computer, or None."""
    osname, arch = plat
    start = "^" + re.escape(app_id) + r"-\d[^-]*-"
    if osname == "windows" and arch in _WINDOWS_ARCH:
        return start + "windows-" + _WINDOWS_ARCH[arch] + r"-portable\.zip$"
    if osname == "linux" and arch in _LINUX_ARCH:
        return start + "linux-" + _LINUX_ARCH[arch] + r"\.tar\.gz$"
    if osname == "macos":
        return start + r"macos-universal\.dmg$"
    return None


def supported(plat: Optional[Tuple[str, str]] = None) -> bool:
    return asset_pattern(APP_IDS[0], plat or engines.this_platform()) is not None


def _latest_api(repo: str) -> Dict[str, Any]:
    data = json.loads(engines._read(f"{engines._github_api()}/repos/{repo}/releases/latest", "application/vnd.github+json"))
    assets = []
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            digest = a.get("digest") if isinstance(a.get("digest"), str) else ""
            assets.append({"name": a["name"], "url": a["browser_download_url"],
                           "size": a.get("size") if isinstance(a.get("size"), int) else None,
                           "sha256": digest[7:].lower() if digest.startswith("sha256:") else None})
    return {"tag": str(data.get("tag_name") or ""), "assets": assets}


def _latest_html(repo: str) -> Dict[str, Any]:
    """The same answer read from the release pages, for when the GitHub API refuses (rate limits)."""
    with engines._open(f"{engines._github()}/{repo}/releases/latest") as resp:
        final = urllib.parse.urlsplit(resp.geturl()).path
    m = re.search(r"/releases/tag/([^/]+)$", final)
    if not m:
        raise ArtCraftError(f"{repo} has no release yet.")
    tag = urllib.parse.unquote(m.group(1))
    html = engines._read(f"{engines._github()}/{repo}/releases/expanded_assets/{urllib.parse.quote(tag, safe='')}")
    digests = {name: sha for name, sha in re.findall(r'digest for ([^"]+)"[^>]*?value="sha256:([0-9a-f]{64})"', html)}
    assets, seen = [], set()
    for href in re.findall(r'href="(/' + re.escape(repo) + r'/releases/download/[^"]+)"', html):
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        if name not in seen:
            seen.add(name)
            assets.append({"name": name, "url": engines._github() + href, "size": None, "sha256": digests.get(name)})
    return {"tag": tag, "assets": assets}


def latest_release(repo: str) -> Dict[str, Any]:
    """The newest stable release of a repository: {"tag", "assets": [{name, url, size, sha256}]}."""
    try:
        return _latest_api(repo)
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 429):
            raise ArtCraftError(f"GitHub answered with an error ({exc.code} {exc.reason}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass
    try:
        return _latest_html(repo)
    except urllib.error.HTTPError as exc:
        raise ArtCraftError(f"GitHub answered with an error ({exc.code} {exc.reason}). Try again later.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ArtCraftError(f"Could not reach github.com ({getattr(exc, 'reason', exc)}). "
                            "Check your internet connection.") from exc


def _published_sha(rel: Dict[str, Any], asset: Dict[str, Any]) -> Optional[str]:
    """The checksum GitHub shows for a file, or the one in the release's SHA256SUMS.txt."""
    if asset.get("sha256"):
        return asset["sha256"]
    sums = next((a for a in rel["assets"] if a["name"] == "SHA256SUMS.txt"), None)
    if sums is None:
        return None
    try:
        text = engines._read(sums["url"])
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m and m.group(2) == asset["name"]:
            return m.group(1).lower()
    return None


# ----------------------------------------------------------------- finding the program


def _find_program(folder: Path, app_id: str, osname: str) -> Optional[Path]:
    """The program to start: <app>.exe, bin/<app>, or the program inside the .app."""
    if osname == "macos":
        for bundle in sorted(folder.glob("*.app")):
            try:
                with (bundle / "Contents" / "Info.plist").open("rb") as f:
                    name = plistlib.load(f).get("CFBundleExecutable")
            except (OSError, ValueError, plistlib.InvalidFileException):
                name = None
            candidates = [bundle / "Contents" / "MacOS" / name] if name else []
            candidates += sorted((bundle / "Contents" / "MacOS").glob("*"))
            for p in candidates:
                if p.is_file():
                    return p
        return None
    exe = app_id + (".exe" if osname == "windows" else "")
    hits = sorted((p for p in folder.rglob(exe) if p.is_file()), key=lambda p: len(p.parts))
    return hits[0] if hits else None


def _run_check(program: Path) -> str:
    """Start the program with --version to see that it loads. Returns the version line it prints, or "".

    A program that does not understand --version may open its window instead, so this waits only a
    few seconds, ends it, and accepts any answer except the ones that mean it cannot run here.
    """
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen([str(program), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=str(program.parent), **kwargs)
    except OSError as exc:
        raise ArtCraftError(f"{program.name} was downloaded but could not be started: {exc}") from exc
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return ""
    text = (out or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode in (-1073741515, 3221225781):   # 0xC0000135: a DLL is missing
        raise ArtCraftError(f"{program.name} needs the Microsoft Visual C++ runtime. Install it from "
                            "https://aka.ms/vs/17/release/vc_redist.x64.exe and try again.")
    if proc.returncode != 0 and BAD_START.search(text):
        raise ArtCraftError(f"{program.name} was downloaded but does not run on this computer.\n"
                            + "\n".join(text.splitlines()[-6:]))
    return next((l.strip()[:200] for l in text.splitlines() if "version" in l.lower() or re.search(r"\d+\.\d+", l)), "")


# ----------------------------------------------------------------- installing an app


def _apps_in(volume: Path) -> List[Path]:
    """The .app folders at the top of a mounted disk image (or one folder down). Links are skipped:
    the image's Applications shortcut leads to every app on this Mac."""
    found: List[Path] = []
    try:
        entries = sorted(p for p in volume.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return found
    found += [p for p in entries if p.suffix == ".app"]
    for folder in entries:
        if folder.suffix != ".app":
            try:
                found += sorted(p for p in folder.iterdir() if p.suffix == ".app" and p.is_dir() and not p.is_symlink())
            except OSError:
                pass
    return found


def _copy_app_from_dmg(dmg: Path, dest: Path, job: jobs.Job) -> None:
    """Copy the .app out of a disk image (macOS) without installing it anywhere else."""
    job.progress(phase=f"Opening {dmg.name}")
    mount = Path(tempfile.mkdtemp(prefix="axis-dmg-"))
    try:
        r = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-noautoopen",
                            "-mountpoint", str(mount), str(dmg)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=300)
        if r.returncode != 0:
            raise ArtCraftError("The disk image could not be opened:\n" + r.stdout.decode("utf-8", errors="replace")[-400:])
        try:
            apps = _apps_in(mount)
            if not apps:
                raise ArtCraftError(f"There is no app inside {dmg.name}.")
            job.progress(phase=f"Copying {apps[0].name}")
            c = subprocess.run(["ditto", str(apps[0]), str(dest / apps[0].name)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=600)
            if c.returncode != 0:
                raise ArtCraftError("The app could not be copied:\n" + c.stdout.decode("utf-8", errors="replace")[-400:])
        finally:
            subprocess.run(["hdiutil", "detach", "-force", str(mount)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=120)
    finally:
        try:
            mount.rmdir()
        except OSError:
            pass


def install_app(app_id: str, job: jobs.Job, update: bool = False) -> Dict[str, Any]:
    """Download the app's portable build and unpack it. Refuses when the app is already installed,
    unless update is set: then the newer version goes beside the old one, the old one's settings folders
    are copied across, and the old version is removed once the new one is recorded."""
    _, label, _ = _app(app_id)
    plat = engines.this_platform()
    pattern = asset_pattern(app_id, plat)
    if pattern is None:
        raise ArtCraftError(f"{label} has no ready-built program for {plat[0]} on {plat[1]}.")
    lock = _INSTALL_LOCKS[app_id]
    if not lock.acquire(blocking=False):
        raise ArtCraftError(f"{label} is already being installed.")
    try:
        old = _app_info(app_id)
        if update and old is None:
            raise ArtCraftError(f"{label} is not installed.")
        if old is not None and not update:
            raise ArtCraftError(f"{label} is already installed.")
        job.progress(phase=f"Looking up the latest {label} release")
        rel = latest_release(f"{REPO_OWNER}/{app_id}")
        if old is not None and rel["tag"] == old.get("tag"):
            raise ArtCraftError(f"{label} is already up to date ({rel['tag']}).")
        asset = next((a for a in sorted(rel["assets"], key=lambda a: a["name"]) if re.search(pattern, a["name"])), None)
        if asset is None:
            raise ArtCraftError(f"The latest {label} release ({rel['tag'] or 'unnamed'}) has no portable build "
                                f"for {plat[0]} on {plat[1]}.")
        asset["sha256"] = _published_sha(rel, asset)
        if not asset["sha256"]:
            raise ArtCraftError(f"The release does not publish a checksum for {asset['name']}, so Axis will not install it.")
        tag = re.sub(r"[^A-Za-z0-9._-]+", "-", rel["tag"]) or "release"
        base = root() / app_id
        final = base / tag
        work = base / (tag + ".partial")
        downloads = engines.root() / "_downloads"
        base.mkdir(parents=True, exist_ok=True)
        downloads.mkdir(parents=True, exist_ok=True)
        remove_tree(work)
        work.mkdir(parents=True)
        archive = downloads / asset["name"]
        try:
            engines._download(asset, archive, job, 0, asset.get("size"))
            if asset["name"].endswith(".dmg"):
                _copy_app_from_dmg(archive, work, job)
            else:
                job.progress(phase=f"Unpacking {asset['name']}")
                engines._unpack(archive, work)
            program = _find_program(work, app_id, plat[0])
            if program is None:
                raise ArtCraftError(f"The program is not in the {label} download.")
            if os.name != "nt":
                for p in {program, *program.parent.glob("*-cli")}:
                    if p.is_file():
                        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            job.progress(phase=f"Checking that {label} starts")
            version = _run_check(program)
            if old is not None:   # keep the settings folders the old version made beside its program
                old_dir, new_dir = Path(old["path"]).parent, program.parent
                for d in old_dir.iterdir() if old_dir.is_dir() else []:
                    if d.is_dir() and not d.is_symlink() and not (new_dir / d.name).exists():
                        shutil.copytree(d, new_dir / d.name, symlinks=True)
            if final.exists():
                # Never delete a folder Axis has no record of: it may hold the app's settings.
                final = base / f"{tag}-{int(time.time())}"
            replace_retry(work, final)
        except BaseException:
            remove_tree(work)
            raise
        finally:
            unlink_quiet(archive)
        record = {"tag": rel["tag"], "version": version, "dir": final.relative_to(engines.root()).as_posix(),
                  "program": (final / program.relative_to(work)).relative_to(engines.root()).as_posix(),
                  "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _remember(app_id, record)
        _UPDATES.pop(app_id, None)
        if old is not None:
            gone = engines.root() / old["dir"]
            if gone != final and inside(gone, root() / app_id):
                remove_tree(gone)   # a copy still in use (Windows) is left for the next delete
        return {"app": app_id, "model": label, "tag": rel["tag"]}
    finally:
        lock.release()


# ----------------------------------------------------------------- installing Rust


def _rustup_base() -> str:
    return (os.environ.get("AXIS_RUSTUP") or "https://static.rust-lang.org/rustup").rstrip("/")


def _pump(stream, lines: "queue.Queue[Optional[str]]") -> None:
    try:
        for raw in iter(stream.readline, b""):
            lines.put(raw.decode("utf-8", errors="replace").strip())
    finally:
        lines.put(None)


def install_rust(job: jobs.Job) -> Dict[str, Any]:
    """Install a stable Rust with rustup into ~/Axis/engines/rust. Refuses when it is already installed."""
    plat = engines.this_platform()
    triple = RUSTUP_TRIPLES.get(plat)
    if triple is None:
        raise ArtCraftError(f"rustup has no program for {plat[0]} on {plat[1]}.")
    lock = _INSTALL_LOCKS[RUST]
    if not lock.acquire(blocking=False):
        raise ArtCraftError("Rust is already being installed.")
    try:
        if _rust_info() is not None:
            raise ArtCraftError("Rust is already installed.")
        exe = "rustup-init" + (".exe" if plat[0] == "windows" else "")
        url = f"{_rustup_base()}/dist/{triple}/{exe}"
        downloads = engines.root() / "_downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        installer = downloads / exe
        job.progress(phase="Looking up the rustup checksum")
        try:
            sums = engines._read(url + ".sha256")
        except urllib.error.HTTPError as exc:
            raise ArtCraftError(f"rustup could not be found ({exc.code} {exc.reason}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ArtCraftError(f"Could not reach static.rust-lang.org ({getattr(exc, 'reason', exc)}). "
                                "Check your internet connection.") from exc
        m = re.match(r"\s*([0-9a-fA-F]{64})", sums)
        if not m:
            raise ArtCraftError("rustup's checksum could not be read.")
        try:
            engines._download({"name": exe, "url": url, "size": None, "sha256": m.group(1).lower()}, installer, job, 0, None)
        except BaseException:
            unlink_quiet(installer)
            raise
        home = rust_home()
        try:
            remove_tree(home)       # a half-finished earlier attempt
            (home / "rustup").mkdir(parents=True)
            (home / "cargo").mkdir()
            if os.name != "nt":
                installer.chmod(installer.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, "RUSTUP_HOME": str(home / "rustup"), "CARGO_HOME": str(home / "cargo")}
            env.pop("RUSTUP_TOOLCHAIN", None)
            kwargs: Dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            job.progress(phase="Installing Rust with rustup")
            proc = None
            for attempt in range(10):
                try:
                    proc = subprocess.Popen([str(installer), "-y", "--no-modify-path", "--profile", "minimal",
                                             "--default-toolchain", "stable"], stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                            cwd=str(home), **kwargs)
                    break
                except PermissionError as exc:   # an antivirus program is still scanning the new download
                    if attempt == 9:
                        raise ArtCraftError("Windows would not let Axis start rustup-init.exe (an antivirus "
                                            f"program may be blocking it): {exc.strerror or exc}") from exc
                    job.check()
                    time.sleep(1.0)
                except OSError as exc:
                    raise ArtCraftError(f"rustup-init could not be started: {exc.strerror or exc}") from exc
            assert proc is not None
            procs.track(proc)   # quitting Axis ends an installer that is still running
            lines: "queue.Queue[Optional[str]]" = queue.Queue()
            threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True).start()
            tail: List[str] = []
            deadline = time.time() + RUSTUP_TIMEOUT
            finished = False
            try:
                while not finished:
                    job.check()
                    if time.time() > deadline:
                        raise ArtCraftError("Installing Rust took too long and was stopped. Try again.")
                    try:
                        line = lines.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if line is None:
                        finished = True
                    elif line:
                        tail = (tail + [line])[-12:]
                        job.progress(phase=line[:160])
            finally:
                if proc.poll() is None:
                    procs.kill_tree(proc)
                proc.wait()
                procs.untrack(proc)
                if proc.stdout is not None:
                    proc.stdout.close()
            if proc.returncode != 0:
                raise ArtCraftError(f"rustup stopped with exit code {proc.returncode}.\n" + "\n".join(tail[-6:]))
            cargo = home / "cargo" / "bin" / ("cargo.exe" if plat[0] == "windows" else "cargo")
            if not cargo.is_file():
                raise ArtCraftError("rustup finished, but cargo is not where it should be.")
            job.progress(phase="Checking that Rust runs")
            r = subprocess.run([str(cargo), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, env=env, timeout=120, **kwargs)
            version = r.stdout.decode("utf-8", errors="replace").strip()
            if r.returncode != 0:
                raise ArtCraftError("Rust was installed but does not run on this computer.\n" + version[-300:])
        except BaseException:
            remove_tree(home)
            raise
        finally:
            unlink_quiet(installer)
        _remember(RUST, {"version": version[:200], "cargo": cargo.relative_to(engines.root()).as_posix(),
                         "installed": time.strftime("%Y-%m-%dT%H:%M:%S")})
        # Rust is installed and recorded. The Windows C++ Build Tools come last, and a problem with them
        # must not undo the Rust that was just downloaded (it used to: everything was deleted).
        job.check()
        try:
            _install_cpp_tools(job)
        except ArtCraftError as exc:
            raise ArtCraftError("Rust is installed, but the Microsoft C++ Build Tools it needs to build programs "
                                f"on Windows are not.\n{exc}") from exc
        return {"app": RUST, "model": "Rust", "version": version}
    finally:
        lock.release()


def update_rust(job: jobs.Job) -> Dict[str, Any]:
    """Bring the portable Rust up to the newest stable release with rustup."""
    info = _rust_info()
    if info is None:
        raise ArtCraftError("Rust is not installed.")
    home = rust_home()
    exe = "rustup" + (".exe" if os.name == "nt" else "")
    env = {**os.environ, **rust_env()}
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    job.progress(phase="Updating Rust with rustup")
    r = subprocess.run([str(home / "cargo" / "bin" / exe), "update", "stable", "--no-self-update"], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, timeout=RUSTUP_TIMEOUT, **kwargs)
    out = r.stdout.decode("utf-8", errors="replace").strip()
    if r.returncode != 0:
        raise ArtCraftError(f"rustup stopped with exit code {r.returncode}.\n" + "\n".join(out.splitlines()[-6:]))
    v = subprocess.run([info["path"], "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                       env=env, timeout=120, **kwargs)
    version = v.stdout.decode("utf-8", errors="replace").strip()[:200]
    _remember(RUST, {**{k: info[k] for k in ("cargo", "installed")}, "version": version})
    _UPDATES.pop(RUST, None)
    return {"app": RUST, "model": "Rust", "version": version}


def remove(body: Any) -> Dict[str, Any]:
    """Delete an installed app (every version, and any settings kept in its folder), or Rust."""
    if not isinstance(body, dict) or not isinstance(body.get("app"), str):
        raise ArtCraftError("Say which app to delete.")
    app_id = body["app"].strip().lower()
    if app_id != RUST:
        _app(app_id)
    label = "Rust" if app_id == RUST else _app(app_id)[1]
    if jobs.busy("artcraft", app_id):
        raise ArtCraftError(f"{label} is being installed or updated. Wait for it to finish, or cancel it.")
    folder = rust_home() if app_id == RUST else root() / app_id
    remove_tree(folder)
    if folder.exists():
        raise ArtCraftError(f"Some files of {label} are in use. Close it and try again.")
    _forget(app_id)
    return {"removed": app_id}


def _check_one(app_id: str) -> Dict[str, Any]:
    info = _app_info(app_id)
    try:
        tag = latest_release(f"{REPO_OWNER}/{app_id}")["tag"]
    except Exception as exc:   # one app failing to answer must not hide the others
        return {"error": str(exc)}
    return {"latest": tag, "update": bool(tag) and tag != (info or {}).get("tag")}


def _check_rust() -> Dict[str, Any]:
    info = _rust_info()
    exe = "rustup" + (".exe" if os.name == "nt" else "")
    try:
        r = subprocess.run([str(rust_home() / "cargo" / "bin" / exe), "check"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, env={**os.environ, **rust_env()}, timeout=90,
                           **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"Could not check Rust: {exc}"}
    out = r.stdout.decode("utf-8", errors="replace")
    if r.returncode != 0:
        return {"error": "Could not check Rust:\n" + "\n".join(out.strip().splitlines()[-3:])}
    return {"update": "update available" in out.lower(), "latest": "", "installed": (info or {}).get("version", "")}


def check_updates() -> Dict[str, Any]:
    """Ask GitHub (and rustup) whether newer versions exist, for what is installed. Remembered until the next check."""
    ids = [a for a in APP_IDS if _app_info(a) is not None]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(zip(ids, pool.map(_check_one, ids)))
        if _rust_info() is not None:
            results[RUST] = _check_rust()
    _UPDATES.clear()
    _UPDATES.update(results)
    return status()


# ----------------------------------------------------------------- what the page asks for


def submit(app_id: str, update: bool = False) -> jobs.Job:
    if app_id == RUST:
        if update:
            return jobs.submit("install", "artcraft", RUST, "Updating Rust", update_rust)
        return jobs.submit("install", "artcraft", RUST, "Installing Rust", install_rust)
    _, label, _ = _app(app_id)
    if not supported():
        osname, arch = engines.this_platform()
        raise ArtCraftError(f"{label} has no ready-built program for {osname} on {arch}.")
    verb = "Updating" if update else "Installing"
    return jobs.submit("install", "artcraft", app_id, f"{verb} {label}", lambda j: install_app(app_id, j, update))


def install(body: Any, update: bool = False) -> Dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("app"), str):
        raise ArtCraftError("Say which app to install.")
    app_id = body["app"].strip().lower()
    if app_id != RUST:
        _app(app_id)
    name = "Rust" if app_id == RUST else _app(app_id)[1]
    have = (_rust_info() if app_id == RUST else _app_info(app_id)) is not None
    if update and not have:
        raise ArtCraftError(f"{name} is not installed.")
    if have and not update:
        raise ArtCraftError(f"{name} is already installed.")
    return {"job": submit(app_id, update).public()}


def _launch(cmd: List[str], cwd: Path, env: Dict[str, str]) -> None:
    """Start a program detached, so it neither blocks Axis nor closes when Axis stops."""
    kw: Dict[str, Any] = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              close_fds=True, cwd=str(cwd), env={**os.environ, **env})
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    threading.Thread(target=proc.wait, daemon=True).start()   # collect its exit when it closes


def open_app(body: Any) -> Dict[str, Any]:
    """Start an installed app. It runs on its own: closing Axis does not close it."""
    if not isinstance(body, dict) or not isinstance(body.get("app"), str):
        raise ArtCraftError("Say which app to open.")
    app_id = body["app"].strip().lower()
    _, label, _ = _app(app_id)
    info = _app_info(app_id)
    if info is None:
        raise ArtCraftError(f"{label} is not installed. Install it first.")
    program = Path(info["path"])
    if jobs.busy("artcraft", app_id):
        raise ArtCraftError(f"{label} is still being installed.")
    try:
        _launch([str(program)], program.parent, rust_env())
    except OSError as exc:
        raise ArtCraftError(f"{label} could not be started: {exc}") from exc
    return {"opened": app_id}


def status() -> Dict[str, Any]:
    plat = engines.this_platform()
    apps = []
    for app_id, label, what in APPS:
        info = _app_info(app_id)
        apps.append({"id": app_id, "name": label, "what": what, "installed": bool(info),
                     "tag": info.get("tag") if info else None, "version": info.get("version") if info else None,
                     "path": info["path"] if info else None, "check": _UPDATES.get(app_id) if info else None})
    rust = _rust_info()
    return {
        "supported": supported(plat), "platform": {"os": plat[0], "arch": plat[1]},
        "apps": apps,
        "rust": {"installed": bool(rust), "supported": plat in RUSTUP_TRIPLES, "version": rust.get("version") if rust else None,
                 "path": str(rust_home()) if rust else None, "check": _UPDATES.get(RUST) if rust else None},
        "root": str(root()),
        "jobs": [j for j in jobs.listing() if j["engine"] == "artcraft"],
    }
