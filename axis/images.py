"""Image generation with stable-diffusion.cpp (``sd-cli``).

Axis does not download or install image models. The user points Axis at their
``sd-cli`` program and describes each image model once (which files it uses and
its default settings). Axis then runs ``sd-cli`` for each picture.

Stored in ~/Axis:

    image-settings.json        where sd-cli is, the folder it runs in, the default model
    image-models/<id>.json     one file per image model

Pictures made in the Images tab go to ~/Axis/shared/images. Pictures an agent
makes with the generate_image action go to images/ in that agent's own folder.
Each images/ folder keeps a hidden index (.axis-images.json) with the prompt and
settings of every picture, for the gallery.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .paths import axis_home, shared_root

INDEX_NAME = ".axis-images.json"
IMAGES_DIR = "images"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TIMEOUT = float(os.environ.get("AXIS_IMAGE_TIMEOUT", "1800"))
MAX_PROMPT = 4000
MIN_SIZE, MAX_SIZE = 64, 2048
MAX_STEPS = 150

FILE_FIELDS = ("model", "diffusion_model", "llm", "clip_l", "t5xxl", "vae")
FILE_FLAGS = {
    "model": "--model", "diffusion_model": "--diffusion-model", "llm": "--llm",
    "clip_l": "--clip_l", "t5xxl": "--t5xxl", "vae": "--vae",
}
FILE_LABELS = {
    "model": "Checkpoint", "diffusion_model": "Diffusion model", "llm": "Text encoder (LLM)",
    "clip_l": "CLIP-L", "t5xxl": "T5-XXL", "vae": "VAE",
}
SAMPLERS = ("euler", "euler_a", "heun", "dpm2", "dpm++2s_a", "dpm++2m", "dpm++2mv2", "ipndm", "ipndm_v",
            "lcm", "ddim_trailing", "tcd", "res_multistep", "res_2s", "er_sde", "dpm++2m_sde", "lms")


class ImageError(Exception):
    """A problem the user must fix (missing sd-cli, bad settings, failed run)."""


class Cancelled(ImageError):
    pass


# ------------------------------------------------------------------ settings


def _settings_path() -> Path:
    return axis_home() / "image-settings.json"


def models_root() -> Path:
    return axis_home() / "image-models"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _text(value: Any, label: str, limit: int = 1000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ImageError(f"{label} must be text.")
    value = value.strip().strip('"').strip()
    if len(value) > limit:
        raise ImageError(f"{label} is too long (limit {limit} characters).")
    return value


def load_settings() -> Dict[str, str]:
    data = _read_json(_settings_path(), {})
    data = data if isinstance(data, dict) else {}
    return {k: str(data.get(k) or "") for k in ("sd_cli", "workdir", "default_model")}


def save_settings(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ImageError("Settings must be a JSON object.")
    current = load_settings()
    for key, label in (("sd_cli", "sd-cli path"), ("workdir", "Working folder"), ("default_model", "Default model")):
        if key in data:
            current[key] = _text(data[key], label)
    if current["default_model"] and not ID_RE.fullmatch(current["default_model"]):
        raise ImageError("Unknown default model.")
    _write_json(_settings_path(), current)
    return status()


def find_cli() -> Optional[Path]:
    """sd-cli from the settings, AXIS_SD_CLI, or the PATH."""
    configured = load_settings()["sd_cli"] or os.environ.get("AXIS_SD_CLI", "")
    if configured:
        p = Path(configured).expanduser()
        if p.is_file():
            return p.resolve()
        found = shutil.which(configured)
        return Path(found).resolve() if found else None
    for name in ("sd-cli", "sd"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def workdir(cli: Optional[Path] = None) -> Path:
    """The folder sd-cli runs in; relative model paths are resolved from here.

    Defaults to the folder above bin/ when sd-cli is in a bin folder (the layout of
    the stable-diffusion.cpp release ZIPs), otherwise sd-cli's own folder.
    """
    configured = load_settings()["workdir"]
    if configured:
        return Path(configured).expanduser()
    cli = cli or find_cli()
    if cli is None:
        return axis_home()
    return cli.parent.parent if cli.parent.name.lower() == "bin" else cli.parent


def status() -> Dict[str, Any]:
    settings = load_settings()
    cli = find_cli()
    models = list_models()
    default = default_model_id(models)
    return {
        "ready": bool(cli and models),
        "sd_cli": str(cli) if cli else None,
        "sd_cli_setting": settings["sd_cli"],
        "workdir": str(workdir(cli)),
        "workdir_setting": settings["workdir"],
        "default_model": default,
        "busy": _LOCK.locked(),
        "samplers": list(SAMPLERS),
        "message": None if cli else (
            "sd-cli was not found. Set its full path below (for example C:\\sd\\bin\\sd-cli.exe), "
            "or set AXIS_SD_CLI."),
        "models": models,
    }


# -------------------------------------------------------------------- models


def _model_path(model_id: str) -> Path:
    if not ID_RE.fullmatch(model_id or ""):
        raise ImageError(f"Image model '{model_id}' does not exist.")
    return models_root() / f"{model_id}.json"


def _resolve_file(rel: str, base: Path) -> Path:
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (base / p)


def _public(data: Dict[str, Any], base: Optional[Path] = None) -> Dict[str, Any]:
    base = base or workdir()
    files = []
    for key in FILE_FIELDS:
        rel = data.get(key) or ""
        if not rel:
            continue
        full = _resolve_file(rel, base)
        try:
            size = full.stat().st_size if full.is_file() else None
        except OSError:
            size = None
        files.append({"role": key, "label": FILE_LABELS[key], "path": rel, "full_path": str(full),
                      "exists": size is not None, "size": size})
    out = {k: data.get(k) for k in ("id", "name", "description", "width", "height", "steps", "cfg_scale",
                                     "sampler", "scheduler", "extra_args", "created")}
    out.update({k: data.get(k) or "" for k in FILE_FIELDS})
    out["files"] = files
    out["missing"] = [f["path"] for f in files if not f["exists"]]
    out["disk_size"] = sum(f["size"] or 0 for f in files) or None
    return out


def _int(value: Any, label: str, lo: int, hi: int, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise ImageError(f"{label} must be a number.")
    if not lo <= n <= hi:
        raise ImageError(f"{label} must be between {lo} and {hi}.")
    return n


def _float(value: Any, label: str, lo: float, hi: float, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ImageError(f"{label} must be a number.")
    if not lo <= n <= hi:
        raise ImageError(f"{label} must be between {lo:g} and {hi:g}.")
    return n


def _size(value: Any, label: str, default: int) -> int:
    n = _int(value, label, MIN_SIZE, MAX_SIZE, default)
    return max(MIN_SIZE, n - n % 8)  # the image models need multiples of 8 (64 is safest)


def _sampler(value: Any) -> str:
    value = _text(value, "Sampler", 40)
    if value and value not in SAMPLERS:
        raise ImageError(f"Unknown sampler '{value}'. Use one of: {', '.join(SAMPLERS)}.")
    return value


def _fields(data: Dict[str, Any]) -> Dict[str, Any]:
    name = _text(data.get("name"), "Name", 80)
    if not name:
        raise ImageError("Give the image model a name.")
    out: Dict[str, Any] = {"name": name, "description": _text(data.get("description"), "Description", 300)}
    for key in FILE_FIELDS:
        out[key] = _text(data.get(key), FILE_LABELS[key])
    if not out["model"] and not out["diffusion_model"]:
        raise ImageError("Give either a checkpoint file (SD 1.5 / SDXL) or a diffusion model file (Flux, Z-Image, SD3, ...).")
    out["width"] = _size(data.get("width"), "Width", 512)
    out["height"] = _size(data.get("height"), "Height", 512)
    out["steps"] = _int(data.get("steps"), "Steps", 1, MAX_STEPS, 20)
    out["cfg_scale"] = _float(data.get("cfg_scale"), "CFG scale", 0, 30, 7.0)
    out["sampler"] = _sampler(data.get("sampler"))
    out["scheduler"] = _text(data.get("scheduler"), "Scheduler", 40)
    out["extra_args"] = _text(data.get("extra_args"), "Extra arguments", 2000)
    return out


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "model"
    return slug if slug[0].isalnum() else "m" + slug


def list_models() -> List[Dict[str, Any]]:
    root = models_root()
    if not root.is_dir():
        return []
    base = workdir()
    out = []
    for f in root.glob("*.json"):
        data = _read_json(f, None)
        if isinstance(data, dict) and data.get("id") == f.stem:
            out.append(_public(data, base))
    return sorted(out, key=lambda m: m["name"].lower())


def get_model(model_id: str) -> Dict[str, Any]:
    data = _read_json(_model_path(model_id), None)
    if not isinstance(data, dict):
        raise ImageError(f"Image model '{model_id}' does not exist.")
    return _public(data)


def find_model(name_or_id: Optional[str]) -> Dict[str, Any]:
    """An image model by id or (case-insensitive) name; the default when empty."""
    models = list_models()
    if not models:
        raise ImageError("No image models are set up. Add one in Axis on the Models page (Image models).")
    if not name_or_id:
        default = default_model_id(models)
        return next(m for m in models if m["id"] == default)
    key = name_or_id.strip().lower()
    for m in models:
        if m["id"] == key or m["name"].lower() == key:
            return m
    names = ", ".join(m["name"] for m in models)
    raise ImageError(f"Unknown image model '{name_or_id}'. Available: {names}.")


def default_model_id(models: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    models = list_models() if models is None else models
    wanted = load_settings()["default_model"]
    if any(m["id"] == wanted for m in models):
        return wanted
    return models[0]["id"] if models else None


def create_model(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ImageError("Request body must be a JSON object.")
    fields = _fields(data)
    existing = list_models()
    if any(m["name"].lower() == fields["name"].lower() for m in existing):
        raise ImageError(f"An image model named '{fields['name']}' already exists.")
    taken = {m["id"] for m in existing}
    base = _slug(fields["name"])
    model_id, n = base, 2
    while model_id in taken:
        model_id, n = f"{base}-{n}", n + 1
    record = {"id": model_id, **fields, "created": datetime.now().isoformat(timespec="seconds")}
    _write_json(_model_path(model_id), record)
    return _public(record)


def update_model(model_id: str, data: Any) -> Dict[str, Any]:
    path = _model_path(model_id)
    current = _read_json(path, None)
    if not isinstance(current, dict):
        raise ImageError(f"Image model '{model_id}' does not exist.")
    fields = _fields({**current, **(data if isinstance(data, dict) else {})})
    if any(m["id"] != model_id and m["name"].lower() == fields["name"].lower() for m in list_models()):
        raise ImageError(f"An image model named '{fields['name']}' already exists.")
    record = {"id": model_id, **fields, "created": current.get("created", "")}
    _write_json(path, record)
    return _public(record)


def delete_model(model_id: str) -> None:
    path = _model_path(model_id)
    if not path.is_file():
        raise ImageError(f"Image model '{model_id}' does not exist.")
    path.unlink()


# ------------------------------------------------------------------- running


def split_args(text: str) -> List[str]:
    """Split extra arguments on spaces, keeping "double quoted" parts together.

    Backslashes are left alone so Windows paths survive.
    """
    out, cur, quoted, has = [], [], False, False
    for ch in text or "":
        if ch == '"':
            quoted, has = not quoted, True
        elif ch.isspace() and not quoted:
            if cur or has:
                out.append("".join(cur))
            cur, has = [], False
        else:
            cur.append(ch)
    if cur or has:
        out.append("".join(cur))
    return out


def build_command(cli: Path, model: Dict[str, Any], params: Dict[str, Any], out_file: Path, base: Path) -> List[str]:
    cmd = [str(cli)]
    for key in FILE_FIELDS:
        rel = model.get(key) or ""
        if rel:
            cmd += [FILE_FLAGS[key], str(_resolve_file(rel, base))]
    cmd += ["--prompt", params["prompt"]]
    if params.get("negative_prompt"):
        cmd += ["--negative-prompt", params["negative_prompt"]]
    cmd += ["--width", str(params["width"]), "--height", str(params["height"]),
            "--steps", str(params["steps"]), "--cfg-scale", f"{params['cfg_scale']:g}",
            "--seed", str(params["seed"])]
    if params.get("sampler"):
        cmd += ["--sampling-method", params["sampler"]]
    if model.get("scheduler"):
        cmd += ["--scheduler", model["scheduler"]]
    cmd += split_args(model.get("extra_args") or "")
    cmd += ["--output", str(out_file)]
    return cmd


def resolve_params(model: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    prompt = _text(raw.get("prompt"), "Prompt", MAX_PROMPT)
    if not prompt:
        raise ImageError("Write a prompt describing the picture.")
    seed = raw.get("seed")
    seed = -1 if seed in (None, "") else _int(seed, "Seed", -1, 2**31 - 1, -1)
    if seed < 0:
        seed = random.randint(0, 2**31 - 1)  # chosen here so it can be shown and reused
    return {
        "prompt": prompt,
        "negative_prompt": _text(raw.get("negative_prompt"), "Negative prompt", MAX_PROMPT),
        "width": _size(raw.get("width"), "Width", int(model.get("width") or 512)),
        "height": _size(raw.get("height"), "Height", int(model.get("height") or 512)),
        "steps": _int(raw.get("steps"), "Steps", 1, MAX_STEPS, int(model.get("steps") or 20)),
        "cfg_scale": _float(raw.get("cfg_scale"), "CFG scale", 0, 30, float(model.get("cfg_scale") or 7.0)),
        "sampler": _sampler(raw.get("sampler")) or (model.get("sampler") or ""),
        "seed": seed,
    }


def _file_name(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40].strip("-") or "image"
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{words}"


PROGRESS = re.compile(r"\|\s*(\d+)/(\d+)")
PHASES = (("loading", "Loading the model"), ("sampling", "Sampling"), ("decod", "Decoding the image"),
          ("generating image", "Generating"))

_LOCK = threading.Lock()  # one picture at a time: they compete for the same GPU memory


def generate(raw: Dict[str, Any], folder: Path, source: str = "",
             on_event: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Make one picture in folder/images. Returns its index record (with "path").

    on_event receives {"queued": True}, {"phase": text}, {"progress": {"step", "steps"}}
    and {"log": line}. If it raises, the run is stopped and the exception passes on.
    """
    emit = on_event or (lambda e: None)
    cli = find_cli()
    if cli is None:
        raise ImageError("sd-cli was not found. Set its path on the Models page under Image models.")
    model = find_model(raw.get("model"))
    if model["missing"]:
        raise ImageError(f"Files for {model['name']} are missing: {', '.join(model['missing'])}. "
                         "Check the paths on the Models page.")
    params = resolve_params(model, raw)
    base = workdir(cli)

    while not _LOCK.acquire(timeout=1.0):
        emit({"queued": True})  # also notices a browser that has gone away
    try:
        out_dir = folder / IMAGES_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        name = _file_name(params["prompt"])
        out_file, n = out_dir / f"{name}.png", 2
        while out_file.exists():
            out_file, n = out_dir / f"{name}-{n}.png", n + 1
        cmd = build_command(cli, model, params, out_file, base)
        emit({"phase": "Starting sd-cli"})
        started = time.time()
        try:
            lines = _run(cmd, base if base.is_dir() else None, params["steps"], emit)
        except BaseException:
            if out_file.exists():  # never leave half a picture behind
                out_file.unlink()
            raise
        seconds = round(time.time() - started, 1)
        if not out_file.is_file():
            tail = "\n".join(l for l in lines[-8:] if l.strip())
            raise ImageError("sd-cli finished without making a picture." + (f"\n{tail}" if tail else ""))
    finally:
        _LOCK.release()

    record = {"file": out_file.name, "path": f"{IMAGES_DIR}/{out_file.name}", "model": model["name"],
              "model_id": model["id"], **params, "seconds": seconds, "source": source,
              "created": datetime.now().isoformat(timespec="seconds")}
    _add_to_index(out_dir, record)
    return record


def _run(cmd: List[str], cwd: Optional[Path], steps: int, emit) -> List[str]:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
    except OSError as exc:
        raise ImageError(f"Could not start sd-cli ({cmd[0]}): {exc.strerror or exc}") from exc
    expired = threading.Event()

    def stop() -> None:
        expired.set()
        proc.kill()

    timer = threading.Timer(TIMEOUT, stop)
    timer.daemon = True
    timer.start()
    lines: List[str] = []
    buf, phase = b"", ""
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                lines.append(line)
                del lines[:-200]
                low = line.lower()
                for key, label in PHASES:
                    if key in low and label != phase:
                        phase = label
                        emit({"phase": label})
                        break
                m = PROGRESS.search(line)
                if m and int(m.group(2)) == steps:  # loading bars use other totals
                    emit({"progress": {"step": int(m.group(1)), "steps": steps}})
                else:
                    emit({"log": line[:300]})
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        timer.cancel()
    if code != 0:
        if expired.is_set():
            raise ImageError(f"Stopped: the picture took longer than {int(TIMEOUT)} seconds.")
        tail = "\n".join(lines[-8:])
        raise ImageError(f"sd-cli failed (exit code {code})." + (f"\n{tail}" if tail else ""))
    return lines


# --------------------------------------------------------------------- index

_INDEX_LOCK = threading.Lock()


def _add_to_index(out_dir: Path, record: Dict[str, Any]) -> None:
    with _INDEX_LOCK:
        path = out_dir / INDEX_NAME
        items = _read_json(path, [])
        items = items if isinstance(items, list) else []
        items.append({k: v for k, v in record.items() if k != "path"})
        _write_json(path, items[-5000:])


def gallery(folder: Path, limit: int = 500) -> List[Dict[str, Any]]:
    """Pictures in folder/images, newest first, with their prompts where known."""
    out_dir = folder / IMAGES_DIR
    if not out_dir.is_dir():
        return []
    items = _read_json(out_dir / INDEX_NAME, [])
    known = {i.get("file"): i for i in (items if isinstance(items, list) else []) if isinstance(i, dict)}
    result = []
    for f in out_dir.iterdir():
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not f.is_file():
            continue
        info = dict(known.get(f.name) or {})
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        info.update({"file": f.name, "path": f"{IMAGES_DIR}/{f.name}", "mtime": mtime})
        info.setdefault("created", datetime.fromtimestamp(mtime).isoformat(timespec="seconds"))
        result.append(info)
    result.sort(key=lambda i: i["mtime"], reverse=True)
    return result[:limit]


def shared_folder() -> Path:
    root = shared_root()
    root.mkdir(parents=True, exist_ok=True)
    return root
