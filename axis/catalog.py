"""Short model names, so models install by name the way Ollama's do.

Image models (stable-diffusion.cpp)
    sd1.5, sdxl, sdxl-turbo, z-image-turbo, flux1-schnell
    A tag after a colon picks the quantization of the main file where there is a
    choice: z-image-turbo:q8, flux1-schnell:q8.

    Each name lists every file the model needs (checkpoint or diffusion model,
    VAE, text encoders) on Hugging Face, and the settings it works best with. One
    install downloads them all and sets the model up.

Chat models (llama.cpp)
    llama3.2:1b, llama3.2:3b, llama3.1:8b, qwen3:4b, qwen3:8b, gemma3:4b (reads pictures)
    These map to GGUF repositories on Hugging Face, so the names typed for Ollama
    also work with llama.cpp.

Anything else is treated as a Hugging Face name (owner/name), as before.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

ZIMAGE_VAE = {"role": "vae", "repo": "Comfy-Org/z_image_turbo", "files": ["split_files/vae/ae.safetensors"]}
SDXL_VAE = {"role": "vae", "repo": "madebyollin/sdxl-vae-fp16-fix", "files": ["sdxl_vae.safetensors"]}

IMAGE: List[Dict[str, Any]] = [
    {
        "name": "sd1.5", "aliases": ["sd15", "sd-1.5", "sd-v1-5", "stable-diffusion-1.5", "stable-diffusion-v1-5"],
        "title": "Stable Diffusion 1.5", "size": "2.1 GB",
        "description": "The classic 512 x 512 model. Small and quick; runs on almost any computer.",
        "parts": [{"role": "checkpoint", "repo": "stable-diffusion-v1-5/stable-diffusion-v1-5",
                   "files": ["v1-5-pruned-emaonly-fp16.safetensors", "v1-5-pruned-emaonly.safetensors"]}],
        "settings": {"width": 512, "height": 512, "steps": 20, "cfg_scale": 7.0, "sampler": "euler_a"},
    },
    {
        "name": "sdxl", "aliases": ["sdxl-base", "sdxl1.0", "stable-diffusion-xl"],
        "title": "Stable Diffusion XL 1.0", "size": "7.3 GB",
        "description": "Detailed 1024 x 1024 pictures. Needs about 8 GB of memory.",
        "parts": [{"role": "checkpoint", "repo": "stabilityai/stable-diffusion-xl-base-1.0",
                   "files": ["sd_xl_base_1.0.safetensors"]}, SDXL_VAE],
        "settings": {"width": 1024, "height": 1024, "steps": 25, "cfg_scale": 7.0, "sampler": "euler_a"},
    },
    {
        "name": "sdxl-turbo", "aliases": ["sdxlturbo", "sd-xl-turbo"],
        "title": "SDXL Turbo", "size": "7.3 GB",
        "description": "512 x 512 pictures in 1 to 4 steps. The fastest of these models.",
        "parts": [{"role": "checkpoint", "repo": "stabilityai/sdxl-turbo",
                   "files": ["sd_xl_turbo_1.0_fp16.safetensors", "sd_xl_turbo_1.0.safetensors"]}, SDXL_VAE],
        "settings": {"width": 512, "height": 512, "steps": 4, "cfg_scale": 1.0, "sampler": "euler_a"},
    },
    {
        "name": "z-image-turbo", "aliases": ["z-image", "zimage", "zimage-turbo", "z_image_turbo"],
        "title": "Z-Image Turbo", "size": "6.5 GB",
        "description": "High quality in 8 steps; follows long prompts and can draw text. Runs on 4 GB GPUs.",
        "tags": {"q3": "Q3_K", "q4": "Q4_0", "q8": "Q8_0"}, "default_tag": "q4",
        "parts": [{"role": "diffusion_model", "repo": "leejet/Z-Image-Turbo-GGUF", "files": ["z_image_turbo-{TAG}.gguf"],
                   "tagged": True},
                  {"role": "llm", "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
                   "files": ["Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "Qwen3-4B-Instruct-2507-Q8_0.gguf"]},
                  ZIMAGE_VAE],
        "settings": {"width": 1024, "height": 1024, "steps": 8, "cfg_scale": 1.0,
                     "extra_args": "--flash-attn off,diffusion=on"},
    },
    {
        "name": "flux1-schnell", "aliases": ["flux-schnell", "flux.1-schnell", "flux", "flux1"],
        "title": "FLUX.1 schnell", "size": "12 GB",
        "description": "Detailed 1024 x 1024 pictures in 4 steps. Needs plenty of memory.",
        "tags": {"q4": "Q4_0", "q8": "Q8_0"}, "default_tag": "q4",
        "parts": [{"role": "diffusion_model", "repo": "second-state/FLUX.1-schnell-GGUF",
                   "files": ["flux1-schnell-{TAG}.gguf"], "tagged": True},
                  {"role": "clip_l", "repo": "second-state/FLUX.1-schnell-GGUF",
                   "files": ["clip_l.safetensors", "clip_l-f16.gguf"]},
                  {"role": "t5xxl", "repo": "second-state/FLUX.1-schnell-GGUF",
                   "files": ["t5xxl-Q8_0.gguf", "t5xxl_fp16.safetensors"]},
                  {"role": "vae", "repo": "second-state/FLUX.1-schnell-GGUF", "files": ["ae.safetensors"]}],
        "settings": {"width": 1024, "height": 1024, "steps": 4, "cfg_scale": 1.0, "sampler": "euler"},
    },
]

CHAT: List[Dict[str, Any]] = [
    {"name": "llama3.2:1b", "ref": "bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", "size": "0.8 GB",
     "description": "Basic chat. Not recommended for actions."},
    {"name": "llama3.2:3b", "ref": "bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M", "size": "2.0 GB",
     "description": "Good chat; simple actions."},
    {"name": "llama3.1:8b", "ref": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF:Q4_K_M", "size": "4.9 GB",
     "description": "Recommended. Reliable for multi-step actions."},
    {"name": "qwen3:4b", "ref": "Qwen/Qwen3-4B-GGUF:Q4_K_M", "size": "2.5 GB",
     "description": "Strong for its size, with thinking."},
    {"name": "qwen3:8b", "ref": "Qwen/Qwen3-8B-GGUF:Q4_K_M", "size": "5.0 GB",
     "description": "Strong all-round model, with thinking."},
    {"name": "gemma3:4b", "ref": "ggml-org/gemma-3-4b-it-GGUF:Q4_K_M", "size": "3.3 GB",
     "description": "Reads pictures you attach in chats (installs its vision file too)."},
]
CHAT_ALIASES = {"gemma3": "gemma3:4b", "gemma3:latest": "gemma3:4b", "llama3.2": "llama3.2:3b", "llama3.1": "llama3.1:8b", "llama3": "llama3.1:8b", "qwen3": "qwen3:8b",
                "llama3.2:latest": "llama3.2:3b", "llama3.1:latest": "llama3.1:8b", "qwen3:latest": "qwen3:8b"}


class CatalogError(Exception):
    user_facing = True


def _key(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().strip('"').strip("'")).lower()


def image_entry(text: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """(entry, tag) for a catalog name such as "z-image-turbo:q8", or None when it is not one."""
    key = _key(text)
    if not key or "/" in key:
        return None
    name, _, tag = key.partition(":")
    for entry in IMAGE:
        if name == entry["name"] or name in entry["aliases"]:
            tags = entry.get("tags") or {}
            if not tag or tag == "latest":
                return entry, entry.get("default_tag", "")
            wanted = tag.replace("_", "").replace("-", "")
            for t, quant in tags.items():
                if wanted in (t, quant.lower().replace("_", "")):
                    return entry, t
            if not tags:
                raise CatalogError(f"{entry['name']} has only one version; install it as {entry['name']}.")
            raise CatalogError(f"{entry['name']} has no version '{tag}'. Use one of: "
                               + ", ".join(f"{entry['name']}:{t}" for t in tags) + ".")
    return None


def is_image_name(text: str) -> bool:
    key = _key(text)
    return bool(key) and "/" not in key and not key.startswith(("http:", "https:"))


def unknown_image(text: str) -> CatalogError:
    names = ", ".join(e["name"] for e in IMAGE)
    return CatalogError(f"No image model named '{text.strip()}'. Type one of: {names}. "
                        "Or type a Hugging Face name, like owner/model.")


def pick(files: List[Dict[str, Any]], part: Dict[str, Any], quant: str) -> Dict[str, Any]:
    """The file a catalog part refers to, from a repository listing."""
    by_path = {f["path"].lower(): f for f in files}
    by_name: Dict[str, Dict[str, Any]] = {}
    for f in sorted(files, key=lambda f: f["path"].count("/")):
        by_name.setdefault(PurePosixPath(f["path"]).name.lower(), f)
    for want in part["files"]:
        want = want.replace("{TAG}", quant).lower()
        hit = by_path.get(want) or by_name.get(PurePosixPath(want).name)
        if hit:
            return hit
    if part.get("tagged") and quant:   # file names changed upstream: match the quantization instead
        q = quant.lower()
        ext = PurePosixPath(part["files"][0]).suffix.lower()
        hits = [f for f in files if f["path"].lower().endswith(ext) and q in PurePosixPath(f["path"]).name.lower()]
        if len(hits) == 1:
            return hits[0]
    raise CatalogError(f"{part['repo']} no longer has {part['files'][0].replace('{TAG}', quant)}. "
                       "Install the model from Hugging Face by its full name instead, or update Axis.")


def chat_ref(text: str) -> Optional[str]:
    """The Hugging Face name for a short chat model name such as llama3.1:8b, or None."""
    key = _key(text)
    for prefix in ("ollamarun", "ollamapull"):
        if key.startswith(prefix):
            key = key[len(prefix):]
    key = CHAT_ALIASES.get(key, key)
    for entry in CHAT:
        if key == entry["name"]:
            return entry["ref"]
    return None


def public() -> Dict[str, Any]:
    return {
        "image": [{"name": e["name"], "title": e["title"], "description": e["description"], "size": e["size"],
                   "tags": [f"{e['name']}:{t}" for t in (e.get("tags") or {})]} for e in IMAGE],
        "chat": [{"name": e["name"], "description": e["description"], "size": e["size"], "ref": e["ref"]}
                 for e in CHAT],
    }
