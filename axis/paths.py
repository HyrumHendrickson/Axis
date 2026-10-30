"""Where Axis keeps its data.

The workspace is the folder agents and the Workspace tab work in. It is ~/Axis/shared unless the
user chose another folder; the choice is kept in ~/Axis/workspace-settings.json. A chat can also use
a folder of its own (see check_work_folder). Pictures Axis makes always go to ~/Axis/images.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path


def axis_home() -> Path:
    override = os.environ.get("AXIS_HOME")
    return Path(override).expanduser() if override else Path.home() / "Axis"


def shared_root() -> Path:
    """The one workspace shared by every agent."""
    return axis_home() / "shared"


def images_folder() -> Path:
    """Where every picture Axis makes is saved (the Images tab and the generate_image action)."""
    return axis_home() / "images"


def _workspace_settings() -> Path:
    return axis_home() / "workspace-settings.json"


class FolderError(Exception):
    pass


def check_work_folder(value: object) -> Path:
    """A folder the user chose for the workspace or for one chat, as an absolute, resolved path.

    Any existing folder may be chosen except the top of a drive or file system, and Axis's own data
    folder (the shared folder inside it is allowed), so agents cannot change Axis's settings or models.
    """
    if not isinstance(value, str) or not value.strip():
        raise FolderError("Choose a folder.")
    raw = Path(value.strip()).expanduser()
    if not raw.is_absolute():
        raise FolderError("Give the folder's full path, for example /home/you/projects or C:\\Users\\you\\projects.")
    try:
        folder = raw.resolve()
    except OSError as exc:
        raise FolderError(f"That folder cannot be used: {exc.strerror or exc}") from exc
    if not folder.is_dir():
        raise FolderError(f"Folder '{value.strip()}' does not exist.")
    if folder.parent == folder:
        raise FolderError("The top of a drive cannot be used. Choose a folder inside it.")
    home = axis_home()
    if inside(folder, home) and not inside(folder, shared_root()):
        raise FolderError(f"Folders inside {home} hold Axis's own data and cannot be used, except {shared_root()}.")
    return folder


def workspace_root() -> Path:
    """The folder agents and the Workspace tab use: the one the user chose, or ~/Axis/shared."""
    try:
        data = json.loads(_workspace_settings().read_text(encoding="utf-8"))
        chosen = data.get("folder") if isinstance(data, dict) else None
        if chosen:
            return check_work_folder(chosen)
    except (OSError, ValueError, FolderError):
        pass   # no setting, or the chosen folder is gone: use the default
    return shared_root()


def workspace_setting() -> dict:
    """What the Workspace folder setting holds, for the page."""
    chosen = ""
    try:
        data = json.loads(_workspace_settings().read_text(encoding="utf-8"))
        chosen = str(data.get("folder") or "") if isinstance(data, dict) else ""
    except (OSError, ValueError):
        pass
    root = workspace_root()
    return {"folder": str(root), "default": str(shared_root()), "is_default": root == shared_root(),
            "missing": chosen if chosen and root == shared_root() else ""}


def set_workspace_root(value: object) -> dict:
    """Use value as the workspace folder; an empty value goes back to ~/Axis/shared."""
    folder = "" if value in (None, "") else str(check_work_folder(value))
    path = _workspace_settings()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"folder": folder}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return workspace_setting()


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


def unlink_quiet(path: Path, tries: int = 10) -> None:
    """Delete a file that may not exist. Never raises.

    On Windows an antivirus program or the search indexer often has a file we just finished with open for
    a moment, and deleting it fails. It is tried again for a few seconds; a leftover temporary file is
    harmless and must never turn a finished install into a failed one.
    """
    for attempt in range(max(1, tries)):
        try:
            Path(path).unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt + 1 < tries:
                time.sleep(0.3)


def replace_retry(src: Path, dst: Path, tries: int = 20) -> None:
    """os.replace that waits out the short locks Windows puts on freshly written files and folders."""
    for attempt in range(max(1, tries)):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt + 1 >= tries:
                raise
            time.sleep(0.25)
