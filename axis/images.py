"""Image generation with stable-diffusion.cpp (``sd-cli`` and ``sd-server``).

Each image model lists the files it uses and its default settings. Models are
either installed from Hugging Face on the Models page (see manager.py) or
described by hand, pointing at files the user already has.

A picture is made in one of two ways:

* When the model is on (started on the Models page), Axis sends the request to
  the ``sd-server`` that holds it in memory, so the picture starts at once.
* Otherwise Axis runs ``sd-cli``, which loads the model, makes the picture and
  exits. If sd-cli is missing but sd-server is present, Axis starts the model.

Stored in ~/Axis:

    image-settings.json        where sd-cli is, the folder it runs in, the default model
    image-models/<id>.json     one file per image model

Every picture Axis makes, in the Images tab or with an agent's generate_image
action, goes to ~/Axis/images (paths.images_folder), never into the workspace.
The folder keeps a hidden index (.axis-images.json) with the prompt and settings
of every picture, for the gallery. Pictures made before Axis 4.3.4 stay where they
were saved (images/ in the shared folder).

Editing a picture (image to image): when a request names an init image, the same
model starts from that picture instead of from noise. strength (0 to 1) sets how far
it may move from it: low keeps the picture and changes details, high mostly follows
the prompt. sd-cli gets --init-img and --strength; sd-server gets init_image and
strength (or /sdapi/v1/img2img on older builds). Without a width and height the
result keeps the source's shape, at about the model's usual number of pixels.

Step previews: when asked, Axis shows the picture as it forms. stable-diffusion.cpp
makes each preview with its "proj" method, a cheap linear projection of the latent
to colours at 1/8 of the final size, so it costs almost nothing next to a sampling
step. With sd-cli, Axis passes --preview proj and reads the small file it writes
after each step; with sd-server it forwards the preview the job status carries.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .paths import axis_home, image_root, inside, shared_root

INDEX_NAME = ".axis-images.json"
IMAGES_DIR = "images"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TIMEOUT = float(os.environ.get("AXIS_IMAGE_TIMEOUT", "1800"))
MAX_PROMPT = 4000
MIN_SIZE, MAX_SIZE = 64, 2048
MAX_STEPS = 150
INIT_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_INIT_BYTES = 50 * 1024 * 1024
DEFAULT_STRENGTH = 0.6

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

    user_facing = True


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
    return {k: str(data.get(k) or "") for k in ("sd_cli", "sd_server", "workdir", "default_model")}


def save_settings(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ImageError("Settings must be a JSON object.")
    current = load_settings()
    for key, label in (("sd_cli", "sd-cli path"), ("sd_server", "sd-server path"), ("workdir", "Working folder"),
                       ("default_model", "Default model")):
        if key in data:
            current[key] = _text(data[key], label)
    if current["default_model"] and not ID_RE.fullmatch(current["default_model"]):
        raise ImageError("Unknown default model.")
    _write_json(_settings_path(), current)
    return status()


def find_cli() -> Optional[Path]:
    """sd-cli from the settings, AXIS_SD_CLI, the copy Axis installed, or the PATH."""
    from . import engines

    configured = load_settings()["sd_cli"] or os.environ.get("AXIS_SD_CLI", "")
    if configured:
        p = Path(configured).expanduser()
        if p.is_file():
            return p.resolve()
        found = shutil.which(configured)
        return Path(found).resolve() if found else None
    axis_copy = engines.program("sd-cli")
    if axis_copy is not None:
        return axis_copy
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
    from . import engines

    cli = cli or find_cli()
    if cli is None or inside(cli, engines.root()):  # Axis's own copy moves when it is updated
        return axis_home()
    return cli.parent.parent if cli.parent.name.lower() == "bin" else cli.parent


def status() -> Dict[str, Any]:
    from . import manager  # imported here: manager imports this module

    settings = load_settings()
    cli = find_cli()
    server = manager.sd_server_binary()
    models = list_models()
    default = default_model_id(models)
    running = manager.image_running()
    return {
        "ready": bool((cli or server) and models),
        "sd_cli": str(cli) if cli else None,
        "sd_cli_setting": settings["sd_cli"],
        "sd_server": str(server) if server else None,
        "sd_server_setting": settings["sd_server"],
        "running_model": running.target if running and running.ready else None,
        "workdir": str(workdir(cli)),
        "workdir_setting": settings["workdir"],
        "default_model": default,
        "busy": _LOCK.locked(),
        "samplers": list(SAMPLERS),
        "message": None if (cli or server) else (
            "stable-diffusion.cpp is not installed yet. Axis installs it with your first image model, or install it "
            "now under Image models on the Models page."),
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
                      "exists": size is not None, "size": size, "managed": inside(full, image_root())})
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
    from . import manager

    if manager.image_server_url(model_id):  # it holds the old files: stop it so the next start uses the new ones
        manager.image_stop(model_id)
    return _public(record)


def delete_model(model_id: str, files: str = "none") -> Dict[str, Any]:
    """Remove an image model. files: "none" keeps every file, "axis" deletes the files Axis downloaded,
    "all" also deletes files the user added. A file another image model uses is always kept."""
    if files not in ("none", "axis", "all"):
        raise ImageError("files must be none, axis or all.")
    path = _model_path(model_id)
    data = _read_json(path, None)
    if not path.is_file() or not isinstance(data, dict):
        raise ImageError(f"Image model '{model_id}' does not exist.")
    model = _public(data)
    others = [m for m in list_models() if m["id"] != model_id]
    used = set()
    for m in others:
        for f in m["files"]:
            try:
                used.add(str(Path(f["full_path"]).resolve()))
            except OSError:
                pass
    path.unlink()
    removed, kept = [], []
    for f in model["files"]:
        full = Path(f["full_path"])
        try:
            key = str(full.resolve())
        except OSError:
            key = str(full)
        wanted = files == "all" or (files == "axis" and f["managed"])
        if not wanted or key in used or not full.is_file():
            if f["exists"]:
                kept.append(f["path"])
            continue
        try:
            full.unlink()
            removed.append(f["path"])
        except OSError as exc:
            kept.append(f"{f['path']} (could not delete: {exc.strerror or exc})")
            continue
        if f["managed"]:
            from .hub import prune_empty

            prune_empty(full.parent, image_root())
    return {"deleted": model_id, "removed_files": removed, "kept_files": kept}


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


def build_command(cli: Path, model: Dict[str, Any], params: Dict[str, Any], out_file: Path, base: Path,
                  preview_file: Optional[Path] = None) -> List[str]:
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
    if params.get("init_image"):
        cmd += ["--init-img", str(params["init_image"]), "--strength", f"{params['strength']:g}"]
    if preview_file is not None:  # before extra_args, so a model's own preview settings still win
        cmd += ["--preview", "proj", "--preview-path", str(preview_file), "--preview-interval", "1"]
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


# ------------------------------------------------------------- image to image


def image_size(path: Path) -> Optional[tuple]:
    """(width, height) of a PNG, JPEG, WebP, GIF or BMP file, read from its header; None if unknown."""
    try:
        with path.open("rb") as f:
            head = f.read(64)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")
            if head[:2] == b"BM" and len(head) >= 26:
                return int.from_bytes(head[18:22], "little", signed=True), abs(int.from_bytes(head[22:26], "little", signed=True))
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                kind = head[12:16]
                if kind == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
                    return int.from_bytes(head[26:28], "little") & 0x3FFF, int.from_bytes(head[28:30], "little") & 0x3FFF
                if kind == b"VP8L" and head[20] == 0x2F:
                    b = int.from_bytes(head[21:25], "little")
                    return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
                if kind == b"VP8X":
                    return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
                return None
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:  # JPEG: walk the segments to the frame header (SOFn)
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    return None
                marker = b[0]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    continue
                length = int.from_bytes(f.read(2), "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    data = f.read(5)
                    return int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big")
                if length < 2:
                    return None
                f.seek(length - 2, 1)
    except (OSError, IndexError, ValueError):
        return None


def fit_size(src_w: int, src_h: int, area: int) -> tuple:
    """A width and height with the source's shape and about `area` pixels, in multiples of 64."""
    if src_w <= 0 or src_h <= 0:
        raise ImageError("The source picture has no size.")
    scale = (area / (src_w * src_h)) ** 0.5
    w, h = src_w * scale, src_h * scale
    big = max(w, h)
    if big > MAX_SIZE:
        w, h = w * MAX_SIZE / big, h * MAX_SIZE / big
    snap = lambda v: int(min(MAX_SIZE, max(MIN_SIZE, round(v / 64) * 64)))  # noqa: E731
    return snap(w), snap(h)


def check_init_image(path: Path) -> Path:
    """The picture to edit, checked: an existing image file Axis can send to stable-diffusion.cpp."""
    if not path.is_file():
        raise ImageError(f"The picture to edit ({path.name}) does not exist.")
    if path.suffix.lower() not in INIT_TYPES:
        raise ImageError(f"{path.name} cannot be edited. Use a PNG, JPEG, WebP or BMP picture.")
    if path.stat().st_size > MAX_INIT_BYTES:
        raise ImageError(f"{path.name} is larger than {MAX_INIT_BYTES // (1024 * 1024)} MB.")
    return path


def edit_params(model: Dict[str, Any], raw: Dict[str, Any], init_image: Path) -> Dict[str, Any]:
    """resolve_params for an edit: adds the strength and, when no size is given, keeps the source's shape."""
    raw = dict(raw)
    if raw.get("width") in (None, "") or raw.get("height") in (None, ""):
        size = image_size(init_image)
        if size:
            area = int(model.get("width") or 512) * int(model.get("height") or 512)
            raw["width"], raw["height"] = fit_size(size[0], size[1], area)
    params = resolve_params(model, raw)
    params["strength"] = _float(raw.get("strength"), "Strength", 0.01, 1.0, DEFAULT_STRENGTH)
    params["init_image"] = str(init_image)
    return params


def _image_b64(path: Path) -> str:
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".bmp": "image/bmp"}.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _file_name(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40].strip("-") or "image"
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{words}"


PROGRESS = re.compile(r"\|\s*(\d+)/(\d+)")
PHASES = (("loading", "Loading the model"), ("sampling", "Sampling"), ("decod", "Decoding the image"),
          ("generating image", "Generating"))

_LOCK = threading.Lock()  # one picture at a time: they compete for the same GPU memory


def generate(raw: Dict[str, Any], folder: Path, source: str = "",
             on_event: Optional[Callable[[Dict[str, Any]], None]] = None, preview: bool = False,
             init_image: Optional[Path] = None, init_name: str = "") -> Dict[str, Any]:
    """Make one picture in folder (normally paths.images_folder()). Returns its index record (with "path",
    the file's name inside folder).

    With init_image the picture is an edit of that file (image to image); init_name is
    how the source is shown in the gallery (for example "shared:uploads/cat.png").

    on_event receives {"queued": True}, {"phase": text}, {"progress": {"step", "steps"}}
    and {"log": line}; with preview=True also {"preview": {"step", "steps", "image"}},
    where image is a data: URL of the picture so far. If on_event raises, the run is
    stopped and the exception passes on.
    """
    from . import manager  # imported here: manager imports this module

    emit = on_event or (lambda e: None)
    model = find_model(raw.get("model"))
    if model["missing"]:
        raise ImageError(f"Files for {model['name']} are missing: {', '.join(model['missing'])}. "
                         "Check the paths on the Models page.")
    if init_image is not None:
        params = edit_params(model, raw, check_init_image(init_image))
    else:
        params = resolve_params(model, raw)
    cli = find_cli()
    if cli is None and not manager.image_server_url(model["id"]) and manager.sd_server_binary() is None:
        raise ImageError("stable-diffusion.cpp is not installed. Install it on the Models page under Image models.")

    while not _LOCK.acquire(timeout=1.0):
        emit({"queued": True})  # also notices a browser that has gone away
    try:
        out_dir = folder
        out_dir.mkdir(parents=True, exist_ok=True)
        name = _file_name(params["prompt"])
        out_file, n = out_dir / f"{name}.png", 2
        while out_file.exists():
            out_file, n = out_dir / f"{name}-{n}.png", n + 1
        started = time.time()
        url = manager.image_server_url(model["id"])
        if url is None and cli is None:
            # No sd-cli, but sd-server is there: turn the model on, then use it.
            beat = [0.0]

            def check() -> None:
                if time.time() - beat[0] >= 1.0:  # raises when the browser has gone away
                    beat[0] = time.time()
                    emit({"log": "Loading the model"})
            try:
                url = manager.image_start(model["id"], check, lambda p: emit({"phase": p}))
            except (manager.ManageError, manager.procs.ProcessError) as exc:
                raise ImageError(str(exc)) from exc
            started = time.time()
        preview_dir = None
        try:
            if url:
                _generate_with_server(url, params, out_file, emit, preview)
            else:
                assert cli is not None
                base = workdir(cli)
                cwd = base if base.is_dir() else None
                preview_file = None
                if preview:
                    preview_dir = Path(tempfile.mkdtemp(prefix="axis-preview-"))
                    preview_file = preview_dir / "preview.png"
                emit({"phase": "Starting sd-cli"})
                try:
                    lines = _run(build_command(cli, model, params, out_file, base, preview_file), cwd,
                                 params["steps"], emit, preview_file)
                except ImageError as exc:
                    if preview_file is None or "--preview" not in str(exc) or "unknown argument" not in str(exc).lower():
                        raise
                    # An older sd-cli without step previews: make the picture without them.
                    emit({"phase": "This sd-cli cannot show steps; generating without them"})
                    lines = _run(build_command(cli, model, params, out_file, base), cwd, params["steps"], emit)
                if not out_file.is_file():
                    tail = "\n".join(l for l in lines[-8:] if l.strip())
                    raise ImageError("sd-cli finished without making a picture." + (f"\n{tail}" if tail else ""))
        except BaseException:
            if out_file.exists():  # never leave half a picture behind
                out_file.unlink()
            raise
        finally:
            if preview_dir is not None:
                shutil.rmtree(preview_dir, ignore_errors=True)
        seconds = round(time.time() - started, 1)
    finally:
        _LOCK.release()

    shown = {k: v for k, v in params.items() if k != "init_image"}   # the full path stays private
    if init_image is not None:
        shown["edited_from"] = init_name or init_image.name
    record = {"file": out_file.name, "path": out_file.name, "model": model["name"],
              "model_id": model["id"], **shown, "seconds": seconds, "source": source,
              "created": datetime.now().isoformat(timespec="seconds")}
    _add_to_index(out_dir, record)
    return record


# ------------------------------------------------------- through sd-server

_SERVER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
POLL = 0.5
POLL_PREVIEW = 0.2  # more often while previews are shown, so fast steps are not skipped


def _data_url(b64: str) -> str:
    """A data: URL for a base64 PNG or JPEG (sd-server sends both)."""
    if b64.startswith("data:"):
        return b64
    return ("data:image/jpeg;base64," if b64.startswith("/9j/") else "data:image/png;base64,") + b64


def _server_json(url: str, body: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with _SERVER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")


def _server_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            err = err.get("message")
        return str(err or exc.reason)
    except Exception:
        return str(exc.reason)


def _save_png(b64: Any, out_file: Path) -> None:
    if not isinstance(b64, str) or not b64:
        raise ImageError("sd-server finished without returning a picture.")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    try:
        data = base64.b64decode(b64, validate=False)
    except (ValueError, TypeError) as exc:
        raise ImageError("sd-server returned a picture Axis could not read.") from exc
    if not data.startswith(b"\x89PNG"):
        raise ImageError("sd-server returned something that is not a PNG picture.")
    tmp = out_file.with_name(out_file.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out_file)


def _generate_with_server(url: str, params: Dict[str, Any], out_file: Path, emit, preview: bool = False) -> None:
    """Make a picture with sd-server's job API (/sdcpp/v1), or its WebUI API on older builds.

    The job API reports the current step only with a preview, so "proj" previews are always
    asked for (they are cheap); preview decides whether they are passed on.
    """
    sample: Dict[str, Any] = {"sample_steps": params["steps"], "guidance": {"txt_cfg": params["cfg_scale"]}}
    if params.get("sampler"):
        sample["sample_method"] = params["sampler"]
    if params.get("scheduler"):
        sample["scheduler"] = params["scheduler"]
    body = {"prompt": params["prompt"], "negative_prompt": params.get("negative_prompt") or "",
            "width": params["width"], "height": params["height"], "seed": params["seed"], "batch_count": 1,
            "sample_params": sample, "preview": "proj", "preview_interval": 1, "output_format": "png"}
    if params.get("init_image"):
        body["init_image"] = _image_b64(Path(params["init_image"]))
        body["strength"] = params["strength"]
    emit({"phase": "Sending the request to sd-server"})
    deadline = time.time() + TIMEOUT
    job = None
    while job is None:
        try:
            job = _server_json(url + "/sdcpp/v1/img_gen", body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return _generate_with_sdapi(url, params, out_file, emit, preview)
            if exc.code in (429, 503) and time.time() < deadline:  # busy, or still loading
                emit({"phase": "Waiting for sd-server"})
                time.sleep(1.0)
                continue
            raise ImageError(f"sd-server refused the request: {_server_error(exc)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ImageError(f"sd-server stopped answering ({getattr(exc, 'reason', exc)}). "
                             "Start the model again on the Models page.") from exc
    job_id = job.get("id") if isinstance(job, dict) else None
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id):
        raise ImageError("sd-server did not return a job id.")
    poll = url + "/sdcpp/v1/jobs/" + job_id
    phase = ""
    last_step = None
    try:
        while True:
            if time.time() > deadline:
                raise ImageError(f"Stopped: the picture took longer than {int(TIMEOUT)} seconds.")
            try:
                st = _server_json(poll, timeout=30)
            except urllib.error.HTTPError as exc:
                raise ImageError(f"sd-server lost the job ({exc.code}).") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ImageError(f"sd-server stopped answering ({getattr(exc, 'reason', exc)}).") from exc
            status = st.get("status") if isinstance(st, dict) else None
            if status == "completed":
                images_ = ((st.get("result") or {}).get("images") or []) if isinstance(st.get("result"), dict) else []
                _save_png(images_[0].get("b64_json") if images_ and isinstance(images_[0], dict) else None, out_file)
                return
            if status in ("failed", "cancelled"):
                err = st.get("error") if isinstance(st.get("error"), dict) else {}
                raise ImageError(f"sd-server could not make the picture: {err.get('message') or status}.")
            if status == "queued":
                emit({"queued": True})
            elif status == "generating":
                prev = st.get("preview") if isinstance(st.get("preview"), dict) else None
                if prev and isinstance(prev.get("step"), int) and isinstance(prev.get("total_steps"), int):
                    if prev["step"] != last_step:
                        last_step = prev["step"]
                        emit({"progress": {"step": prev["step"], "steps": prev["total_steps"]}})
                        if preview and isinstance(prev.get("b64_json"), str) and prev["b64_json"]:
                            emit({"preview": {"step": prev["step"], "steps": prev["total_steps"],
                                              "image": _data_url(prev["b64_json"])}})
                        if phase != "Sampling":
                            phase = "Sampling"
                            emit({"phase": phase})
                    if prev["step"] >= prev["total_steps"] and phase != "Decoding the image":
                        phase = "Decoding the image"
                        emit({"phase": phase})
                elif phase != "Generating":
                    phase = "Generating"
                    emit({"phase": phase})
                else:
                    emit({"log": "working"})  # keeps noticing a browser that has gone away
            time.sleep(POLL_PREVIEW if preview else POLL)
    except BaseException:
        try:
            _server_json(poll + "/cancel", {}, timeout=5)  # stop the work, not just the waiting
        except Exception:
            pass
        raise


def _generate_with_sdapi(url: str, params: Dict[str, Any], out_file: Path, emit, preview: bool = False) -> None:
    body = {"prompt": params["prompt"], "negative_prompt": params.get("negative_prompt") or "",
            "width": params["width"], "height": params["height"], "steps": params["steps"],
            "cfg_scale": params["cfg_scale"], "seed": params["seed"], "batch_size": 1}
    if params.get("sampler"):
        body["sampler_name"] = params["sampler"]
    if params.get("scheduler"):
        body["scheduler"] = params["scheduler"]
    route = "/sdapi/v1/txt2img"
    if params.get("init_image"):
        body["init_images"] = [_image_b64(Path(params["init_image"]))]
        body["denoising_strength"] = params["strength"]
        route = "/sdapi/v1/img2img"
    result: Dict[str, Any] = {}

    def call() -> None:
        try:
            result["data"] = _server_json(url + route, body, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            result["error"] = f"sd-server could not make the picture: {_server_error(exc)}"
        except Exception as exc:
            result["error"] = f"sd-server stopped answering ({getattr(exc, 'reason', exc)})."

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    emit({"phase": "Generating"})
    last_step = None
    query = "false" if preview else "true"
    while worker.is_alive():
        worker.join(POLL_PREVIEW if preview else POLL)
        try:
            prog = _server_json(url + "/sdapi/v1/progress?skip_current_image=" + query, timeout=5)
            state = prog.get("state") if isinstance(prog, dict) else None
            if isinstance(state, dict) and state.get("sampling_steps"):
                step, steps = int(state.get("sampling_step") or 0), int(state["sampling_steps"])
                emit({"progress": {"step": step, "steps": steps}})
                image = prog.get("current_image")
                if preview and step != last_step and isinstance(image, str) and image:
                    last_step = step
                    emit({"preview": {"step": step, "steps": steps, "image": _data_url(image)}})
            else:
                emit({"log": "working"})
        except (urllib.error.URLError, OSError, ValueError, TypeError):
            emit({"log": "working"})
    if "error" in result:
        raise ImageError(result["error"])
    imgs = (result.get("data") or {}).get("images") if isinstance(result.get("data"), dict) else None
    _save_png(imgs[0] if imgs else None, out_file)


PNG_END = b"IEND\xaeB`\x82"


class _PreviewFile:
    """Watches the preview file sd-cli rewrites after each step and passes on each new picture."""

    def __init__(self, path: Path, steps: int, emit):
        self.path, self.steps, self.emit = path, steps, emit
        self.last: Optional[bytes] = None

    def check(self, step: int) -> None:
        # Compare the picture itself, not the file's modified time and size: a step can rewrite the file
        # within one timestamp tick (Windows rounds to ~15 ms) and a same-sized picture would be skipped.
        try:
            data = self.path.read_bytes()
        except OSError:
            return
        if not (data.startswith(b"\x89PNG") and data.rstrip(b"\0").endswith(PNG_END)):
            return  # still being written: try again on the next line
        if data == self.last:
            return
        self.last = data
        image = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        self.emit({"preview": {"step": step, "steps": self.steps, "image": image}})


def _run(cmd: List[str], cwd: Optional[Path], steps: int, emit, preview_file: Optional[Path] = None) -> List[str]:
    from . import engines

    kwargs: Dict[str, Any] = {"env": {**os.environ, **engines.run_env(Path(cmd[0]))}}
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
    buf, phase, step = b"", "", 0
    watcher = _PreviewFile(preview_file, steps, emit) if preview_file is not None else None
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
                    step = int(m.group(1))
                    emit({"progress": {"step": step, "steps": steps}})
                else:
                    emit({"log": line[:300]})
                if watcher is not None and step:
                    watcher.check(step)
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        timer.cancel()
        if proc.stdout is not None:
            proc.stdout.close()   # an open pipe keeps its handle (and, on Windows, the files it touched) locked
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
    """Pictures in folder (normally paths.images_folder()), newest first, with their prompts where known."""
    out_dir = folder
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
        info.update({"file": f.name, "path": f.name, "mtime": mtime})
        info.setdefault("created", datetime.fromtimestamp(mtime).isoformat(timespec="seconds"))
        result.append(info)
    result.sort(key=lambda i: i["mtime"], reverse=True)
    return result[:limit]


def shared_folder() -> Path:
    root = shared_root()
    root.mkdir(parents=True, exist_ok=True)
    return root
