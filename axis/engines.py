"""Installs the programs Axis runs models with: llama.cpp, stable-diffusion.cpp and Ollama.

Both projects publish ready-built programs on GitHub for Windows, macOS and
Linux. Axis picks the build for this computer (CUDA for NVIDIA on Windows,
Vulkan for other graphics cards, Metal on Apple Silicon, otherwise the CPU
build), downloads it, checks it, unpacks it into ~/Axis/engines and confirms
that it runs. Nothing is installed system-wide and no administrator rights are
needed.

    ~/Axis/engines/engines.json          what is installed (version, build, program paths)
    ~/Axis/engines/llama/<release>-<build>/   llama-server and its libraries
    ~/Axis/engines/sd/<release>-<build>/      sd-cli, sd-server and their libraries
    ~/Axis/engines/ollama/<release>-<build>/  ollama and its libraries (installed only when you ask)

Every download is checked against the SHA-256 that GitHub publishes for it. A download with no
published checksum is refused (set AXIS_ALLOW_UNVERIFIED=1 to accept one), and the next older release
that has checksums is tried first. Ollama is never installed by itself on the first start.

pip and pipx cannot run code while a package is installed, so Axis installs the
engines itself: on its first start (in the background), from the Models page,
with `axis setup`, and whenever a model is installed or started that needs an
engine that is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import __version__, jobs
from .paths import axis_home, inside, replace_retry, unlink_quiet

CHUNK = 1024 * 1024
RELEASES_TO_SCAN = 10
DISK_MARGIN = 512 * 1024 * 1024

ENGINES = {
    "llama": {"label": "llama.cpp", "repo": "ggml-org/llama.cpp", "programs": ["llama-server"]},
    "sd": {"label": "stable-diffusion.cpp", "repo": "leejet/stable-diffusion.cpp", "programs": ["sd-cli", "sd-server"]},
    "ollama": {"label": "Ollama", "repo": "ollama/ollama", "programs": ["ollama"]},
}
BUILDS = ("auto", "cpu", "vulkan", "cuda", "rocm", "metal", "standard")
BUILD_LABELS = {"auto": "Automatic", "cpu": "CPU only", "vulkan": "Vulkan (any modern GPU)", "cuda": "CUDA (NVIDIA)",
                "rocm": "ROCm (AMD)", "metal": "Metal (Apple Silicon)",
                "standard": "Standard (uses the graphics card when it can)"}

_ARCHIVE = r"\.(?:zip|tar\.gz|tgz)$"

# Release file names, by engine, platform and build. Each entry is (pattern for the main
# archive, function that turns the main archive's name into patterns for extra archives).
PATTERNS: Dict[Tuple[str, str, str], Dict[str, Tuple[str, Optional[Callable[[str], List[str]]]]]] = {
    ("llama", "windows", "x64"): {
        "cuda": (r"^llama-[^-]+-bin-win-cuda-12\.[\d.]+-x64\.zip$",
                 lambda name: [r"^cudart-llama-bin-win-cuda-" + re.escape(_cuda_version(name)) + r"-x64\.zip$"]),
        "vulkan": (r"^llama-[^-]+-bin-win-vulkan-x64\.zip$", None),
        "rocm": (r"^llama-[^-]+-bin-win-(?:rocm|hip)[^/]*-x64\.zip$", None),
        "cpu": (r"^llama-[^-]+-bin-win-cpu-x64\.zip$", None),
    },
    ("llama", "windows", "arm64"): {
        "vulkan": (r"^llama-[^-]+-bin-win-vulkan-arm64\.zip$", None),
        "cpu": (r"^llama-[^-]+-bin-win-cpu-arm64\.zip$", None),
    },
    ("llama", "macos", "arm64"): {"metal": (r"^llama-[^-]+-bin-macos-arm64" + _ARCHIVE, None)},
    ("llama", "macos", "x64"): {"cpu": (r"^llama-[^-]+-bin-macos-x64" + _ARCHIVE, None)},
    ("llama", "linux", "x64"): {
        "vulkan": (r"^llama-[^-]+-bin-ubuntu-vulkan-x64" + _ARCHIVE, None),
        "rocm": (r"^llama-[^-]+-bin-ubuntu-rocm-[\d.]+-x64" + _ARCHIVE, None),
        "cpu": (r"^llama-[^-]+-bin-ubuntu-x64" + _ARCHIVE, None),
    },
    ("llama", "linux", "arm64"): {
        "vulkan": (r"^llama-[^-]+-bin-ubuntu-vulkan-arm64" + _ARCHIVE, None),
        "cpu": (r"^llama-[^-]+-bin-ubuntu-arm64" + _ARCHIVE, None),
    },
    ("ollama", "windows", "x64"): {"standard": (r"^ollama-windows-amd64\.zip$", None)},
    ("ollama", "windows", "arm64"): {"standard": (r"^ollama-windows-arm64\.zip$", None)},
    ("ollama", "macos", "arm64"): {"standard": (r"^ollama-darwin\.tgz$", None)},
    ("ollama", "macos", "x64"): {"standard": (r"^ollama-darwin\.tgz$", None)},
    ("ollama", "linux", "x64"): {"standard": (r"^ollama-linux-amd64\.(?:tar\.zst|tgz)$", None)},
    ("ollama", "linux", "arm64"): {"standard": (r"^ollama-linux-arm64\.(?:tar\.zst|tgz)$", None)},
    ("sd", "windows", "x64"): {
        "cuda": (r"^sd-.*-bin-win-cuda12-x64\.zip$", lambda name: [r"^cudart-sd-bin-win-cu12-x64\.zip$"]),
        "vulkan": (r"^sd-.*-bin-win-vulkan-x64\.zip$", None),
        "rocm": (r"^sd-.*-bin-win-rocm-.*-x64\.zip$", None),
        "cpu": (r"^sd-.*-bin-win-cpu-x64\.zip$", None),
    },
    ("sd", "macos", "arm64"): {"metal": (r"^sd-.*-bin-Darwin-.*-arm64\.zip$", None)},
    ("sd", "linux", "x64"): {
        "vulkan": (r"^sd-.*-bin-Linux-.*-x86_64-vulkan\.zip$", None),
        "rocm": (r"^sd-.*-bin-Linux-.*-x86_64-rocm-.*\.zip$", None),
        "cpu": (r"^sd-.*-bin-Linux-.*-x86_64\.zip$", None),
    },
}


class EngineError(Exception):
    """A problem installing an engine that the user can act on."""

    user_facing = True


def unverified_ok() -> bool:
    return os.environ.get("AXIS_ALLOW_UNVERIFIED", "").strip().lower() in ("1", "true", "yes", "on")


def _cuda_version(name: str) -> str:
    m = re.search(r"-cuda-(\d+\.\d+)", name)
    return m.group(1) if m else "12.4"


# ----------------------------------------------------------------- the record


def root() -> Path:
    return axis_home() / "engines"


def _record_path() -> Path:
    return root() / "engines.json"


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


_RECORD_LOCK = threading.Lock()


def installed(engine: str) -> Optional[Dict[str, Any]]:
    """What Axis installed for an engine, or None."""
    info = _load().get(engine)
    if not isinstance(info, dict) or not isinstance(info.get("dir"), str):
        return None
    folder = root() / info["dir"]
    if not folder.is_dir() or not inside(folder, root()):
        return None
    return {**info, "path": str(folder)}


def program(name: str) -> Optional[Path]:
    """A program Axis installed (llama-server, sd-cli or sd-server), or None."""
    for engine, spec in ENGINES.items():
        if name not in spec["programs"]:
            continue
        info = installed(engine)
        rel = (info or {}).get("programs", {}).get(name) if info else None
        if not rel:
            return None
        p = root() / rel
        if p.is_file() and inside(p, root()):
            return p.resolve()
    return None


def run_env(binary: Optional[Path]) -> Dict[str, str]:
    """Environment additions for running a program Axis installed (its libraries sit beside it)."""
    if binary is None or this_platform()[0] != "linux" or not inside(binary, root()):
        return {}
    old = os.environ.get("LD_LIBRARY_PATH", "")
    return {"LD_LIBRARY_PATH": str(binary.parent) + (os.pathsep + old if old else "")}


# ----------------------------------------------------------------- platform


def this_platform() -> Tuple[str, str]:
    system = platform.system().lower()
    osname = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(system, system)
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return osname, arch


def builds_for(engine: str, plat: Optional[Tuple[str, str]] = None) -> List[str]:
    osname, arch = plat or this_platform()
    return list(PATTERNS.get((engine, osname, arch), {}).keys())


def _quiet_run(cmd: List[str], timeout: float = 20.0) -> Tuple[int, str]:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           timeout=timeout, **kwargs)
        return r.returncode, r.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def graphics() -> List[str]:
    """Names of the graphics adapters (Windows), for choosing a build."""
    if os.name != "nt":
        return []
    code, out = _quiet_run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
    return [line.strip() for line in out.splitlines() if line.strip()] if code == 0 else []


def default_build(engine: str, plat: Optional[Tuple[str, str]] = None) -> str:
    """The build installed when none is chosen: CPU only, or "auto" where no CPU build exists
    (macOS on Apple Silicon ships Metal only)."""
    have = builds_for(engine, plat)
    return "cpu" if "cpu" in have else "standard" if "standard" in have else "auto"


def preferred_builds(engine: str, plat: Optional[Tuple[str, str]] = None,
                     gpus: Optional[List[str]] = None, has_vulkan: Optional[bool] = None) -> List[str]:
    """Builds to try, best first, for this computer."""
    osname, arch = plat or this_platform()
    available = builds_for(engine, (osname, arch))
    order: List[str] = []
    if engine == "ollama":
        order.append("standard")
    elif osname == "windows":
        names = graphics() if gpus is None else gpus
        nvidia = shutil.which("nvidia-smi") is not None or any("nvidia" in g.lower() for g in names)
        real = [g for g in names if not re.search(r"basic display|remote display|hyper-v|virtual|parsec|citrix", g, re.I)]
        if nvidia:
            order.append("cuda")
        if real or nvidia:
            order.append("vulkan")
        order.append("cpu")
    elif osname == "linux":
        if has_vulkan is None:
            has_vulkan = shutil.which("vulkaninfo") is not None and _quiet_run(["vulkaninfo", "--summary"])[0] == 0
        if has_vulkan:
            order.append("vulkan")
        order.append("cpu")
    else:
        order += ["metal", "cpu"]
    return [b for b in order if b in available]


# ----------------------------------------------------------------- releases


def _github() -> str:
    return (os.environ.get("AXIS_GITHUB") or "https://github.com").rstrip("/")


def _github_api() -> str:
    return (os.environ.get("AXIS_GITHUB_API") or "https://api.github.com").rstrip("/")


def _open(url: str, timeout: float = 30.0, accept: str = "*/*"):
    headers = {"User-Agent": f"axis/{__version__}", "Accept": accept}
    req = urllib.request.Request(url, headers=headers)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token and urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(_github_api()).netloc:
        # "Unredirected": sent to this address only, never on to wherever a redirect points.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=timeout)


def _read(url: str, accept: str = "*/*") -> str:
    with _open(url, accept=accept) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _releases_api(repo: str) -> List[Dict[str, Any]]:
    data = json.loads(_read(f"{_github_api()}/repos/{repo}/releases?per_page={RELEASES_TO_SCAN}",
                            "application/vnd.github+json"))
    out = []
    for rel in data if isinstance(data, list) else []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        assets = []
        for a in rel.get("assets") or []:
            if not isinstance(a, dict) or not a.get("name") or not a.get("browser_download_url"):
                continue
            digest = a.get("digest") if isinstance(a.get("digest"), str) else ""
            assets.append({"name": a["name"], "url": a["browser_download_url"],
                           "size": a.get("size") if isinstance(a.get("size"), int) else None,
                           "sha256": digest[7:].lower() if digest.startswith("sha256:") else None})
        out.append({"tag": str(rel.get("tag_name") or ""), "assets": assets})
    return out


def _releases_html(repo: str) -> Iterator[Dict[str, Any]]:
    """The same list read from the release pages, for when the GitHub API refuses (rate limits)."""
    page = _read(f"{_github()}/{repo}/releases")
    tags: List[str] = []
    for tag in re.findall(r"/" + re.escape(repo) + r"/releases/(?:expanded_assets|tag)/([^\"?#/]+)", page):
        tag = urllib.parse.unquote(tag)
        if tag not in tags:
            tags.append(tag)
    for tag in tags[:RELEASES_TO_SCAN]:
        html = _read(f"{_github()}/{repo}/releases/expanded_assets/{urllib.parse.quote(tag, safe='')}")
        digests = {name: sha for name, sha in
                   re.findall(r'digest for ([^"]+)"[^>]*?value="sha256:([0-9a-f]{64})"', html)}
        seen, assets = set(), []
        for href in re.findall(r'href="(/' + re.escape(repo) + r'/releases/download/[^"]+)"', html):
            name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
            if name in seen:
                continue
            seen.add(name)
            assets.append({"name": name, "url": _github() + href, "size": None, "sha256": digests.get(name)})
        yield {"tag": tag, "assets": assets}


def releases(repo: str) -> Iterator[Dict[str, Any]]:
    try:
        yield from _releases_api(repo)
        return
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 429):
            raise EngineError(f"GitHub answered with an error ({exc.code} {exc.reason}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass
    try:
        yield from _releases_html(repo)
    except urllib.error.HTTPError as exc:
        raise EngineError(f"GitHub answered with an error ({exc.code} {exc.reason}). Try again later.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineError(f"Could not reach github.com ({getattr(exc, 'reason', exc)}). "
                          "Check your internet connection.") from exc


class _Cached:
    """Reads releases only as far as needed, and remembers them for the next build tried."""

    def __init__(self, source: Iterator[Dict[str, Any]]):
        self._source, self._seen = source, []

    def __iter__(self):
        yield from self._seen
        for rel in self._source:
            if not rel["assets"]:
                continue
            self._seen.append(rel)
            yield rel
            if len(self._seen) >= RELEASES_TO_SCAN:
                return


def choose(engine: str, build: str, rels,
           plat: Optional[Tuple[str, str]] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The newest release with a build for this platform: (release, [main archive, extra archives...])."""
    osname, arch = plat or this_platform()
    table = PATTERNS.get((engine, osname, arch))
    if not table:
        raise EngineError(f"{ENGINES[engine]['label']} has no ready-built program for {osname} on {arch}. "
                          "Build it from source and set its path on the Models page.")
    if build not in table:
        raise EngineError(f"There is no '{build}' build of {ENGINES[engine]['label']} for {osname} on {arch}. "
                          f"Choose one of: {', '.join(table)}.")
    main_re, extra = table[build]
    unsigned = False
    for rel in rels:
        names = {a["name"]: a for a in rel["assets"]}
        main = next((a for n, a in sorted(names.items()) if re.search(main_re, n)), None)
        if main is None:
            continue
        picked = [main]
        ok = True
        for pat in (extra(main["name"]) if extra else []):
            hit = next((a for n, a in sorted(names.items()) if re.search(pat, n)), None)
            if hit is None:
                ok = False
                break
            picked.append(hit)
        if ok and not unverified_ok() and any(not a.get("sha256") for a in picked):
            unsigned = True   # GitHub published no checksum for it: try an older release that has one
            continue
        if ok:
            return rel, picked
    if unsigned:
        raise EngineError(f"The latest {ENGINES[engine]['label']} releases have no published checksum for the "
                          f"'{build}' build, so Axis will not install it. Try again later, or set "
                          "AXIS_ALLOW_UNVERIFIED=1 to install it without the check.")
    raise EngineError(f"None of the latest {ENGINES[engine]['label']} releases has a '{build}' build for "
                      f"{osname} on {arch}.")


# ----------------------------------------------------------------- download and unpack


def _download(asset: Dict[str, Any], dest: Path, job: jobs.Job, base: int, total: Optional[int]) -> int:
    job.progress(phase=f"Downloading {asset['name']}")
    h = hashlib.sha256()
    got = 0
    for attempt in range(4):
        try:
            with _open(asset["url"], timeout=60) as resp, dest.open("wb") as out:
                length = resp.headers.get("Content-Length")
                size = int(length) if length and length.isdigit() else asset.get("size")
                if size and shutil.disk_usage(dest.parent).free - size < DISK_MARGIN:
                    raise EngineError(f"Not enough disk space to download {asset['name']}.")
                h, got, last = hashlib.sha256(), 0, 0.0
                while True:
                    job.check()
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if time.time() - last > 0.25:
                        job.progress(done=base + got, total=total)
                        last = time.time()
                if size and got != size:
                    raise ConnectionError("the download ended early")
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404):
                raise EngineError(f"GitHub refused the download of {asset['name']} ({exc.code}).") from exc
            if attempt == 3:
                raise EngineError(f"The download of {asset['name']} kept failing "
                                  f"({getattr(exc, 'reason', exc)}). Try again.") from exc
            job.progress(phase=f"Connection problem; retrying ({attempt + 1} of 3)")
            for _ in range(10 * (attempt + 1)):
                job.check()
                time.sleep(0.2)
    if asset.get("sha256"):
        if h.hexdigest() != asset["sha256"]:
            dest.unlink(missing_ok=True)
            raise EngineError(f"{asset['name']} failed its checksum and was deleted. Try again.")
    elif not unverified_ok():
        dest.unlink(missing_ok=True)
        raise EngineError(f"{asset['name']} has no published checksum, so Axis will not use it.")
    job.progress(done=base + got, total=total)
    return got


def _within(top: Path, path: Any) -> bool:
    """True when path is top or lies inside it. (A plain startswith would accept the sibling top + "-evil".)"""
    try:
        return os.path.commonpath([str(top), os.path.normpath(path)]) == str(top)
    except ValueError:  # different drives
        return False


def _unpack(archive: Path, dest: Path) -> None:
    """Unpack an archive into dest, refusing anything that would land outside it."""
    dest.mkdir(parents=True, exist_ok=True)
    top = dest.resolve()
    name = archive.name.lower()

    def need_space(size: int) -> None:
        free = shutil.disk_usage(dest).free
        if size + DISK_MARGIN > free:
            raise EngineError(f"Not enough disk space to unpack {archive.name}: it needs "
                              f"{size // (1024 * 1024)} MB and {free // (1024 * 1024)} MB is free.")

    if name.endswith(".zip"):
        try:
            with zipfile.ZipFile(archive) as z:
                need_space(sum(i.file_size for i in z.infolist()))
                for info in z.infolist():
                    parts = PurePosixPath(info.filename.replace("\\", "/")).parts
                    if not parts or info.filename.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                        raise EngineError(f"{archive.name} contains an unsafe path: {info.filename}")
                    target = top.joinpath(*parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out, CHUNK)
                    mode = (info.external_attr >> 16) & 0o777
                    if mode and os.name != "nt":
                        target.chmod(mode)
        except zipfile.BadZipFile as exc:
            raise EngineError(f"The download of {archive.name} is damaged. Try again.") from exc
        return
    if name.endswith(".tar.zst"):
        return _unpack_zst(archive, top, need_space)
    try:
        with tarfile.open(archive, "r:*") as t:
            members = t.getmembers()
            need_space(sum(m.size for m in members if m.isfile()))
            for m in members:
                parts = PurePosixPath(m.name).parts
                if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                    raise EngineError(f"{archive.name} contains an unsafe entry: {m.name}")
                if m.issym() or m.islnk():
                    link = (top / PurePosixPath(m.name).parent / m.linkname) if m.issym() else top / m.linkname
                    if not _within(top, link):
                        raise EngineError(f"{archive.name} contains a link that points outside it: {m.name}")
            if hasattr(tarfile, "data_filter"):
                t.extractall(top, members=members, filter="data")
            else:
                t.extractall(top, members=members)
    except tarfile.TarError as exc:
        raise EngineError(f"The download of {archive.name} is damaged. Try again.") from exc


def _zstd_program() -> Optional[str]:
    return shutil.which("zstd") or shutil.which("unzstd")


def _unpack_zst(archive: Path, top: Path, need_space: Callable[[int], None]) -> None:
    """Unpack a .tar.zst (Ollama on Linux) by streaming it through the system's zstd program.

    Python's standard library cannot read Zstandard before 3.14. Every entry is checked before it is
    written, as for the other archives, and nothing is ever stored as a whole archive on disk twice.
    """
    program = _zstd_program()
    need_space(max(archive.stat().st_size * 3, 0))
    if program is None:
        raise EngineError("This download is compressed with zstd, which this computer does not have. Install it "
                          "(for example: sudo apt install zstd) and try again, or install Ollama from "
                          "ollama.com/download.")
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([program, "-dc", str(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, **kwargs)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as t:
            for m in t:
                parts = PurePosixPath(m.name).parts
                if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                    raise EngineError(f"{archive.name} contains an unsafe entry: {m.name}")
                if m.issym() or m.islnk():
                    link = (top / PurePosixPath(m.name).parent / m.linkname) if m.issym() else top / m.linkname
                    if not _within(top, link):
                        raise EngineError(f"{archive.name} contains a link that points outside it: {m.name}")
                if hasattr(tarfile, "data_filter"):
                    t.extract(m, top, filter="data")
                else:
                    t.extract(m, top)
    except tarfile.TarError as exc:
        raise EngineError(f"The download of {archive.name} is damaged. Try again.") from exc
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()


def _find(folder: Path, name: str) -> Optional[Path]:
    exe = name + (".exe" if os.name == "nt" else "")
    hits = sorted((p for p in folder.rglob(exe) if p.is_file()), key=lambda p: len(p.parts))
    return hits[0] if hits else None


def _check_runs(binary: Path) -> str:
    env = {**os.environ, **run_env(binary)}
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([str(binary), "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=60, env=env, cwd=str(binary.parent), **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineError(f"{binary.name} was downloaded but could not be started: {exc}") from exc
    out = r.stdout.decode("utf-8", errors="replace").strip()
    if r.returncode in (-1073741515, 3221225781):   # 0xC0000135: a DLL is missing
        raise EngineError(f"{binary.name} needs the Microsoft Visual C++ runtime. Install it from "
                          "https://aka.ms/vs/17/release/vc_redist.x64.exe and try again.")
    if r.returncode != 0:
        tail = "\n".join(out.splitlines()[-6:])
        raise EngineError(f"{binary.name} was downloaded but does not run on this computer "
                          f"(exit code {r.returncode}).\n{tail}")
    lines = [l for l in out.splitlines() if l.strip()]
    for line in lines:
        if "version" in line.lower():
            return line.strip()[:200]
    return (lines[0].strip() if lines else "")[:200]


# ----------------------------------------------------------------- installing

_LOCKS = {name: threading.Lock() for name in ENGINES}


def _install_build(engine: str, build: str, rels, job: jobs.Job) -> Dict[str, Any]:
    spec = ENGINES[engine]
    rel, assets = choose(engine, build, rels)
    tag = re.sub(r"[^A-Za-z0-9._-]+", "-", rel["tag"]) or "release"
    folder_name = f"{tag}-{build}"
    base = root() / engine
    base.mkdir(parents=True, exist_ok=True)
    work = base / (folder_name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    downloads = root() / "_downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    sizes = [a.get("size") for a in assets]
    total = sum(sizes) if all(isinstance(s, int) for s in sizes) else None
    try:
        done = 0
        for a in assets:
            archive = downloads / a["name"]
            try:
                done += _download(a, archive, job, done, total)
                job.progress(phase=f"Unpacking {a['name']}")
                _unpack(archive, work)
            finally:
                unlink_quiet(archive)
        found: Dict[str, Path] = {}
        for prog in spec["programs"]:
            p = _find(work, prog)
            if p is not None:
                found[prog] = p
        if spec["programs"][0] not in found and not (engine == "sd" and found):
            raise EngineError(f"{spec['programs'][0]} is not in the {spec['label']} download.")
        main_dir = next(iter(found.values())).parent
        if engine == "llama" or build == "cuda":
            # Runtime libraries from the extra archives must sit next to the programs.
            for lib in work.rglob("*"):
                if lib.is_file() and lib.suffix.lower() in (".dll", ".so", ".dylib") and lib.parent != main_dir:
                    if not (main_dir / lib.name).exists():
                        shutil.copy2(lib, main_dir / lib.name)
        if os.name != "nt":
            for p in found.values():
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        job.progress(phase=f"Checking that {spec['label']} runs")
        version = _check_runs(found.get(spec["programs"][0]) or next(iter(found.values())))
        final = base / folder_name
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        if final.exists():
            final = base / f"{folder_name}-{int(time.time())}"
        replace_retry(work, final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    programs = {prog: (final / p.relative_to(work)).relative_to(root()).as_posix() for prog, p in found.items()}
    record = {"tag": rel["tag"], "build": build, "dir": final.relative_to(root()).as_posix(), "programs": programs,
              "version": version, "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with _RECORD_LOCK:
        data = _load()
        data[engine] = record
        _save(data)
    # Remove older versions; one still in use (a running model on Windows) is left for next time.
    for old in base.iterdir():
        if old != final and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    return record


def install(engine: str, build: str, job: jobs.Job,
            unless: Optional[Callable[[], Optional[Path]]] = None) -> Dict[str, Any]:
    """Download and install an engine. build "auto" tries the best build first and falls back.

    unless: skip the install when this finds the program after waiting for another install.
    """
    if engine not in ENGINES:
        raise EngineError("Choose llama.cpp, stable-diffusion.cpp or Ollama.")
    if build not in BUILDS:
        raise EngineError(f"Unknown build '{build}'. Use one of: {', '.join(BUILDS)}.")
    label = ENGINES[engine]["label"]
    while not _LOCKS[engine].acquire(timeout=0.5):
        job.check()
        job.progress(phase=f"Waiting for another install of {label}")
    try:
        if unless is not None and unless() is not None:
            return {"engine": engine, "model": label}
        job.progress(phase=f"Looking up the latest {label} release")
        rels = _Cached(releases(ENGINES[engine]["repo"]))
        candidates = preferred_builds(engine) if build == "auto" else [build]
        if not candidates:
            osname, arch = this_platform()
            raise EngineError(f"{label} has no ready-built program for {osname} on {arch}. Build it from source "
                              "and set its path on the Models page.")
        errors = []
        for b in candidates:
            try:
                record = _install_build(engine, b, rels, job)
                return {"engine": engine, "model": label, **record,
                        "note": "; ".join(errors) if errors else ""}
            except EngineError as exc:
                if build != "auto":
                    raise
                errors.append(f"{b}: {exc}")
                job.progress(phase=f"The {b} build did not work here; trying the next one")
        raise EngineError(f"No build of {label} works on this computer.\n" + "\n".join(errors))
    finally:
        _LOCKS[engine].release()


def ensure(engine: str, found: Callable[[], Optional[Path]], job: jobs.Job) -> None:
    """Install an engine inside another job when no copy of it can be found."""
    if found() is not None:
        return
    label = ENGINES[engine]["label"]
    if not builds_for(engine):
        osname, arch = this_platform()
        raise EngineError(f"{label} is not installed, and there is no ready-built program for {osname} on {arch}. "
                          "Build it from source and set its path on the Models page.")
    job.progress(phase=f"Installing {label} first")
    install(engine, default_build(engine), job, unless=found)
    job.done = job.total = job.rate = None   # the model's own download reports from zero
    job._rate_mark = None


def submit(engine: str, build: Optional[str] = None) -> jobs.Job:
    if engine not in ENGINES:
        raise EngineError("Choose llama.cpp, stable-diffusion.cpp or Ollama.")
    build = build or default_build(engine)
    label = ENGINES[engine]["label"]
    verb = "Updating" if installed(engine) else "Installing"
    return jobs.submit("install", "engine", engine, f"{verb} {label}", lambda j: install(engine, build, j))


def remove(engine: str) -> Dict[str, Any]:
    if engine not in ENGINES:
        raise EngineError("Choose llama.cpp, stable-diffusion.cpp or Ollama.")
    with _RECORD_LOCK:
        data = _load()
        data.pop(engine, None)
        _save(data)
    folder = root() / engine
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        raise EngineError(f"Some files of {ENGINES[engine]['label']} are in use. Stop every model that uses it "
                          "and try again.")
    return {"removed": engine}


# ----------------------------------------------------------------- first start


def auto_disabled() -> bool:
    return os.environ.get("AXIS_NO_ENGINE_INSTALL", "").strip().lower() in ("1", "true", "yes", "on")


def auto_install(found: Dict[str, Callable[[], Optional[Path]]]) -> List[jobs.Job]:
    """On the first start, install every engine that cannot be found, in the background."""
    if auto_disabled():
        return []
    with _RECORD_LOCK:
        data = _load()
        if data.get("auto_installed"):
            return []
        data["auto_installed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save(data)
    started = []
    for engine, finder in found.items():
        if finder() is None and builds_for(engine) and installed(engine) is None:
            try:
                started.append(submit(engine))
            except jobs.JobError:
                pass
    return started


def status(found: Dict[str, Callable[[], Optional[Path]]]) -> Dict[str, Any]:
    out = {}
    plat = this_platform()
    for engine, spec in ENGINES.items():
        info = installed(engine)
        binary = found[engine]() if engine in found else None
        out[engine] = {
            "label": spec["label"],
            "binary": str(binary) if binary else None,
            "axis": bool(info and binary and inside(binary, root())),
            "installed": {k: info.get(k) for k in ("tag", "build", "version", "installed")} if info else None,
            "builds": builds_for(engine, plat),
            "build_labels": {b: BUILD_LABELS[b] for b in builds_for(engine, plat)},
            "default_build": default_build(engine, plat),
        }
    return out
