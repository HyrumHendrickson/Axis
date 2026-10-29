"""Reads the text in pictures (OCR) with Tesseract, or with PyTorch OCR models (EasyOCR and docTR).

Engines
  * Tesseract is a program. Axis uses a copy it finds (the path on the Models page,
    AXIS_TESSERACT, the PATH, or the usual install folders) and can install it: on Windows
    with the installer published on Tesseract's GitHub releases (checked against its
    SHA-256), on macOS with Homebrew, and on Linux with the system's package manager
    (pkexec asks for the password).
  * PyTorch OCR models run in a Python environment of their own, ~/Axis/engines/ocr-python,
    made with venv. Axis installs PyTorch into it with pip (the CPU-only build by default, or
    the CUDA build), then EasyOCR or docTR when a model needs it. Axis itself stays
    standard-library only; ocr_worker.py is the part that runs inside that environment.

Models
  * Tesseract languages (eng, lat, grc, ...): traineddata files from GitHub
    (tesseract-ocr/tessdata_fast, or tessdata_best with ":best") in ~/Axis/models/ocr/tessdata.
    Languages that came with Tesseract are listed too.
  * EasyOCR: a set of languages ("en", "en,fr"); weights in ~/Axis/models/ocr/easyocr/<name>.
  * docTR: a text detector and a text recognizer ("db_resnet50+crnn_vgg16_bn"); weights in
    ~/Axis/models/ocr/doctr/<name>.
  A PyTorch model can be started (kept in memory by ocr_worker.py serving on a local port)
  or used without starting it, which loads it for each picture.
"""

from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from . import engines, jobs, procs
from .paths import axis_home, inside, ocr_root

WORKER = Path(__file__).resolve().parent / "ocr_worker.py"
OCR_PORT = 7901
START_TIMEOUT = float(os.environ.get("AXIS_START_TIMEOUT", "900"))
RUN_TIMEOUT = float(os.environ.get("AXIS_OCR_TIMEOUT", "900"))
MAX_IMAGE = 64 * 1024 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
MARK = "@@AXIS@@"
CHUNK = 256 * 1024

_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OcrError(Exception):
    """A problem with OCR that the user can act on."""

    user_facing = True


# ------------------------------------------------------------------ names

TESSDATA = {"fast": "tesseract-ocr/tessdata_fast", "best": "tesseract-ocr/tessdata_best"}
TESS_LANG_RE = re.compile(r"^(?:script/)?[A-Za-z][A-Za-z0-9_]{1,39}$")
TESS_ALIASES = {
    "english": "eng", "latin": "lat", "greek": "ell", "ancient-greek": "grc", "ancientgreek": "grc",
    "german": "deu", "french": "fra", "spanish": "spa", "italian": "ita", "portuguese": "por", "dutch": "nld",
    "russian": "rus", "hebrew": "heb", "arabic": "ara", "chinese": "chi_sim", "japanese": "jpn", "korean": "kor",
    "hindi": "hin",
}
EASY_LANG_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z]+)?$")
EASY_ALIASES = {
    "english": "en", "latin": "la", "french": "fr", "german": "de", "spanish": "es", "italian": "it",
    "portuguese": "pt", "dutch": "nl", "russian": "ru", "arabic": "ar", "chinese": "ch_sim", "japanese": "ja",
    "korean": "ko", "hindi": "hi",
}
# Architectures docTR 1.x offers (doctr.models.detection.zoo.ARCHS and recognition.zoo.ARCHS).
DOCTR_DET = ("fast_base", "fast_small", "fast_tiny", "db_resnet50", "db_resnet34", "db_mobilenet_v3_large",
             "linknet_resnet18", "linknet_resnet34", "linknet_resnet50")
DOCTR_RECO = ("crnn_vgg16_bn", "crnn_mobilenet_v3_small", "crnn_mobilenet_v3_large", "sar_resnet31", "master",
              "vitstr_small", "vitstr_base", "parseq", "viptr_tiny")
DOCTR_DEFAULT = ("fast_base", "crnn_vgg16_bn")
DOCTR_PRESETS = {"doctr": DOCTR_DEFAULT, "default": DOCTR_DEFAULT,
                 "small": ("db_mobilenet_v3_large", "crnn_mobilenet_v3_small"),
                 "accurate": ("db_resnet50", "parseq")}
PACKAGES = {"easyocr": "easyocr", "doctr": "python-doctr"}
ENGINE_LABELS = {"tesseract": "Tesseract", "easyocr": "EasyOCR", "doctr": "docTR"}

CATALOG = {
    "tesseract": [
        {"name": "eng", "title": "English", "size": "4 MB"},
        {"name": "lat", "title": "Latin", "size": "3 MB"},
        {"name": "grc", "title": "Ancient Greek", "size": "2 MB"},
        {"name": "ell", "title": "Greek", "size": "1 MB"},
        {"name": "heb", "title": "Hebrew", "size": "1 MB"},
        {"name": "deu", "title": "German", "size": "2 MB"},
        {"name": "fra", "title": "French", "size": "1 MB"},
        {"name": "spa", "title": "Spanish", "size": "2 MB"},
        {"name": "eng:best", "title": "English, the larger and more accurate model", "size": "15 MB"},
    ],
    "easyocr": [
        {"name": "en", "title": "English", "size": "about 100 MB"},
        {"name": "en,fr,de", "title": "English, French and German", "size": "about 100 MB"},
        {"name": "en,la", "title": "English and Latin", "size": "about 100 MB"},
        {"name": "ch_sim,en", "title": "Simplified Chinese and English", "size": "about 100 MB"},
        {"name": "ja,en", "title": "Japanese and English", "size": "about 100 MB"},
    ],
    "doctr": [
        {"name": "fast_base+crnn_vgg16_bn", "title": "docTR's default pair", "size": "about 80 MB"},
        {"name": "db_resnet50+parseq", "title": "Slower, often more accurate", "size": "about 120 MB"},
        {"name": "db_mobilenet_v3_large+crnn_mobilenet_v3_small", "title": "Small and quick", "size": "about 20 MB"},
    ],
}

PSM = {3: "Find the layout automatically", 4: "One column of text", 6: "One block of text", 7: "One line of text",
       11: "Scattered text (labels, signs)"}


def tesseract_langs(text: str) -> Tuple[List[str], str]:
    """Languages and variant ("fast" or "best") from "eng", "eng:best", "eng+lat", "english, latin"."""
    t = (text or "").strip()
    variant = "fast"
    m = re.search(r":\s*(fast|best)\s*$", t, re.IGNORECASE)
    if m:
        variant, t = m.group(1).lower(), t[: m.start()]
    out: List[str] = []
    for word in re.split(r"[+,\s]+", t):
        if not word:
            continue
        word = TESS_ALIASES.get(word.lower(), word)
        if word.lower().endswith(".traineddata"):
            word = word[: -len(".traineddata")]
        if not TESS_LANG_RE.fullmatch(word):
            raise OcrError(f"'{word}' is not a Tesseract language. Languages are short codes like eng, lat or grc.")
        if word not in out:
            out.append(word)
    if not out:
        raise OcrError("Type a Tesseract language, like eng (English) or lat (Latin).")
    if len(out) > 12:
        raise OcrError("Install at most 12 languages at a time.")
    return out, variant


def easyocr_langs(text: str) -> List[str]:
    out: List[str] = []
    for word in re.split(r"[+,\s]+", (text or "").strip()):
        if not word:
            continue
        word = EASY_ALIASES.get(word.lower(), word.lower())
        if not EASY_LANG_RE.fullmatch(word):
            raise OcrError(f"'{word}' is not an EasyOCR language. Languages are codes like en, fr or ch_sim.")
        if word not in out:
            out.append(word)
    if not out:
        raise OcrError("Type one or more EasyOCR languages, like en or en,fr.")
    return out


def doctr_pair(text: str) -> Tuple[str, str]:
    t = (text or "").strip().lower()
    if t in DOCTR_PRESETS or t.startswith("doctr") and t[5:].strip(":-_ ") in DOCTR_PRESETS:
        return DOCTR_PRESETS.get(t) or DOCTR_PRESETS[t[5:].strip(":-_ ")]
    det, reco = "", ""
    for word in re.split(r"[+,\s]+", t):
        if not word:
            continue
        if word in DOCTR_DET:
            det = word
        elif word in DOCTR_RECO:
            reco = word
        else:
            raise OcrError(f"'{word}' is not a docTR model. Detectors: {', '.join(DOCTR_DET)}. "
                           f"Recognizers: {', '.join(DOCTR_RECO)}.")
    if not det and not reco:
        return DOCTR_DEFAULT
    return det or DOCTR_DEFAULT[0], reco or DOCTR_DEFAULT[1]


def model_id(engine: str, model: str) -> str:
    return f"{engine}:{model}"


def split_id(mid: str) -> Tuple[str, str]:
    engine, _, model = (mid or "").partition(":")
    if engine not in ENGINE_LABELS or not model:
        raise OcrError("Choose an OCR model.")
    return engine, model


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


def tessdata_dir() -> Path:
    return ocr_root() / "tessdata"


def _langs_record() -> Path:
    return tessdata_dir() / "axis-languages.json"


def _models_record() -> Path:
    return ocr_root() / "ocr-models.json"


def env_dir() -> Path:
    return engines.root() / "ocr-python"


def _env_record() -> Path:
    return env_dir() / "axis-env.json"


def _engines_record() -> Path:
    return engines.root() / "ocr-engines.json"


def _settings_path() -> Path:
    return axis_home() / "ocr-settings.json"


_RECORD_LOCK = threading.RLock()


def load_settings() -> Dict[str, Any]:
    data = _read_json(_settings_path(), {})
    device = data.get("device") if data.get("device") in ("auto", "cpu") else "auto"
    return {"tesseract": str(data.get("tesseract") or ""), "python": str(data.get("python") or ""),
            "device": device, "torch_index": str(data.get("torch_index") or "")}


def save_settings(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise OcrError("Settings must be a JSON object.")
    current = load_settings()
    for key, label in (("tesseract", "Tesseract path"), ("python", "Python"), ("torch_index", "PyTorch package index")):
        if key in body:
            value = body[key] if body[key] is not None else ""
            if not isinstance(value, str):
                raise OcrError(f"{label} must be text.")
            value = value.strip().strip('"').strip()
            if len(value) > 1000:
                raise OcrError(f"{label} is too long.")
            if key == "torch_index" and value and not re.match(r"^https?://", value):
                raise OcrError("The PyTorch package index must be a web address, like https://download.pytorch.org/whl/cpu.")
            current[key] = value
    if "device" in body:
        if body["device"] not in ("auto", "cpu"):
            raise OcrError("Device must be auto or cpu.")
        current["device"] = body["device"]
    _write_json(_settings_path(), current)
    _PROBE.clear()
    return status()


# ------------------------------------------------------------------ running programs


def _popen(cmd: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None,
           stdin: Any = subprocess.DEVNULL) -> subprocess.Popen:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env={**os.environ, **(env or {})}, cwd=str(cwd) if cwd else None, **kwargs)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


class _Lines:
    """Collects a program's output lines in a background thread (\\r counts as a line end,
    so progress bars arrive as they change)."""

    def __init__(self, proc: subprocess.Popen, on_line: Optional[Callable[[str], None]] = None, keep: int = 400):
        self.lines: Deque[str] = collections.deque(maxlen=keep)
        self.result: Optional[Dict[str, Any]] = None
        self._proc, self._on_line = proc, on_line
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        out = self._proc.stdout
        if out is None:
            return
        buf = b""
        try:
            while True:
                chunk = out.read1(4096) if hasattr(out, "read1") else out.read(4096)
                if not chunk:
                    break
                buf += chunk
                *done, buf = buf.replace(b"\r", b"\n").split(b"\n")
                for raw in done:
                    self._line(raw)
        except (OSError, ValueError):
            pass
        if buf.strip():
            self._line(buf)

    def _line(self, raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if not line:
            return
        if line.startswith(MARK):
            try:
                data = json.loads(line[len(MARK):])
                self.result = data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                pass
            return
        self.lines.append(line[:500])
        if self._on_line:
            try:
                self._on_line(line)
            except Exception:
                pass

    def join(self) -> None:
        self._thread.join(timeout=10)

    def tail(self, n: int = 12) -> str:
        return "\n".join(list(self.lines)[-n:])


def _run(cmd: List[str], job: Optional[jobs.Job], on_line: Optional[Callable[[str], None]] = None,
         env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None,
         timeout: Optional[float] = None, beat: Optional[Callable[[], None]] = None) -> Tuple[int, _Lines]:
    """Run a program to the end, stopping it when the job is cancelled (or beat raises)."""
    try:
        proc = _popen(cmd, env=env, cwd=cwd)
    except OSError as exc:
        raise OcrError(f"Could not start {Path(cmd[0]).name}: {exc.strerror or exc}") from exc
    lines = _Lines(proc, on_line)
    deadline = time.time() + timeout if timeout else None
    last_beat = time.time()
    try:
        while proc.poll() is None:
            if job is not None:
                job.check()
            if beat is not None and time.time() - last_beat > 2:
                beat()
                last_beat = time.time()
            if deadline and time.time() > deadline:
                raise OcrError(f"{Path(cmd[0]).name} took longer than {int(timeout or 0)} seconds, so it was stopped.")
            time.sleep(0.2)
    except BaseException:
        _kill(proc)
        lines.join()
        _close(proc)
        raise
    lines.join()
    _close(proc)
    return proc.returncode, lines


def _close(proc: subprocess.Popen) -> None:
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except (OSError, ValueError):
        pass


def _percent(job: jobs.Job, line: str) -> None:
    """Progress bars from pip, EasyOCR and docTR downloads: "45.3%"."""
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s?%", line)
    if m:
        pct = float(m.group(1))
        if 0 <= pct <= 100:
            job.progress(done=int(pct * 10), total=1000)


# ------------------------------------------------------------------ Tesseract


def _registry_dirs() -> List[Path]:
    if os.name != "nt":
        return []
    out: List[Path] = []
    try:
        import winreg  # type: ignore[import-not-found]

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (0, getattr(winreg, "KEY_WOW64_64KEY", 0)):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Tesseract-OCR", 0, winreg.KEY_READ | view) as key:
                        for name in ("Path", "InstallDir"):
                            try:
                                value, _ = winreg.QueryValueEx(key, name)
                                if value:
                                    out.append(Path(str(value)))
                            except OSError:
                                pass
                except OSError:
                    pass
    except ImportError:
        pass
    return out


def _usual_tesseract() -> List[Path]:
    if os.name == "nt":
        dirs = _registry_dirs()
        for var, sub in (("ProgramFiles", "Tesseract-OCR"), ("ProgramFiles(x86)", "Tesseract-OCR"),
                         ("LOCALAPPDATA", r"Programs\Tesseract-OCR"), ("LOCALAPPDATA", "Tesseract-OCR")):
            base = os.environ.get(var)
            if base:
                dirs.append(Path(base) / sub)
        return [d / "tesseract.exe" for d in dirs]
    return [Path(p) for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/opt/local/bin/tesseract",
                              "/usr/bin/tesseract")]


def tesseract_binary() -> Optional[Path]:
    """Tesseract from the settings, AXIS_TESSERACT, the PATH, or a usual install location."""
    for value in (load_settings()["tesseract"], os.environ.get("AXIS_TESSERACT", "")):
        if value:
            p = Path(value).expanduser()
            if p.is_dir():
                for cand in (p / "tesseract", p / "tesseract.exe"):
                    if cand.is_file():
                        return cand.resolve()
            if p.is_file():
                return p.resolve()
            found = shutil.which(value)
            return Path(found).resolve() if found else None
    found = shutil.which("tesseract")
    if found:
        return Path(found).resolve()
    for p in _usual_tesseract():
        try:
            if p.is_file():
                return p.resolve()
        except OSError:
            pass
    return None


_PROBE: Dict[str, Any] = {}
_PROBE_TTL = 60.0


def tesseract_info(binary: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Version and built-in languages of a Tesseract (remembered for a minute)."""
    binary = binary or tesseract_binary()
    if binary is None:
        return None
    try:
        stamp = binary.stat().st_mtime
    except OSError:
        return None
    key = f"{binary}|{stamp}"
    hit = _PROBE.get(key)
    if hit and time.time() - hit["at"] < _PROBE_TTL:
        return hit["info"]
    code, out = engines._quiet_run([str(binary), "--version"])
    version = ""
    for line in out.splitlines():
        if line.strip().lower().startswith("tesseract"):
            version = line.strip()[len("tesseract"):].strip().lstrip("v ")
            break
    info: Dict[str, Any] = {"version": version or None, "langs": [], "dir": None, "works": code == 0}
    code, out = engines._quiet_run([str(binary), "--list-langs"])
    if code == 0:
        for line in out.splitlines():
            line = line.strip()
            m = re.search(r'"(.+?)"', line)
            if m and "languages" in line.lower():
                info["dir"] = m.group(1)
            elif TESS_LANG_RE.fullmatch(line):
                info["langs"].append(line)
    _PROBE.clear()
    _PROBE[key] = {"at": time.time(), "info": info}
    return info


def axis_langs() -> Dict[str, Dict[str, Any]]:
    """Languages Axis downloaded: {lang: {variant, size, installed}}."""
    folder = tessdata_dir()
    if not folder.is_dir():
        return {}
    record = _read_json(_langs_record(), {})
    out: Dict[str, Dict[str, Any]] = {}
    for f in sorted(folder.rglob("*.traineddata")):
        rel = f.relative_to(folder).as_posix()[: -len(".traineddata")]
        if not TESS_LANG_RE.fullmatch(rel):
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        info = record.get(rel) if isinstance(record.get(rel), dict) else {}
        out[rel] = {"variant": info.get("variant") or "", "size": size, "installed": info.get("installed")}
    return out


def _tessdata_listing(repo: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """{path: {size, sha}} for a tessdata repository, or None when GitHub's API does not answer."""
    url = f"{engines._github_api()}/repos/{repo}/git/trees/main?recursive=1"
    try:
        with engines._open(url, timeout=30, accept="application/vnd.github+json") as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    tree = data.get("tree") if isinstance(data, dict) else None
    if not isinstance(tree, list):
        return None
    return {e["path"]: {"size": e.get("size"), "sha": e.get("sha")} for e in tree
            if isinstance(e, dict) and e.get("type") == "blob" and isinstance(e.get("path"), str)}


def _raw_base() -> str:
    return (os.environ.get("AXIS_GITHUB_RAW") or "https://raw.githubusercontent.com").rstrip("/")


def _fetch(url: str, dest: Path, job: jobs.Job, base: int, total: Optional[int], label: str,
           size: Optional[int] = None, git_sha: Optional[str] = None) -> int:
    """Download url to dest (through dest.part), checking the size and git blob hash when known."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(4):
        got = 0
        try:
            with engines._open(url, timeout=60) as resp, part.open("wb") as out:
                length = resp.headers.get("Content-Length")
                expect = size if size is not None else (int(length) if length and length.isdigit() else None)
                h = hashlib.sha1(f"blob {expect}\0".encode()) if git_sha and expect is not None else None
                last = 0.0
                while True:
                    job.check()
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    if h is not None:
                        h.update(chunk)
                    got += len(chunk)
                    if time.time() - last > 0.25:
                        job.progress(done=base + got, total=total)
                        last = time.time()
            if expect is not None and got != expect:
                raise ConnectionError("the download ended early")
            if size is not None and got != size:
                raise OcrError(f"{label} is not the size GitHub lists. Try again.")
            if h is not None and h.hexdigest() != git_sha:
                raise OcrError(f"{label} failed its checksum and was deleted. Try again.")
            os.replace(part, dest)
            job.progress(done=base + got, total=total)
            return got
        except jobs.Cancelled:
            part.unlink(missing_ok=True)
            raise
        except OcrError:
            part.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as exc:
            part.unlink(missing_ok=True)
            if exc.code == 404:
                raise OcrError(f"{label} was not found ({url}).") from exc
            if attempt == 3:
                raise OcrError(f"GitHub refused the download of {label} ({exc.code} {exc.reason}).") from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            part.unlink(missing_ok=True)
            if attempt == 3:
                raise OcrError(f"The download of {label} kept failing ({getattr(exc, 'reason', exc)}). Try again.") from exc
        job.progress(phase=f"Connection problem; retrying ({attempt + 1} of 3)")
        for _ in range(10 * (attempt + 1)):
            job.check()
            time.sleep(0.2)
    raise OcrError(f"The download of {label} failed.")


def install_languages(langs: List[str], variant: str, job: jobs.Job) -> Dict[str, Any]:
    repo = TESSDATA[variant]
    job.progress(phase="Looking up the languages on GitHub")
    listing = _tessdata_listing(repo)
    files = []
    for lang in langs:
        path = f"{lang}.traineddata"
        info = (listing or {}).get(path)
        if listing is not None and info is None:
            raise OcrError(f"Tesseract has no language named '{lang}'. The list is at github.com/{repo}.")
        files.append((lang, path, info or {}))
    sizes = [f[2].get("size") for f in files]
    total = sum(sizes) if all(isinstance(s, int) for s in sizes) else None
    folder = tessdata_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if total and shutil.disk_usage(folder).free - total < engines.DISK_MARGIN:
        raise OcrError("Not enough disk space for these languages.")
    done = 0
    for lang, path, info in files:
        job.progress(phase=f"Downloading {path}")
        url = f"{_raw_base()}/{repo}/main/{urllib.parse.quote(path)}"
        try:
            done += _fetch(url, folder / path, job, done, total, path, info.get("size"), info.get("sha"))
        except OcrError as exc:
            if "was not found" in str(exc):
                raise OcrError(f"Tesseract has no language named '{lang}'. The list is at github.com/{repo}.") from exc
            raise
        with _RECORD_LOCK:
            record = _read_json(_langs_record(), {})
            record[lang] = {"variant": variant, "installed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "verified": bool(info.get("sha"))}
            _write_json(_langs_record(), record)
    label = "+".join(langs) + (" (best)" if variant == "best" else "")
    return {"model": f"Tesseract {label}", "id": model_id("tesseract", langs[0])}


def delete_language(lang: str) -> Dict[str, Any]:
    if lang not in axis_langs():
        if lang in ((tesseract_info() or {}).get("langs") or []):
            raise OcrError(f"{lang} came with Tesseract, so Axis cannot delete it. Remove it with Tesseract's own "
                           "installer or package manager.")
        raise OcrError(f"The Tesseract language '{lang}' does not exist.")
    target = tessdata_dir() / f"{lang}.traineddata"
    if not inside(target, tessdata_dir()):
        raise OcrError("That language file is outside the OCR models folder.")
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OcrError(f"Could not delete {target.name}: {exc.strerror or exc}") from exc
    with _RECORD_LOCK:
        record = _read_json(_langs_record(), {})
        record.pop(lang, None)
        _write_json(_langs_record(), record)
    return {"model": f"Tesseract {lang}"}


# --- installing Tesseract itself


def _linux_install_command() -> Optional[List[str]]:
    for prog, cmd in (("apt-get", ["apt-get", "install", "-y", "tesseract-ocr"]),
                      ("dnf", ["dnf", "install", "-y", "tesseract"]),
                      ("pacman", ["pacman", "-S", "--noconfirm", "--needed", "tesseract", "tesseract-data-eng"]),
                      ("zypper", ["zypper", "--non-interactive", "install", "tesseract-ocr"]),
                      ("apk", ["apk", "add", "tesseract-ocr"])):
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


def tesseract_install_plan() -> Dict[str, Any]:
    """How Axis would install Tesseract here: {how, note, command?}."""
    osname, _ = engines.this_platform()
    if osname == "windows":
        return {"how": "installer", "note": "Axis downloads Tesseract's Windows installer from GitHub and runs it. "
                                            "Windows may ask for permission."}
    if osname == "macos":
        brew = _brew()
        if brew:
            return {"how": "brew", "note": "Axis installs Tesseract with Homebrew (brew install tesseract)."}
        return {"how": None, "note": "Install Homebrew from brew.sh, then click Install Tesseract. Or install it "
                                     "yourself and set its path under Engine settings."}
    cmd = _linux_install_command()
    if cmd is None:
        return {"how": None, "note": "Install Tesseract with your system's package manager, then click Refresh."}
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    gui = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if root:
        return {"how": "package", "command": " ".join(cmd), "note": f"Axis runs {' '.join(cmd)}."}
    if shutil.which("pkexec") and gui:
        return {"how": "package", "command": "sudo " + " ".join(cmd),
                "note": f"Axis runs {' '.join(cmd)}; your system asks for your password."}
    return {"how": None, "command": "sudo " + " ".join(cmd),
            "note": f"Run this in a terminal, then click Refresh: sudo {' '.join(cmd)}"}


def _install_tesseract_windows(job: jobs.Job) -> Dict[str, Any]:
    job.progress(phase="Looking up the latest Tesseract release")
    pattern = re.compile(r"^tesseract-ocr-w64-setup-.*\.exe$", re.IGNORECASE)
    for rel in engines.releases("tesseract-ocr/tesseract"):
        asset = next((a for a in rel["assets"] if pattern.match(a["name"])), None)
        if asset is not None:
            break
    else:
        raise OcrError("None of the latest Tesseract releases on GitHub has a Windows installer. Install Tesseract "
                       "yourself and set its path under Engine settings.")
    downloads = engines.root() / "_downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    exe = downloads / asset["name"]
    try:
        engines._download(asset, exe, job, 0, asset.get("size"))
        job.progress(phase="Running the Tesseract installer (Windows may ask for permission)")
        job.cancellable = False
        quoted = str(exe).replace("'", "''")
        code, out = engines._quiet_run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                        f"$p = Start-Process -FilePath '{quoted}' -ArgumentList '/S' -Wait -PassThru; "
                                        "exit $p.ExitCode"], timeout=900)
    finally:
        try:
            exe.unlink(missing_ok=True)
        except OSError:
            pass
    if code != 0:
        raise OcrError("The Tesseract installer did not finish"
                       + (" (permission was not given)" if "cancel" in out.lower() else f" (code {code})")
                       + ". Try again, or install Tesseract yourself and set its path under Engine settings."
                       + (f"\n{out.strip()[-600:]}" if out.strip() else ""))
    return {"tag": rel["tag"], "method": "installer"}


def install_tesseract(job: jobs.Job) -> Dict[str, Any]:
    plan = tesseract_install_plan()
    if plan["how"] is None:
        raise OcrError(plan["note"])
    if plan["how"] == "installer":
        info = _install_tesseract_windows(job)
    else:
        if plan["how"] == "brew":
            cmd = [_brew() or "brew", "install", "tesseract"]
            env = {"HOMEBREW_NO_ENV_HINTS": "1"}
        else:
            cmd = _linux_install_command() or []
            if not (hasattr(os, "geteuid") and os.geteuid() == 0):
                cmd = ["pkexec"] + cmd
            env = {"DEBIAN_FRONTEND": "noninteractive"}
        job.progress(phase="Running " + " ".join(cmd))
        code, lines = _run(cmd, job, on_line=lambda line: job.progress(phase=line[:160]), env=env, timeout=1800)
        if code != 0:
            hint = ""
            if code in (126, 127) and cmd[0] == "pkexec":
                hint = "The password was not given. "
            elif "unable to locate package" in lines.tail(30).lower():
                hint = "Your package list is out of date: run sudo apt-get update in a terminal first. "
            raise OcrError(f"{hint}Installing Tesseract failed (code {code}).\n{lines.tail()}")
        info = {"tag": "", "method": plan["how"]}
    _PROBE.clear()
    binary = tesseract_binary()
    if binary is None:
        raise OcrError("Tesseract was installed but Axis cannot find it. Set its path under Engine settings.")
    probe = tesseract_info(binary) or {}
    with _RECORD_LOCK:
        data = _read_json(_engines_record(), {})
        data["tesseract"] = {**info, "binary": str(binary), "version": probe.get("version"),
                             "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _write_json(_engines_record(), data)
    return {"engine": "tesseract", "model": "Tesseract " + (probe.get("version") or "")}


# ------------------------------------------------------------------ the PyTorch environment


TORCH_BUILDS = ("auto", "cpu", "cuda")
TORCH_LABELS = {"auto": "Best for this computer", "cpu": "CPU only (smaller download)", "cuda": "CUDA (NVIDIA GPU)",
                "default": "Standard (Apple Silicon GPU)"}
CPU_INDEX = "https://download.pytorch.org/whl/cpu"
CUDA_INDEX_WINDOWS = "https://download.pytorch.org/whl/cu132"   # published for PyTorch 2.12 through 2.15


def env_python() -> Optional[Path]:
    folder = env_dir()
    for cand in (folder / "Scripts" / "python.exe", folder / "bin" / "python3", folder / "bin" / "python"):
        if cand.is_file():
            return cand
    return None


def base_python() -> str:
    return load_settings()["python"] or os.environ.get("AXIS_OCR_PYTHON", "") or sys.executable


def torch_builds() -> List[str]:
    osname, arch = engines.this_platform()
    if osname == "macos":
        return ["default"]
    if osname == "windows" and arch == "arm64":
        return ["cpu"]
    if osname == "linux" and arch == "arm64":
        return ["cpu", "cuda"]
    return ["cpu", "cuda"]


def _has_nvidia() -> bool:
    if shutil.which("nvidia-smi"):
        return True
    return any("nvidia" in g.lower() for g in engines.graphics())


def resolve_build(build: Optional[str]) -> str:
    builds = torch_builds()
    build = build or builds[0]
    if build == "auto":
        return "cuda" if "cuda" in builds and _has_nvidia() else builds[0]
    if build not in builds:
        raise OcrError(f"There is no '{build}' build of PyTorch here. Choose one of: {', '.join(builds)}.")
    return build


def torch_index_args(build: str) -> List[str]:
    custom = load_settings()["torch_index"]
    if custom:
        return ["--index-url", custom]
    osname, _ = engines.this_platform()
    if build == "cpu" and osname != "macos":
        return ["--index-url", CPU_INDEX]
    if build == "cuda" and osname == "windows":
        return ["--index-url", CUDA_INDEX_WINDOWS]
    return []   # PyPI: CUDA builds on Linux, the standard build on macOS


def env_info() -> Optional[Dict[str, Any]]:
    py = env_python()
    if py is None:
        return None
    record = _read_json(_env_record(), {})
    if not record.get("torch"):
        return None
    return {**record, "python": str(py)}


def _pip(py: Path, args: List[str], job: jobs.Job, what: str) -> None:
    """pip install in the OCR environment. "--progress-bar raw" (pip 24.1 and newer) prints
    "Progress <done> of <total>" for each download, which drives the progress bar."""
    progress = re.compile(r"^Progress (\d+) of (\d+)$")

    def on_line(line: str) -> None:
        text = line.strip()
        m = progress.match(text)
        if m:
            job.progress(done=int(m.group(1)), total=int(m.group(2)) or None)
        elif text.lower().startswith(("collecting ", "downloading ", "installing collected", "building wheel")):
            job.progress(phase=text[:160])

    job.progress(phase=f"Installing {what}")
    for bar in ("raw", "off"):
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--progress-bar", bar] + args
        code, lines = _run(cmd, job, on_line=on_line, timeout=7200, env={"PIP_NO_INPUT": "1", "PYTHONUTF8": "1"})
        if code != 0 and bar == "raw" and "invalid choice" in lines.tail(6).lower():
            continue   # an older pip
        break
    job.done = job.total = job.rate = None
    job._rate_mark = None
    if code != 0:
        tail = lines.tail(14)
        low = tail.lower()
        hint = ""
        if "no matching distribution" in low or "could not find a version" in low:
            hint = (f"pip found no {what} for this Python ({_py_version(py)}). PyTorch supports recent 64-bit Python "
                    "versions; set another Python under Engine settings and install the PyTorch environment again. ")
        elif "no space left" in low:
            hint = "The disk is full. "
        raise OcrError(f"{hint}Installing {what} failed.\n{tail}")


def _py_version(py: Path) -> str:
    code, out = engines._quiet_run([str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    return out.strip() if code == 0 else "unknown version"


def _check_env(py: Path) -> Dict[str, Any]:
    code, lines = _run([str(py), "-u", str(WORKER), "check"], None, timeout=300)
    if code != 0 or lines.result is None:
        raise OcrError("The OCR Python environment does not work.\n" + lines.tail())
    return lines.result


_ENV_LOCK = threading.RLock()


@contextlib.contextmanager
def _env_locked(job: jobs.Job):
    """One pip at a time in the OCR Python environment."""
    while not _ENV_LOCK.acquire(timeout=0.5):
        job.check()
        job.progress(phase="Waiting for another install in the OCR Python environment")
    try:
        yield
    finally:
        _ENV_LOCK.release()


def install_env(build: str, job: jobs.Job) -> Dict[str, Any]:
    """Make ~/Axis/engines/ocr-python and install PyTorch into it."""
    with _env_locked(job):
        return _install_env(build, job)


def _install_env(build: str, job: jobs.Job) -> Dict[str, Any]:
    build = resolve_build(build)
    folder = env_dir()
    folder.parent.mkdir(parents=True, exist_ok=True)
    need = 7 if build == "cuda" else 2
    if shutil.disk_usage(folder.parent).free < need * 1024 ** 3:
        raise OcrError(f"PyTorch needs about {need} GB of free disk space for this build.")
    py = env_python()
    if py is None:
        job.progress(phase="Making a Python environment for OCR")
        base = base_python()
        code, lines = _run([base, "-m", "venv", str(folder)], job, timeout=600)
        py = env_python()
        if code != 0 or py is None:
            shutil.rmtree(folder, ignore_errors=True)
            tail = lines.tail()
            hint = ""
            if "ensurepip" in tail or "venv" in tail.lower():
                hint = ("This Python cannot make environments. On Debian and Ubuntu install python3-venv "
                        "(sudo apt install python3-venv), or set another Python under Engine settings. ")
            raise OcrError(f"{hint}Making the OCR Python environment failed.\n{tail}")
    try:
        _pip(py, ["--upgrade", "pip"], job, "pip")
    except OcrError:
        pass  # the pip that came with venv usually works too
    label = TORCH_LABELS.get(build, build).split(" (")[0]
    _pip(py, ["--upgrade", "torch", "torchvision"] + torch_index_args(build), job, f"PyTorch ({label})")
    job.progress(phase="Checking that PyTorch works")
    info = _check_env(py)
    with _RECORD_LOCK:
        record = _read_json(_env_record(), {})
        record.update({"build": build, "torch": info.get("torch"), "cuda": info.get("cuda"), "mps": info.get("mps"),
                       "python_version": info.get("python"), "installed": time.strftime("%Y-%m-%dT%H:%M:%S")})
        record.setdefault("packages", {})
        _write_json(_env_record(), record)
    return {"engine": "pytorch", "model": f"PyTorch {info.get('torch') or ''}".strip()}


def ensure_package(engine: str, job: jobs.Job) -> Path:
    """The environment's Python, with PyTorch and the package for engine installed (installing them first)."""
    with _env_locked(job):
        return _ensure_package(engine, job)


def _ensure_package(engine: str, job: jobs.Job) -> Path:
    if env_info() is None:
        job.progress(phase="Installing PyTorch first")
        install_env(torch_builds()[0], job)   # like llama.cpp: the CPU build first; the GPU build is a choice
        job.done = job.total = job.rate = None
        job._rate_mark = None
    py = env_python()
    assert py is not None
    record = _read_json(_env_record(), {})
    packages = record.get("packages") if isinstance(record.get("packages"), dict) else {}
    if not packages.get(engine):
        _pip(py, [PACKAGES[engine]], job, ENGINE_LABELS[engine])
        info = _check_env(py)
        version = info.get(engine)
        if not version:
            raise OcrError(f"{ENGINE_LABELS[engine]} was installed but cannot be loaded.\n{info.get(engine + '_error') or ''}")
        with _RECORD_LOCK:
            record = _read_json(_env_record(), {})
            record.setdefault("packages", {})[engine] = version
            _write_json(_env_record(), record)
        job.done = job.total = job.rate = None
        job._rate_mark = None
    return py


def remove_env() -> Dict[str, Any]:
    if procs.running("ocr"):
        raise OcrError("Stop every OCR model that is on first.")
    shutil.rmtree(env_dir(), ignore_errors=True)
    if env_dir().exists():
        raise OcrError("Some files of the OCR Python environment are in use. Stop every OCR model and try again.")
    return {"removed": "pytorch"}


# ------------------------------------------------------------------ PyTorch models


def _records() -> List[Dict[str, Any]]:
    data = _read_json(_models_record(), [])
    return [r for r in data if isinstance(r, dict) and r.get("engine") in PACKAGES and isinstance(r.get("model"), str)]


def _save_records(records: List[Dict[str, Any]]) -> None:
    _write_json(_models_record(), records)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._") or "model"


def model_dir(engine: str, model: str) -> Path:
    return ocr_root() / engine / _slug(model)


def _folder_size(folder: Path) -> int:
    total = 0
    try:
        for p in folder.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _worker_env(engine: str, folder: Path) -> Dict[str, str]:
    env = {"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1"}
    if engine == "doctr":
        env.update({"DOCTR_CACHE_DIR": str(folder), "USE_TORCH": "1", "DOCTR_MULTIPROCESSING_DISABLE": "TRUE"})
    return env


def _worker_cmd(py: Path, mode: str, engine: str, model: str, folder: Path, *extra: str) -> List[str]:
    return [str(py), "-u", str(WORKER), mode, "--engine", engine, "--model", model, "--dir", str(folder),
            "--device", load_settings()["device"], *extra]


def install_torch_model(engine: str, model: str, job: jobs.Job) -> Dict[str, Any]:
    py = ensure_package(engine, job)
    folder = model_dir(engine, model)
    folder.mkdir(parents=True, exist_ok=True)
    job.progress(phase=f"Downloading the {ENGINE_LABELS[engine]} model")

    def on_line(line: str) -> None:
        if "%" in line:
            _percent(job, line)
        elif line.strip():
            job.progress(phase=line.strip()[:160])

    try:
        code, lines = _run(_worker_cmd(py, "download", engine, model, folder), job, on_line=on_line,
                           env=_worker_env(engine, folder), cwd=folder, timeout=7200)
    except jobs.Cancelled:
        if not any(r["engine"] == engine and r["model"] == model for r in _records()):
            shutil.rmtree(folder, ignore_errors=True)
        raise
    result = lines.result or {}
    if code != 0 or not result.get("ok"):
        if not any(r["engine"] == engine and r["model"] == model for r in _records()):
            shutil.rmtree(folder, ignore_errors=True)
        raise OcrError(str(result.get("error") or f"The {ENGINE_LABELS[engine]} model could not be downloaded.")
                       + ("\n" + lines.tail() if not result.get("error") else ""))
    name = _display_name(engine, model)
    with _RECORD_LOCK:
        records = [r for r in _records() if not (r["engine"] == engine and r["model"] == model)]
        records.append({"engine": engine, "model": model, "installed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "version": result.get("version")})
        _save_records(records)
    return {"model": name, "id": model_id(engine, model)}


def _display_name(engine: str, model: str) -> str:
    if engine == "tesseract":
        return f"Tesseract {model}"
    if engine == "easyocr":
        return f"EasyOCR {model.replace(',', ', ')}"
    return f"docTR {model.replace('+', ' + ')}"


def delete_torch_model(engine: str, model: str) -> Dict[str, Any]:
    if not any(r["engine"] == engine and r["model"] == model for r in _records()):
        raise OcrError(f"The OCR model '{_display_name(engine, model)}' does not exist.")
    stop_model(model_id(engine, model))
    folder = model_dir(engine, model)
    if inside(folder, ocr_root()):
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            raise OcrError(f"Some files of {_display_name(engine, model)} are in use. Stop it and try again.")
    with _RECORD_LOCK:
        _save_records([r for r in _records() if not (r["engine"] == engine and r["model"] == model)])
    return {"model": _display_name(engine, model)}


def running_model() -> Optional[procs.Managed]:
    found = procs.running("ocr")
    return found[0] if found else None


def _ready(url: str) -> bool:
    return procs.http_status(url + "/health", timeout=3) == 200


def start_model(mid: str, job: jobs.Job) -> Dict[str, Any]:
    engine, model = split_id(mid)
    if engine == "tesseract":
        raise OcrError("Tesseract runs for each picture and needs no starting.")
    if not any(r["engine"] == engine and r["model"] == model for r in _records()):
        raise OcrError(f"{_display_name(engine, model)} is not installed.")
    current = procs.get("ocr:" + mid)
    if current is not None:
        return {"model": _display_name(engine, model), "url": current.url}
    py = ensure_package(engine, job)
    for other in procs.running("ocr"):
        job.progress(phase=f"Stopping {other.extra.get('name') or other.target}")
        procs.stop(other.key)
    folder = model_dir(engine, model)
    token = secrets.token_hex(16)
    last: Optional[Exception] = None
    for attempt in range(3):
        port = procs.free_port(OCR_PORT)
        job.progress(phase=f"Loading {_display_name(engine, model)}")
        try:
            m = procs.start("ocr:" + mid, "ocr", mid, _worker_cmd(py, "serve", engine, model, folder, "--port", str(port)),
                            port, _ready, cwd=folder, timeout=START_TIMEOUT, cancelled=lambda: job.cancelled,
                            env={**_worker_env(engine, folder), "AXIS_OCR_TOKEN": token},
                            extra={"name": _display_name(engine, model), "token": token})
            return {"model": _display_name(engine, model), "url": m.url}
        except procs.Cancelled:
            raise jobs.Cancelled("Cancelled.")
        except procs.ProcessError as exc:
            last = exc
            if "address already in use" in str(exc).lower() or "bind" in str(exc).lower():
                continue
            raise OcrError(_hint(str(exc))) from exc
    raise OcrError(_hint(str(last)))


def stop_model(mid: str) -> Dict[str, Any]:
    procs.stop("ocr:" + mid)
    return {"model": mid}


def _hint(message: str) -> str:
    low = message.lower()
    if "out of memory" in low:
        return "The model did not fit in memory. Set Device to CPU under Engine settings, or close other programs.\n" + message
    if "no module named" in low:
        return "A package the model needs is missing. Delete the model and install it again.\n" + message
    return message


# ------------------------------------------------------------------ listing


def list_models() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    info = tesseract_info()
    binary_ok = bool(info and info.get("works"))
    mine = axis_langs()
    system = [l for l in ((info or {}).get("langs") or []) if l != "osd"]
    for lang in sorted(set(mine) | set(system)):
        if lang == "osd":
            continue
        own = mine.get(lang)
        out.append({"id": model_id("tesseract", lang), "engine": "tesseract", "model": lang,
                    "name": f"Tesseract {lang}", "source": "axis" if own else "tesseract",
                    "variant": (own or {}).get("variant") or "", "size": (own or {}).get("size"),
                    "path": str(tessdata_dir() / f"{lang}.traineddata") if own else (info or {}).get("dir"),
                    "deletable": bool(own), "startable": False, "usable": binary_ok,
                    "also_system": bool(own) and lang in system})
    run = running_model()
    env = env_info() or {}
    packages = env.get("packages") or {}
    for r in sorted(_records(), key=lambda r: (r["engine"], r["model"])):
        mid = model_id(r["engine"], r["model"])
        folder = model_dir(r["engine"], r["model"])
        on = run is not None and run.target == mid
        dead = procs.last_exit("ocr:" + mid)
        out.append({"id": mid, "engine": r["engine"], "model": r["model"], "name": _display_name(r["engine"], r["model"]),
                    "source": "axis", "size": _folder_size(folder), "path": str(folder), "deletable": True,
                    "startable": True, "usable": bool(env) and bool(packages.get(r["engine"])),
                    "running": on, "ready": bool(on and run.ready), "adopted": bool(on and run.adopted),
                    "crashed": dead.tail(15) if dead is not None and not on else None,
                    "installed": r.get("installed")})
    return out


def status() -> Dict[str, Any]:
    binary = tesseract_binary()
    info = tesseract_info(binary) if binary else None
    settings = load_settings()
    record = _read_json(_engines_record(), {})
    tess_rec = record.get("tesseract") if isinstance(record.get("tesseract"), dict) else None
    env = env_info()
    builds = torch_builds()
    run = running_model()
    models = list_models()
    return {
        "tesseract": {"binary": str(binary) if binary else None, "setting": settings["tesseract"],
                      "version": (info or {}).get("version"), "works": bool(info and info.get("works")),
                      "system_dir": (info or {}).get("dir"), "system_langs": (info or {}).get("langs") or [],
                      "axis": bool(tess_rec and binary and tess_rec.get("binary") == str(binary)),
                      "plan": tesseract_install_plan()},
        "pytorch": {"installed": env, "python": str(env_python()) if env_python() else None,
                    "base_python": base_python(), "builds": builds,
                    "build_labels": {b: TORCH_LABELS[b] for b in ["auto"] + builds if b in TORCH_LABELS},
                    "default_build": builds[0], "root": str(env_dir())},
        "settings": settings,
        "models": models,
        "ready": any(m["usable"] for m in models),
        "running": {"model": run.target, "url": run.url, "ready": run.ready, "adopted": run.adopted} if run else None,
        "catalog": CATALOG,
        "psm": [{"value": k, "label": v} for k, v in PSM.items()],
        "root": str(ocr_root()),
    }


# ------------------------------------------------------------------ the API (called by manager.py)


def install(body: Dict[str, Any]) -> Dict[str, Any]:
    kind = body.get("kind") or "tesseract"
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    if kind == "tesseract":
        langs, variant = tesseract_langs(name)
        label = "+".join(langs) + (" (best)" if variant == "best" else "")
        job = jobs.submit("install", "ocr", "tesseract:" + "+".join(langs), f"Installing Tesseract {label}",
                          lambda j: install_languages(langs, variant, j))
    elif kind == "easyocr":
        model = ",".join(easyocr_langs(name))
        job = jobs.submit("install", "ocr", model_id("easyocr", model), f"Installing {_display_name('easyocr', model)}",
                          lambda j: install_torch_model("easyocr", model, j))
    elif kind == "doctr":
        model = "+".join(doctr_pair(name))
        job = jobs.submit("install", "ocr", model_id("doctr", model), f"Installing {_display_name('doctr', model)}",
                          lambda j: install_torch_model("doctr", model, j))
    else:
        raise OcrError("Choose Tesseract, EasyOCR or docTR.")
    return {"job": job.public()}


def _known(mid: str) -> Dict[str, Any]:
    for m in list_models():
        if m["id"] == mid:
            return m
    engine, model = split_id(mid)
    raise OcrError(f"The OCR model '{_display_name(engine, model)}' does not exist. It may have been deleted.")


def start(mid: str) -> Dict[str, Any]:
    m = _known(mid)
    if not m["startable"]:
        raise OcrError("Tesseract runs for each picture and needs no starting.")
    procs.forget_exit("ocr:" + mid)
    job = jobs.submit("start", "ocr", mid, f"Starting {m['name']}", lambda j: start_model(mid, j))
    return {"job": job.public()}


def stop(mid: str) -> Dict[str, Any]:
    name = mid
    try:
        name = _known(mid)["name"]
    except OcrError:
        pass
    job = jobs.submit("stop", "ocr", mid, f"Stopping {name}", lambda j: stop_model(mid), cancellable=False)
    return {"job": job.public()}


def delete(mid: str) -> Dict[str, Any]:
    m = _known(mid)
    if not m["deletable"]:
        raise OcrError(f"{m['model']} came with Tesseract, so Axis cannot delete it.")

    def work(j: jobs.Job) -> Dict[str, Any]:
        j.progress(phase=f"Deleting {m['name']}")
        if m["engine"] == "tesseract":
            return delete_language(m["model"])
        return delete_torch_model(m["engine"], m["model"])

    job = jobs.submit("delete", "ocr", mid, f"Deleting {m['name']}", work, cancellable=False)
    return {"job": job.public()}


ENGINE_NAMES = {"tesseract": "Tesseract", "pytorch": "the PyTorch environment for OCR"}


def submit_engine(engine: str, build: Optional[str]) -> jobs.Job:
    if engine == "tesseract":
        verb = "Updating" if tesseract_binary() else "Installing"
        return jobs.submit("install", "engine", "tesseract", f"{verb} Tesseract", install_tesseract)
    if engine == "pytorch":
        if build and build not in TORCH_BUILDS and build not in torch_builds():
            raise OcrError(f"Unknown build '{build}'.")
        verb = "Updating" if env_info() else "Installing"
        return jobs.submit("install", "engine", "pytorch", f"{verb} PyTorch for OCR",
                           lambda j: install_env(build or torch_builds()[0], j))
    raise OcrError("Choose Tesseract or PyTorch.")


# ------------------------------------------------------------------ reading text


def check_image(path: Path) -> Path:
    if path.suffix.lower() not in IMAGE_TYPES:
        raise OcrError("OCR reads PNG, JPEG, BMP, TIFF and WebP pictures.")
    if path.stat().st_size > MAX_IMAGE:
        raise OcrError(f"Pictures larger than {MAX_IMAGE // (1024 * 1024)} MB cannot be read.")
    return path


def text_from_tsv(tsv: str) -> Tuple[str, Optional[float], int]:
    """Text, mean word confidence (0-100) and word count from Tesseract's TSV output."""
    paragraphs: List[List[str]] = []
    para_key = line_key = None
    confs: List[float] = []
    words = 0
    lines = tsv.splitlines()
    for row in lines[1:] if lines and lines[0].startswith("level") else lines:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = "\t".join(cols[11:]).strip()
        if not text:
            continue
        pk = (cols[1], cols[2], cols[3])
        lk = pk + (cols[4],)
        if pk != para_key:
            paragraphs.append([])
            para_key, line_key = pk, None
        if lk != line_key:
            paragraphs[-1].append(text)
            line_key = lk
        else:
            paragraphs[-1][-1] += " " + text
        words += 1
        try:
            c = float(cols[10])
            if c >= 0:
                confs.append(c)
        except ValueError:
            pass
    text = "\n\n".join("\n".join(p) for p in paragraphs if p)
    return text, (round(sum(confs) / len(confs), 1) if confs else None), words


def _tessdata_for(langs: List[str]) -> Optional[Path]:
    """The tessdata folder that holds every language, copying Tesseract's own files into Axis's
    folder when a picture needs languages from both."""
    mine = axis_langs()
    info = tesseract_info() or {}
    system = info.get("langs") or []
    missing = [l for l in langs if l not in mine and l not in system]
    if missing:
        raise OcrError(f"The Tesseract language{'s' if len(missing) > 1 else ''} {', '.join(missing)} "
                       f"{'are' if len(missing) > 1 else 'is'} not installed. Install "
                       f"{'them' if len(missing) > 1 else 'it'} on the Models page (OCR models).")
    if all(l in mine for l in langs):
        return tessdata_dir()
    if all(l in system for l in langs):
        return None
    src = Path(info["dir"]) if info.get("dir") else None
    for l in langs:
        if l not in mine and src is not None and (src / f"{l}.traineddata").is_file():
            dest = tessdata_dir() / f"{l}.traineddata"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / f"{l}.traineddata", dest)
            with _RECORD_LOCK:
                record = _read_json(_langs_record(), {})
                record[l] = {"variant": "copied", "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}
                _write_json(_langs_record(), record)
    return tessdata_dir()


def _run_tesseract(path: Path, langs: List[str], psm: int, emit: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    binary = tesseract_binary()
    if binary is None:
        raise OcrError("Tesseract is not installed. Install it on the Models page (OCR models).")
    folder = _tessdata_for(langs)
    cmd = [str(binary), "stdin", "stdout", "-l", "+".join(langs), "--psm", str(psm)]
    if folder is not None:
        cmd += ["--tessdata-dir", str(folder)]
    # The "tsv" config file lives in Tesseract's own tessdata/configs, which Axis's folder does not have.
    cmd += ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
    emit({"phase": "Reading the text with Tesseract"})
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, input=path.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=RUN_TIMEOUT, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"Tesseract took longer than {int(RUN_TIMEOUT)} seconds.") from exc
    except OSError as exc:
        raise OcrError(f"Could not start Tesseract: {exc.strerror or exc}") from exc
    err = r.stderr.decode("utf-8", errors="replace").strip()
    if r.returncode != 0:
        raise OcrError("Tesseract could not read this picture.\n" + err[-800:])
    text, conf, words = text_from_tsv(r.stdout.decode("utf-8", errors="replace"))
    return {"text": text, "confidence": conf, "words": words, "engine": "tesseract",
            "model": "+".join(langs), "name": "Tesseract " + "+".join(langs), "device": "cpu"}


def _post(url: str, body: Dict[str, Any], token: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Axis-Token": token})
    try:
        with _LOCAL.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            data = {}
        raise OcrError(str(data.get("error") or f"The OCR model answered with an error ({exc.code}).")) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OcrError(f"The OCR model stopped answering ({getattr(exc, 'reason', exc)}).") from exc
    if not isinstance(data, dict):
        raise OcrError("The OCR model gave an answer Axis does not understand.")
    if data.get("error"):
        raise OcrError(str(data["error"]))
    return data


def _run_torch(mid: str, path: Path, emit: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    engine, model = split_id(mid)
    if not any(r["engine"] == engine and r["model"] == model for r in _records()):
        raise OcrError(f"{_display_name(engine, model)} is not installed.")
    m = procs.get("ocr:" + mid)
    started = time.time()
    if m is not None and m.ready:
        emit({"phase": f"Reading the text with {_display_name(engine, model)}"})
        box: Dict[str, Any] = {}

        def call() -> None:
            try:
                box["result"] = _post(m.url + "/ocr", {"image": str(path)}, str(m.extra.get("token") or ""), RUN_TIMEOUT)
            except Exception as exc:  # passed back to the waiting thread
                box["error"] = exc

        t = threading.Thread(target=call, daemon=True)
        t.start()
        while t.is_alive():
            t.join(2.0)
            if t.is_alive():
                emit({"elapsed": round(time.time() - started)})
        if "error" in box:
            raise box["error"]
        return {**box["result"], "name": _display_name(engine, model), "kept_on": True}
    py = env_python()
    if py is None or not ((env_info() or {}).get("packages") or {}).get(engine):
        raise OcrError(f"{ENGINE_LABELS[engine]} is not installed in the OCR Python environment. Delete the model and "
                       "install it again.")
    folder = model_dir(engine, model)
    emit({"phase": f"Loading {_display_name(engine, model)} (Start it on the Models page to keep it loaded)"})

    def on_line(line: str) -> None:
        if line.startswith("Reading"):
            emit({"phase": line[:160]})

    code, lines = _run(_worker_cmd(py, "run", engine, model, folder, "--image", str(path)), None, on_line=on_line,
                       env=_worker_env(engine, folder), cwd=folder, timeout=RUN_TIMEOUT,
                       beat=lambda: emit({"elapsed": round(time.time() - started)}))
    result = lines.result or {}
    if code != 0 or not result.get("ok"):
        raise OcrError(_hint(str(result.get("error") or "The OCR model could not read this picture.")
                             + ("\n" + lines.tail() if not result.get("error") else "")))
    return {**result, "name": _display_name(engine, model), "kept_on": False}


def run(body: Dict[str, Any], path: Path, emit: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    """Read the text in a picture. emit receives {"phase"} and {"elapsed"} events while it works."""
    mid = body.get("model")
    if not isinstance(mid, str) or not mid:
        raise OcrError("Choose an OCR model.")
    engine, model = split_id(mid)
    check_image(path)
    started = time.time()
    if engine == "tesseract":
        langs_text = body.get("languages") if isinstance(body.get("languages"), str) and body["languages"].strip() else model
        langs, _ = tesseract_langs(langs_text)
        try:
            psm = int(body.get("psm") or 3)
        except (TypeError, ValueError):
            psm = 3
        if psm not in PSM:
            raise OcrError("Choose a page layout from the list.")
        result = _run_tesseract(path, langs, psm, emit)
    else:
        result = _run_torch(mid, path, emit)
    result.pop("ok", None)
    result["seconds"] = round(time.time() - started, 1)
    result["id"] = mid
    return result
