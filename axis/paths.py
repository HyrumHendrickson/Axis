"""Where Axis keeps its data."""

from __future__ import annotations

import os
from pathlib import Path


def axis_home() -> Path:
    override = os.environ.get("AXIS_HOME")
    return Path(override).expanduser() if override else Path.home() / "Axis"


def shared_root() -> Path:
    """The folder shared by every agent that has the Shared folder skill."""
    return axis_home() / "shared"


def models_root() -> Path:
    """Model files Axis downloads: models/llm (GGUF for llama.cpp) and models/image (Stable Diffusion)."""
    return axis_home() / "models"


def llm_root() -> Path:
    return models_root() / "llm"


def image_root() -> Path:
    return models_root() / "image"


def ocr_root() -> Path:
    """OCR models: Tesseract languages (ocr/tessdata) and PyTorch OCR weights (ocr/easyocr, ocr/doctr)."""
    return models_root() / "ocr"


def inside(path: Path, root: Path) -> bool:
    """True when path is root or lies under it (after resolving links)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False
