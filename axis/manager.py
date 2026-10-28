"""Installs, starts, stops and deletes models for the user.

Chat models
  * Ollama: Axis uses Ollama's own API to download (pull), load, unload and
    delete models. If Ollama is installed but its server is not running, Axis
    starts ``ollama serve`` itself.
  * llama.cpp: Axis downloads GGUF files from Hugging Face into
    ~/Axis/models/llm and runs one ``llama-server`` per model that is on, each on
    its own port. GGUF files copied into that folder by hand are found too.

Image models
  * Installed by name (sd1.5, sdxl, z-image-turbo, ...; see catalog.py), which
    downloads every file the model needs, or one file at a time by Hugging Face
    name. Files go to ~/Axis/models/image and are set up as image models.
  * "Start" runs stable-diffusion.cpp's ``sd-server`` with the model, which keeps
    it in memory so each picture starts immediately. "Stop" ends it. When no
    image model is on, pictures are made with ``sd-cli`` as before.

Engines
  * llama.cpp and stable-diffusion.cpp are installed by Axis itself when they
    are missing (see engines.py): on the first start, from the Models page, and
    as the first step of installing or starting a model that needs them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import backend, catalog, engines, hub, images, jobs, procs
from .paths import axis_home, image_root, inside, llm_root

START_TIMEOUT = float(os.environ.get("AXIS_START_TIMEOUT", "900"))
LLAMA_PORT = 8080
IMAGE_PORT = 7861
OLLAMA_PORT = 11434
MAX_SCAN = 2000

_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ManageError(Exception):
    user_facing = True


# ------------------------------------------------------------------ settings


def _settings_path() -> Path:
    return axis_home() / "engine-settings.json"


def default_ctx() -> int:
    return backend.NUM_CTX if backend.NUM_CTX > 0 else 8192


def load_settings() -> Dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data = data if isinstance(data, dict) else {}
    ctx = data.get("llama_ctx")
    return {
        "llama_server": str(data.get("llama_server") or ""),
        "llama_ctx": ctx if isinstance(ctx, int) and 512 <= ctx <= 1048576 else default_ctx(),
        "llama_args": str(data.get("llama_args") or ""),
        "ollama": str(data.get("ollama") or ""),
    }


def save_settings(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ManageError("Settings must be a JSON object.")
    current = load_settings()
    for key, label, limit in (("llama_server", "llama-server path", 1000), ("llama_args", "Extra arguments", 2000),
                              ("ollama", "Ollama path", 1000)):
        if key in body:
            value = body[key]
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ManageError(f"{label} must be text.")
            value = value.strip().strip('"').strip()
            if len(value) > limit:
                raise ManageError(f"{label} is too long.")
            current[key] = value
    if "llama_ctx" in body:
        try:
            ctx = int(body["llama_ctx"])
        except (TypeError, ValueError):
            raise ManageError("Context size must be a whole number of tokens.")
        if not 512 <= ctx <= 1048576:
            raise ManageError("Context size must be between 512 and 1048576 tokens.")
        current["llama_ctx"] = ctx
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return current


def _find_program(configured: str, env: str, names: List[str], extra: List[Path],
                  axis_copy: Optional[Path] = None) -> Optional[Path]:
    """A program from the settings, an environment variable, the copy Axis installed, the PATH,
    or a usual install location."""
    for value in (configured, os.environ.get(env, "")):
        if value:
            p = Path(value).expanduser()
            if p.is_dir():
                for name in names:
                    for cand in (p / name, p / (name + ".exe")):
                        if cand.is_file():
                            return cand.resolve()
            if p.is_file():
                return p.resolve()
            found = shutil.which(value)
            return Path(found).resolve() if found else None
    if axis_copy is not None:
        return axis_copy
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    for p in extra:
        if p.is_file():
            return p.resolve()
    return None


# -------------------------------------------------------------------- Ollama


def ollama_binary() -> Optional[Path]:
    extra: List[Path] = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            extra.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    else:
        extra += [Path("/Applications/Ollama.app/Contents/Resources/ollama"), Path("/usr/local/bin/ollama"),
                  Path("/usr/bin/ollama"), Path("/opt/homebrew/bin/ollama")]
    return _find_program(load_settings()["ollama"], "AXIS_OLLAMA", ["ollama"], extra)


def ollama_url(server_url: Optional[str] = None) -> str:
    """Where Ollama answers: --url / AXIS_URL when that is Ollama, else OLLAMA_HOST, else the default."""
    explicit = backend.resolve_url(server_url)
    if explicit:
        try:
            if backend._probe_ollama(explicit):
                return explicit
        except backend.BackendError:
            return explicit
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if host:
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        parts = urllib.parse.urlsplit(host)
        name = parts.hostname or "127.0.0.1"
        if name in ("0.0.0.0", "::", ""):
            name = "127.0.0.1"
        port = parts.port or OLLAMA_PORT
        return f"{parts.scheme}://{name}:{port}"
    return f"http://127.0.0.1:{OLLAMA_PORT}"


def _is_local(url: str) -> bool:
    return (urllib.parse.urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def ollama_version(url: str) -> Optional[str]:
    try:
        with _LOCAL.open(url + "/api/version", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return str(data.get("version") or "?") if isinstance(data, dict) else None
    except Exception:
        return None


def _ollama_call(url: str, path: str, body: Dict[str, Any], method: str = "POST", timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url + path, data=json.dumps(body).encode("utf-8"), method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _LOCAL.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ManageError(backend._http_error_message(exc)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManageError(f"Ollama stopped answering at {url} ({getattr(exc, 'reason', exc)}).") from exc
    lines = [line for line in raw.splitlines() if line.strip()]
    try:
        data = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict) and data.get("error"):
        raise ManageError(str(data["error"]))
    return data


def start_ollama_server(url: str, check: Callable[[], None] = lambda: None,
                        phase: Callable[[str], None] = lambda p: None) -> str:
    if ollama_version(url):
        return url
    if not _is_local(url):
        raise ManageError(f"Ollama is not answering at {url}, and Axis can only start Ollama on this computer.")
    binary = ollama_binary()
    if binary is None:
        raise ManageError("Ollama is not installed. Download it from ollama.com/download, install it, then try again.")
    phase("Starting Ollama")
    port = urllib.parse.urlsplit(url).port or OLLAMA_PORT
    env = {"OLLAMA_HOST": f"127.0.0.1:{port}"}
    try:
        procs.start("ollama", "ollama", "", [str(binary), "serve"], port,
                    ready=lambda u: ollama_version(u) is not None, timeout=60, env=env,
                    cancelled=lambda: _raises(check))
    except procs.Cancelled:
        raise jobs.Cancelled("Cancelled.")
    except procs.ProcessError as exc:
        if ollama_version(url):  # another Ollama (the desktop app) started at the same time
            return url
        raise ManageError(f"Ollama could not be started. {exc}") from exc
    return url


def _raises(check: Callable[[], None]) -> bool:
    try:
        check()
    except Exception:
        return True
    return False


def stop_ollama_server() -> None:
    if procs.get("ollama") is None:
        raise ManageError("Axis did not start Ollama, so it cannot stop it. Quit Ollama from its menu bar or tray icon.")
    procs.stop("ollama")


OLLAMA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:]{0,199}$")


def ollama_name(text: str) -> str:
    """A model name for Ollama, from a name, an `ollama run` command or an ollama.com / Hugging Face link."""
    t = (text or "").strip().strip('"').strip("'").strip()
    low = t.lower()
    command = False
    for prefix in ("ollama run ", "ollama pull "):
        if low.startswith(prefix):
            t, command = t[len(prefix):].strip(), True
            break
    words = t.split()
    if len(words) > 1 and not command:
        raise ManageError(f"'{t}' is not a valid Ollama model name. Names have no spaces, like llama3.1:8b.")
    t = words[0] if words else ""  # after "ollama run", drop options such as --keepalive
    if re.match(r"^https?://", t, re.IGNORECASE):
        parts = urllib.parse.urlsplit(t)
        host = (parts.hostname or "").lower()
        segs = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
        if host in ("ollama.com", "www.ollama.com", "registry.ollama.ai"):
            if segs[:1] == ["library"]:
                segs = segs[1:]
            if not segs or len(segs) > 2 or segs[0] in ("search", "download", "blog"):
                raise ManageError("That ollama.com link is not a model page. Open a model at ollama.com/library and copy its name.")
            t = "/".join(segs)
        elif host in ("huggingface.co", "www.huggingface.co", "hf.co"):
            if len(segs) < 2:
                raise ManageError("That Hugging Face link is not a model page.")
            t = f"hf.co/{segs[0]}/{segs[1]}"
        else:
            raise ManageError("Only ollama.com and huggingface.co links can be installed with Ollama.")
    if t.lower().startswith("huggingface.co/"):
        t = "hf.co/" + t[len("huggingface.co/"):]
    if not t:
        raise ManageError("Type the name of a model from ollama.com/library, like llama3.1:8b.")
    if not OLLAMA_NAME.fullmatch(t) or ".." in t or t.endswith((":", "/")):
        raise ManageError(f"'{t}' is not a valid Ollama model name. Names look like llama3.1:8b or qwen3.")
    return t


def ollama_pull(url: str, name: str, job: jobs.Job) -> Dict[str, Any]:
    start_ollama_server(url, job.check, lambda p: job.progress(phase=p))
    job.progress(phase="Asking Ollama for the model")
    req = urllib.request.Request(url + "/api/pull", data=json.dumps({"model": name, "stream": True}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    layers: Dict[str, List[int]] = {}
    try:
        resp = _LOCAL.open(req, timeout=900)
    except urllib.error.HTTPError as exc:
        raise ManageError(_pull_error(name, backend._http_error_message(exc))) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManageError(f"Ollama stopped answering at {url} ({getattr(exc, 'reason', exc)}).") from exc
    success = False
    with resp:
        for raw in resp:
            job.check()
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("error"):
                raise ManageError(_pull_error(name, str(ev["error"])))
            status = str(ev.get("status") or "")
            digest = ev.get("digest")
            if digest and isinstance(ev.get("total"), int):
                layers[digest] = [int(ev.get("completed") or 0), ev["total"]]
                done = sum(c for c, _ in layers.values())
                total = sum(t for _, t in layers.values())
                job.progress(phase=f"Downloading {name}", done=done, total=total)
            elif status:
                job.progress(phase={"pulling manifest": "Reading the model list",
                                    "verifying sha256 digest": "Verifying the download",
                                    "writing manifest": "Finishing", "removing any unused layers": "Tidying up",
                                    "success": "Installed"}.get(status, status[:1].upper() + status[1:]))
            if status == "success":
                success = True
    if not success:
        raise ManageError(f"Ollama stopped before {name} finished downloading. Install it again to resume.")
    return {"model": name}


def _pull_error(name: str, err: str) -> str:
    low = err.lower()
    if "file does not exist" in low or "manifest unknown" in low or "not found" in low:
        return (f"No model named '{name}' was found. Check the name at ollama.com/library "
                "(for example llama3.1:8b), including the part after the colon.")
    if "no space" in low:
        return f"The disk is full, so {name} could not be installed. Free some space and try again."
    return f"Ollama could not install {name}: {err}"


def ollama_installed(url: str) -> List[str]:
    try:
        with _LOCAL.open(url + "/api/tags", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    return [m.get("name") or m.get("model") for m in (data.get("models") or []) if isinstance(m, dict)
            and (m.get("name") or m.get("model"))]


def ollama_loaded(url: str) -> List[str]:
    try:
        with _LOCAL.open(url + "/api/ps", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    return [m.get("name") or m.get("model") for m in (data.get("models") or []) if isinstance(m, dict)
            and (m.get("name") or m.get("model"))]


def _ollama_known(url: str, name: str) -> str:
    installed = ollama_installed(url)
    for n in installed:
        if n == name or n == f"{name}:latest":
            return n
    raise ManageError(f"'{name}' is not installed in Ollama." + (" Installed: " + ", ".join(installed) if installed else ""))


def ollama_load(url: str, name: str, job: jobs.Job) -> Dict[str, Any]:
    start_ollama_server(url, job.check, lambda p: job.progress(phase=p))
    name = _ollama_known(url, name)
    job.progress(phase=f"Loading {name} into memory")
    body: Dict[str, Any] = {"model": name, "keep_alive": -1, "stream": False}
    if backend.NUM_CTX > 0:
        body["options"] = {"num_ctx": backend.NUM_CTX}  # load at Axis's size, so the first chat does not reload it
    backend.pin_ollama(url, name)
    _ollama_call(url, "/api/generate", body, timeout=START_TIMEOUT)
    return {"model": name}


def ollama_unload(url: str, name: str, job: Optional[jobs.Job] = None) -> Dict[str, Any]:
    backend.unpin_ollama(url, name)
    if not ollama_version(url):
        return {"model": name}  # Ollama is not running, so nothing is loaded
    if job:
        job.progress(phase=f"Unloading {name}")
    _ollama_call(url, "/api/generate", {"model": name, "keep_alive": 0, "stream": False}, timeout=120)
    return {"model": name}


def ollama_delete(url: str, name: str, job: jobs.Job) -> Dict[str, Any]:
    start_ollama_server(url, job.check, lambda p: job.progress(phase=p))
    name = _ollama_known(url, name)
    if name in ollama_loaded(url):
        try:
            ollama_unload(url, name, job)
        except ManageError:
            pass  # deleting still works; Ollama unloads it
    job.progress(phase=f"Deleting {name}")
    _ollama_call(url, "/api/delete", {"model": name}, method="DELETE", timeout=120)
    return {"model": name}


# ------------------------------------------------------------------ llama.cpp


def llama_binary() -> Optional[Path]:
    extra: List[Path] = []
    if os.name != "nt":
        extra += [Path("/opt/homebrew/bin/llama-server"), Path("/usr/local/bin/llama-server")]
    else:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            extra.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "llama-server.exe")
    return _find_program(load_settings()["llama_server"], "AXIS_LLAMA_SERVER", ["llama-server"], extra,
                         engines.program("llama-server"))


def _display_name(file: Path) -> str:
    name = hub.SPLIT_RE.sub(".gguf", file.name)
    return name[:-5] if name.lower().endswith(".gguf") else name


def _alias(name: str) -> str:
    return re.sub(r"[,\s]+", "-", name).strip("-") or "model"


def scan_llama() -> List[Dict[str, Any]]:
    """GGUF chat models in ~/Axis/models/llm (the first part only, for split files)."""
    root = llm_root()
    if not root.is_dir():
        return []
    out = []
    count = 0
    for f in sorted(root.rglob("*.gguf"), key=lambda p: str(p).lower()):
        count += 1
        if count > MAX_SCAN:
            break
        low = f.name.lower()
        if "mmproj" in low or not f.is_file():
            continue
        m = hub.SPLIT_RE.search(low)
        if m and int(m.group(1)) != 1:
            continue
        parts = _parts(f)
        try:
            size = sum(p.stat().st_size for p in parts)
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        top = rel.split("/")[0]
        repo = top.replace("__", "/", 1) if "__" in top and "/" in rel else ""
        out.append({"id": rel, "name": _display_name(f), "file": str(f), "size": size, "parts": len(parts),
                    "repo": repo, "missing_parts": m is not None and len(parts) != int(m.group(2))})
    return out


def _parts(first: Path) -> List[Path]:
    m = hub.SPLIT_RE.search(first.name)
    if not m:
        return [first]
    stem = first.name[: m.start()]
    count = int(m.group(2))
    return [p for p in (first.with_name(f"{stem}-{i:05d}-of-{count:05d}.gguf") for i in range(1, count + 1)) if p.is_file()]


def _llama_model(model_id: str) -> Dict[str, Any]:
    if not isinstance(model_id, str) or not model_id or "\\" in model_id or model_id.startswith("/"):
        raise ManageError("Choose a llama.cpp model.")
    for m in scan_llama():
        if m["id"] == model_id:
            if not inside(Path(m["file"]), llm_root()):
                raise ManageError("That model file is outside the models folder.")
            return m
    raise ManageError(f"The llama.cpp model '{model_id}' does not exist. It may have been deleted.")


def llama_running(model_id: str) -> Optional[procs.Managed]:
    return procs.get("llama:" + model_id)


def chat_urls() -> List[str]:
    """Addresses of the llama-servers Axis started, for backend.connect."""
    return [m.url for m in procs.running("llama") if m.ready]


def _llama_ready(url: str) -> bool:
    return procs.http_status(url + "/health") == 200


def llama_start(model_id: str, job: jobs.Job) -> Dict[str, Any]:
    model = _llama_model(model_id)
    if model["missing_parts"]:
        raise ManageError(f"Some parts of {model['name']} are missing. Delete it and install it again.")
    if llama_running(model_id):
        return {"model": model["name"]}
    engines.ensure("llama", llama_binary, job)
    binary = llama_binary()
    if binary is None:
        raise ManageError("llama-server was not found. Install llama.cpp on the Models page, or set the path to "
                          "llama-server under Engine settings.")
    settings = load_settings()
    taken = {m.extra.get("alias") for m in procs.running("llama")}
    alias, n = _alias(model["name"]), 2
    while alias in taken:
        alias, n = f"{_alias(model['name'])}-{n}", n + 1
    jinja = True
    last_error: Optional[Exception] = None
    for attempt in range(3):
        port = procs.free_port(LLAMA_PORT, avoid=[OLLAMA_PORT])
        cmd = [str(binary), "-m", model["file"], "--host", "127.0.0.1", "--port", str(port),
               "-c", str(settings["llama_ctx"]), "--alias", alias]
        if jinja:
            cmd.append("--jinja")  # needed for tool calls on older builds; the default on newer ones
        cmd += images.split_args(settings["llama_args"])
        job.progress(phase=f"Loading {model['name']}")
        try:
            procs.start("llama:" + model_id, "llama", model_id, cmd, port, _llama_ready, cwd=binary.parent,
                        timeout=START_TIMEOUT, cancelled=lambda: job.cancelled, env=engines.run_env(binary),
                        on_line=lambda line: _llama_phase(job, line),
                        extra={"alias": alias, "name": model["name"]})
            return {"model": alias, "url": f"http://127.0.0.1:{port}"}
        except procs.Cancelled:
            raise jobs.Cancelled("Cancelled.")
        except procs.ProcessError as exc:
            text = str(exc).lower()
            last_error = exc
            if jinja and "--jinja" in text and ("invalid" in text or "unknown" in text):
                jinja = False
                continue
            if "bind" in text or "address already in use" in text or "port" in text and "in use" in text:
                continue  # another program took the port at the same moment; try the next one
            raise ManageError(_llama_hint(str(exc))) from exc
    raise ManageError(_llama_hint(str(last_error)))


def _llama_phase(job: jobs.Job, line: str) -> None:
    low = line.lower()
    if "loading model" in low or "load_tensors" in low:
        job.progress(phase="Loading the model into memory")
    elif "warming up" in low or "warmup" in low:
        job.progress(phase="Warming up")


def _llama_hint(message: str) -> str:
    low = message.lower()
    if "out of memory" in low or "failed to allocate" in low or "cudamalloc failed" in low:
        return ("The model did not fit in memory. Close other programs, choose a smaller model or quantization, "
                "or lower the context size under Engine settings.\n" + message)
    if "unknown model architecture" in low or "unsupported model" in low:
        return "This llama-server is too old for this model. Update llama.cpp.\n" + message
    return message


def llama_stop(model_id: str, job: Optional[jobs.Job] = None) -> Dict[str, Any]:
    key = "llama:" + model_id
    m = procs.get(key)
    if m is None:
        return {"model": model_id}
    if job:
        job.progress(phase=f"Stopping {m.extra.get('name') or model_id}")
    procs.stop(key)
    return {"model": model_id}


def llama_delete(model_id: str, job: jobs.Job) -> Dict[str, Any]:
    model = _llama_model(model_id)
    llama_stop(model_id, job)
    job.progress(phase=f"Deleting {model['name']}")
    first = Path(model["file"])
    root = llm_root()
    targets = _parts(first)
    m = hub.SPLIT_RE.search(first.name)
    if m:
        stem = first.name[: m.start()]
        targets += [p for p in first.parent.glob(f"{stem}-*-of-*.gguf*") if p not in targets]
    for p in targets + [first.with_name(first.name + ".part"), first.with_name(first.name + ".part.json")]:
        if inside(p, root):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ManageError(f"Could not delete {p.name}: {exc.strerror or exc}. If it is in use, stop it "
                                  "and try again.") from exc
    hub.prune_empty(first.parent, root)
    return {"model": model["name"]}


def llama_install(ref: hub.Ref, files: List[Dict[str, Any]], job: jobs.Job) -> Dict[str, Any]:
    engines.ensure("llama", llama_binary, job)
    root = llm_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        paths = hub.download_all(ref, files, root, job)
    except jobs.Cancelled:
        hub.cleanup_partial(root, ref.repo, files)
        raise
    rel = paths[0].relative_to(root).as_posix()
    return {"model": _display_name(paths[0]), "id": rel}


# ------------------------------------------------------------- image models


def sd_server_binary() -> Optional[Path]:
    configured = images.load_settings().get("sd_server", "")
    found = _find_program(configured, "AXIS_SD_SERVER", [], [])
    if found:
        return found
    cli = images.find_cli()
    if cli is not None:  # release archives put sd-server next to sd-cli
        for name in ("sd-server", "sd-server.exe"):
            cand = cli.parent / name
            if cand.is_file():
                return cand.resolve()
    axis_copy = engines.program("sd-server")
    if axis_copy is not None:
        return axis_copy
    found_path = shutil.which("sd-server")
    return Path(found_path).resolve() if found_path else None


def image_running() -> Optional[procs.Managed]:
    found = procs.running("image")
    return found[0] if found else None


def image_server_url(model_id: str) -> Optional[str]:
    m = procs.get("image:" + model_id)
    return m.url if m is not None and m.ready else None


def _image_ready(url: str) -> bool:
    try:
        with _LOCAL.open(url + "/sdcpp/v1/models/status", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        state = data.get("state") if isinstance(data, dict) else None
        if state == "error":
            raise procs.ProcessError(str(data.get("error") or "sd-server could not load the model."))
        return state in (None, "ready")
    except urllib.error.HTTPError as exc:
        return exc.code == 404  # an older sd-server: it only listens once the model is loaded
    except procs.ProcessError:
        raise
    except Exception:
        return False


def _sd_found() -> Optional[Path]:
    return images.find_cli() or sd_server_binary()


def image_start(model_id: str, check: Callable[[], None], phase: Callable[[str], None],
                job: Optional[jobs.Job] = None) -> str:
    """Start sd-server with an image model (stopping any other image model). Returns its URL."""
    model = images.get_model(model_id)
    if image_server_url(model_id):
        return image_server_url(model_id) or ""
    if model["missing"]:
        raise ManageError(f"Files for {model['name']} are missing: {', '.join(model['missing'])}.")
    if job is not None:
        engines.ensure("sd", sd_server_binary, job)
    binary = sd_server_binary()
    if binary is None:
        raise ManageError("sd-server was not found. Install stable-diffusion.cpp on the Models page (Image models), "
                          "or set the sd-server path there.")
    for other in procs.running("image"):
        phase(f"Stopping {other.extra.get('name') or other.target}")
        procs.stop(other.key)
    base = images.workdir()
    last: Optional[Exception] = None
    for attempt in range(3):
        port = procs.free_port(IMAGE_PORT)
        cmd = [str(binary)]
        for key in images.FILE_FIELDS:
            rel = model.get(key) or ""
            if rel:
                cmd += [images.FILE_FLAGS[key], str(images._resolve_file(rel, base))]
        cmd += ["--listen-ip", "127.0.0.1", "--listen-port", str(port)]
        cmd += images.split_args(model.get("extra_args") or "")
        phase(f"Loading {model['name']}")
        try:
            m = procs.start("image:" + model_id, "image", model_id, cmd, port, _image_ready, cwd=base,
                            timeout=START_TIMEOUT, cancelled=lambda: _raises(check), env=engines.run_env(binary),
                            on_line=lambda line: phase("Loading the model") if "loading" in line.lower() else None,
                            extra={"name": model["name"]})
            return m.url
        except procs.Cancelled:
            raise jobs.Cancelled("Cancelled.")
        except procs.ProcessError as exc:
            last = exc
            text = str(exc).lower()
            if "bind" in text or "address already in use" in text:
                continue
            if "unknown argument" in text or "invalid argument" in text:
                raise ManageError(f"sd-server did not accept this model's extra arguments ({model.get('extra_args')}). "
                                  f"Edit the model and remove options only sd-cli understands.\n{exc}") from exc
            raise ManageError(str(exc)) from exc
    raise ManageError(str(last))


def image_stop(model_id: str) -> Dict[str, Any]:
    procs.stop("image:" + model_id)
    return {"model": model_id}


ROLES = {"checkpoint": "model", "diffusion_model": "diffusion_model", "vae": "vae", "llm": "llm",
         "clip_l": "clip_l", "t5xxl": "t5xxl"}


def _new_image_fields(name: str, role: str, path: str, repo: str) -> Dict[str, Any]:
    low = name.lower()
    big = any(k in low for k in ("xl", "flux", "z_image", "z-image", "qwen", "sd3", "chroma", "hidream"))
    fast = any(k in low for k in ("turbo", "lightning", "schnell", "lcm", "hyper"))
    fields: Dict[str, Any] = {"name": name, "description": f"Installed from huggingface.co/{repo}",
                              ROLES[role]: path, "width": 1024 if big else 512, "height": 1024 if big else 512,
                              "steps": 4 if fast else 20, "cfg_scale": 1.0 if fast or role == "diffusion_model" else 7.0}
    if "sd3" in low and not fast:
        fields["cfg_scale"] = 4.5
    return fields


def _unique_name(name: str) -> str:
    names = {m["name"].lower() for m in images.list_models()}
    base, n = name[:70], 2
    while name.lower() in names:
        name, n = f"{base} ({n})", n + 1
    return name


def image_install(ref: hub.Ref, f: Dict[str, Any], role: str, target: str, job: jobs.Job) -> Dict[str, Any]:
    engines.ensure("sd", _sd_found, job)
    root = image_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        paths = hub.download_all(ref, [f], root, job)
    except jobs.Cancelled:
        hub.cleanup_partial(root, ref.repo, [f])
        raise
    path = str(paths[0])
    job.progress(phase="Setting up the image model")
    if target:
        images.get_model(target)  # raises if it was removed during the download
        if image_server_url(target):
            image_stop(target)
        updated = images.update_model(target, {ROLES[role]: path})
        return {"model": updated["name"], "id": updated["id"]}
    stem = PurePosixPath(f["path"]).name
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    created = images.create_model(_new_image_fields(_unique_name(stem), role, path, ref.repo))
    return {"model": created["name"], "id": created["id"]}


def catalog_plan(entry: Dict[str, Any], tag: str) -> List[Tuple[str, hub.Ref, Dict[str, Any]]]:
    """[(role, repository, file)] for every file of a catalog image model. Reads the repositories now,
    so a wrong name or a missing file is reported before anything is downloaded."""
    quant = (entry.get("tags") or {}).get(tag, "")
    listings: Dict[str, List[Dict[str, Any]]] = {}
    plan = []
    for part in entry["parts"]:
        ref = hub.Ref(repo=part["repo"])
        if part["repo"] not in listings:
            listings[part["repo"]] = hub.list_files(ref)
        try:
            f = catalog.pick(listings[part["repo"]], part, quant)
        except catalog.CatalogError as exc:
            raise ManageError(str(exc)) from exc
        plan.append((part["role"], ref, f))
    return plan


def catalog_title(entry: Dict[str, Any], tag: str) -> str:
    tags = entry.get("tags") or {}
    return entry["title"] + (f" ({tags[tag]})" if tag and tag != entry.get("default_tag") and tag in tags else "")


def catalog_image_install(entry: Dict[str, Any], tag: str, plan: List[Tuple[str, hub.Ref, Dict[str, Any]]],
                          job: jobs.Job) -> Dict[str, Any]:
    """Download every file of a catalog image model and set it up as one image model."""
    engines.ensure("sd", _sd_found, job)
    root = image_root()
    root.mkdir(parents=True, exist_ok=True)
    sizes = [f.get("size") for _, _, f in plan]
    total = sum(sizes) if all(isinstance(s, int) for s in sizes) else None
    if total is not None:
        need = sum(f["size"] - _have(hub.safe_dest(root, ref.repo, f["path"])) for _, ref, f in plan)
        free = shutil.disk_usage(root).free
        if free - need < hub.DISK_MARGIN:
            raise ManageError(f"Not enough disk space for {entry['title']}: it needs {hub._gb(need)} more and "
                              f"{hub._gb(free)} is free on that drive.")
    fields: Dict[str, Any] = {}
    done = 0
    try:
        for role, ref, f in plan:
            dest = hub.safe_dest(root, ref.repo, f["path"])
            base = done
            path = hub.download(ref, f, dest, job.check,
                                lambda d, t, base=base: job.progress(done=base + d, total=total),
                                lambda p: job.progress(phase=p))
            fields[ROLES[role]] = str(path)
            done += f.get("size") or path.stat().st_size
    except jobs.Cancelled:
        for _, ref, f in plan:
            hub.cleanup_partial(root, ref.repo, [f])
        raise
    job.progress(phase="Setting up the image model")
    title = catalog_title(entry, tag)
    for m in images.list_models():   # installed before: keep it, with its files restored
        if all(m.get(k) == v for k, v in fields.items()):
            return {"model": m["name"], "id": m["id"]}
    created = images.create_model({"name": _unique_name(title), "description": entry["description"],
                                   **entry["settings"], **fields})
    return {"model": created["name"], "id": created["id"]}


def _have(path: Path) -> int:
    for p in (path, path.with_name(path.name + ".part")):
        try:
            if p.is_file():
                return p.stat().st_size
        except OSError:
            pass
    return 0


# --------------------------------------------------------------- the API


def _engine(body: Dict[str, Any]) -> str:
    engine = body.get("engine")
    if engine not in ("ollama", "llamacpp", "image"):
        raise ManageError("Choose Ollama, llama.cpp or Image.")
    return engine


def _text(body: Dict[str, Any], key: str, label: str) -> str:
    value = body.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ManageError(f"{label} must be text.")
    return value.strip()


def install(body: Any, server_url: Optional[str]) -> Dict[str, Any]:
    """Start an install. Returns {"job": ...}, or {"choose": [...]} when the user must pick a file first."""
    if not isinstance(body, dict):
        raise ManageError("Request body must be a JSON object.")
    engine = _engine(body)
    name = _text(body, "name", "Name")
    choice = _text(body, "choice", "File")
    if engine == "ollama":
        model = ollama_name(name)
        url = ollama_url(server_url)
        if not ollama_version(url) and (not _is_local(url) or ollama_binary() is None):
            same = catalog.chat_ref(name)
            raise ManageError("Ollama is not installed or not running. Download it from ollama.com/download, "
                              "or choose \"Chat model, llama.cpp\": Axis installs llama.cpp for you"
                              + (f", and {model} works there too." if same else
                                 " and installs GGUF models from Hugging Face."))
        job = jobs.submit("install", "ollama", model, f"Installing {model}", lambda j: ollama_pull(url, model, j))
        return {"job": job.public()}

    if engine == "image" and not _text(body, "role", "Role") and catalog.is_image_name(name):
        try:
            found = catalog.image_entry(name)
        except catalog.CatalogError as exc:
            raise ManageError(str(exc)) from exc
        if found is None:
            raise ManageError(str(catalog.unknown_image(name)))
        entry, tag = found
        plan = catalog_plan(entry, tag)
        title = catalog_title(entry, tag)
        job = jobs.submit("install", "image", f"catalog:{entry['name']}:{tag}", f"Installing {title}",
                          lambda j: catalog_image_install(entry, tag, plan, j))
        return {"job": job.public()}

    if engine == "llamacpp":
        name = catalog.chat_ref(name) or name
    ref = hub.parse(name)
    files = hub.list_files(ref)
    if engine == "llamacpp":
        try:
            chosen = hub.pick_gguf(files, ref, choice)
        except hub.NeedChoice as need:
            return {"choose": need.options, "message": str(need), "name": name}
        label = PurePosixPath(chosen[0]["path"]).name
        job = jobs.submit("install", "llamacpp", f"{ref.repo}/{chosen[0]['path']}", f"Installing {label}",
                          lambda j: llama_install(ref, chosen, j))
        return {"job": job.public()}

    role = _text(body, "role", "Role") or "checkpoint"
    if role not in ROLES:
        raise ManageError("Choose what the file is: checkpoint, diffusion model, VAE, text encoder, CLIP-L or T5-XXL.")
    target = _text(body, "target", "Image model")
    if target:
        images.get_model(target)  # raises when it does not exist
    elif role not in ("checkpoint", "diffusion_model"):
        raise ManageError("A VAE or text encoder is added to an existing image model. Choose the model to add it to.")
    try:
        f = hub.pick_image_file(files, ref, choice)
    except hub.NeedChoice as need:
        return {"choose": need.options, "message": str(need), "name": name}
    label = PurePosixPath(f["path"]).name
    job = jobs.submit("install", "image", target or f"{ref.repo}/{f['path']}", f"Installing {label}",
                      lambda j: image_install(ref, f, role, target, j))
    return {"job": job.public()}


def start(body: Any, server_url: Optional[str]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ManageError("Request body must be a JSON object.")
    engine = _engine(body)
    model = _text(body, "model", "Model")
    if not model:
        raise ManageError("Choose a model to start.")
    if engine == "ollama":
        url = ollama_url(server_url)
        job = jobs.submit("start", "ollama", model, f"Starting {model}", lambda j: ollama_load(url, model, j),
                          cancellable=False)
    elif engine == "llamacpp":
        info = _llama_model(model)
        procs.forget_exit("llama:" + model)
        job = jobs.submit("start", "llamacpp", model, f"Starting {info['name']}", lambda j: llama_start(model, j))
    else:
        info = images.get_model(model)
        procs.forget_exit("image:" + model)
        job = jobs.submit("start", "image", model, f"Starting {info['name']}",
                          lambda j: {"url": image_start(model, j.check, lambda p: j.progress(phase=p), j)})
    return {"job": job.public()}


def stop(body: Any, server_url: Optional[str]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ManageError("Request body must be a JSON object.")
    engine = _engine(body)
    model = _text(body, "model", "Model")
    if not model:
        raise ManageError("Choose a model to stop.")
    if engine == "ollama":
        url = ollama_url(server_url)
        job = jobs.submit("stop", "ollama", model, f"Stopping {model}", lambda j: ollama_unload(url, model, j),
                          cancellable=False)
    elif engine == "llamacpp":
        job = jobs.submit("stop", "llamacpp", model, "Stopping " + _display_name(Path(model.split("/")[-1])),
                          lambda j: llama_stop(model, j), cancellable=False)
    else:
        try:
            label = "Stopping " + images.get_model(model)["name"]
        except images.ImageError:
            label = "Stopping the image model"
        job = jobs.submit("stop", "image", model, label, lambda j: image_stop(model),
                          cancellable=False)
    return {"job": job.public()}


def delete(body: Any, server_url: Optional[str]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ManageError("Request body must be a JSON object.")
    engine = _engine(body)
    model = _text(body, "model", "Model")
    if not model:
        raise ManageError("Choose a model to delete.")
    if engine == "ollama":
        url = ollama_url(server_url)
        job = jobs.submit("delete", "ollama", model, f"Deleting {model}", lambda j: ollama_delete(url, model, j),
                          cancellable=False)
    elif engine == "llamacpp":
        info = _llama_model(model)
        job = jobs.submit("delete", "llamacpp", model, f"Deleting {info['name']}", lambda j: llama_delete(model, j),
                          cancellable=False)
    else:
        info = images.get_model(model)
        files = body.get("files") if body.get("files") in ("none", "axis", "all") else "axis"

        def work(j: jobs.Job) -> Dict[str, Any]:
            j.progress(phase=f"Deleting {info['name']}")
            image_stop(model)
            return images.delete_model(model, files=files)

        job = jobs.submit("delete", "image", model, f"Deleting {info['name']}", work, cancellable=False)
    return {"job": job.public()}


def ollama_server(action: str, server_url: Optional[str]) -> Dict[str, Any]:
    url = ollama_url(server_url)
    if action == "start":
        job = jobs.submit("start", "ollama", "", "Starting Ollama",
                          lambda j: {"url": start_ollama_server(url, j.check, lambda p: j.progress(phase=p))})
        return {"job": job.public()}
    stop_ollama_server()
    return {"stopped": True}


def state(server_url: Optional[str]) -> Dict[str, Any]:
    url = ollama_url(server_url)
    version = ollama_version(url)
    obin = ollama_binary()
    settings = load_settings()
    lbin = llama_binary()

    llama = []
    for m in scan_llama():
        run = llama_running(m["id"])
        dead = procs.last_exit("llama:" + m["id"])
        llama.append({**m, "running": run is not None, "ready": bool(run and run.ready),
                      "alias": run.extra.get("alias") if run else None, "url": run.url if run else None,
                      "adopted": bool(run and run.adopted),
                      "crashed": dead.tail(15) if dead is not None and run is None else None})

    img = image_running()
    sd_bin = sd_server_binary()
    crashed_images = {}
    for model in images.list_models():
        dead = procs.last_exit("image:" + model["id"])
        if dead is not None and not (img and img.target == model["id"]):
            crashed_images[model["id"]] = dead.tail(15)

    return {
        "ollama": {"url": url, "running": version is not None, "version": version,
                   "binary": str(obin) if obin else None, "managed": procs.get("ollama") is not None,
                   "local": _is_local(url)},
        "llamacpp": {"binary": str(lbin) if lbin else None, "binary_setting": settings["llama_server"],
                     "ctx": settings["llama_ctx"], "args": settings["llama_args"], "root": str(llm_root()),
                     "models": llama},
        "image": {"sd_server": str(sd_bin) if sd_bin else None,
                  "sd_server_setting": images.load_settings().get("sd_server", ""), "root": str(image_root()),
                  "running": {"model": img.target, "url": img.url, "ready": img.ready, "adopted": img.adopted}
                  if img else None, "crashed": crashed_images},
        "engines": engines.status(ENGINE_FINDERS),
        "catalog": catalog.public(),
        "hf_token": bool(hub.token()),
        "jobs": jobs.listing(),
    }


ENGINE_FINDERS: Dict[str, Callable[[], Optional[Path]]] = {"llama": lambda: llama_binary(), "sd": lambda: _sd_found()}


def install_engine(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ManageError("Request body must be a JSON object.")
    engine = _text(body, "engine", "Engine")
    build = _text(body, "build", "Build") or "auto"
    job = engines.submit(engine, build)
    return {"job": job.public()}


def remove_engine(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise ManageError("Request body must be a JSON object.")
    engine = _text(body, "engine", "Engine")
    kind = {"llama": "llama", "sd": "image"}.get(engine)
    if kind and procs.running(kind):
        raise ManageError("Stop every model that uses it first.")
    return engines.remove(engine)


def auto_install_engines() -> List[jobs.Job]:
    """Called when Axis starts: install llama.cpp and stable-diffusion.cpp if they are missing."""
    return engines.auto_install(ENGINE_FINDERS)


def shutdown() -> None:
    """Stop every program Axis started (called when Axis exits).

    Models Axis loaded into Ollama are unloaded first. Axis loads them with keep_alive -1,
    so when Ollama runs on its own (desktop app, system service) they would otherwise stay
    in memory after Axis quits. Then llama-server, sd-server and any `ollama serve` that Axis
    started are stopped, together with the programs they started.
    """
    for url, name in backend.take_pinned_ollama():
        try:
            if ollama_version(url):
                _ollama_call(url, "/api/generate", {"model": name, "keep_alive": 0, "stream": False}, timeout=15)
        except Exception:
            pass  # Ollama is gone or busy; it frees the memory when it stops
    procs.stop_all()


backend.add_url_source(chat_urls)
