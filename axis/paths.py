"""Where Axis keeps its data."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


def axis_home() -> Path:
    override = os.environ.get("AXIS_HOME")
    return Path(override).expanduser() if override else Path.home() / "Axis"


def shared_root() -> Path:
    """The one workspace shared by every agent."""
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


def _unlock(path: str) -> None:
    """Make a folder tree deletable: everything writable, and every folder listable.

    Links are never followed, so a link inside the tree cannot make this change a file outside it.
    """
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return
    if stat.S_ISLNK(mode):
        return
    try:
        if stat.S_ISDIR(mode):
            os.chmod(path, mode | stat.S_IRWXU)
        elif not mode & stat.S_IWUSR:
            os.chmod(path, mode | stat.S_IWUSR)
    except OSError:
        return
    if stat.S_ISDIR(mode):
        try:
            names = os.listdir(path)
        except OSError:
            return
        for name in names:
            _unlock(os.path.join(path, name))


def remove_tree(path: Path) -> None:
    """Delete a folder and everything in it, including read-only files and folders.

    Windows refuses to delete read-only files, and POSIX refuses to delete anything inside a
    read-only folder, so the tree is made writable first. A link is removed, never followed.
    Anything that still cannot be deleted is left in place; callers check whether the folder is gone.
    """
    _unlock(str(path))

    def retry(func, name, *_):
        _unlock(os.path.dirname(name))
        _unlock(name)
        try:
            func(name)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)
