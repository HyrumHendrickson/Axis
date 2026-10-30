"""Optional local LaTeX rendering support for the web chat.

MathJax is downloaded only when the user asks for it.  Keeping the asset in
Axis' home directory means chat rendering still works without a CDN once it
has been installed, and the user can remove it from the Tools page.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict

from .paths import axis_home

ASSET_NAME = "tex-svg.js"
ASSET_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"


class LatexError(Exception):
    pass


def _folder() -> Path:
    return axis_home() / "latex"


def _settings_path() -> Path:
    return axis_home() / "latex-settings.json"


def _settings() -> Dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    axis_home().mkdir(parents=True, exist_ok=True)
    tmp = _settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _settings_path())


def status() -> Dict[str, Any]:
    installed = (_folder() / ASSET_NAME).is_file()
    return {"installed": installed, "enabled": installed and bool(_settings().get("enabled")),
            "size": (_folder() / ASSET_NAME).stat().st_size if installed else 0,
            "url": ASSET_URL}


def install() -> Dict[str, Any]:
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ASSET_NAME
    temp = Path(tempfile.mkstemp(prefix="mathjax-", suffix=".js", dir=str(folder))[1])
    try:
        request = urllib.request.Request(ASSET_URL, headers={"User-Agent": "Axis/4.3.6"})
        with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        if temp.stat().st_size < 10000:
            raise LatexError("The LaTeX renderer download was incomplete.")
        os.replace(temp, target)
    except Exception as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        if isinstance(exc, LatexError):
            raise
        raise LatexError(f"Could not download the LaTeX renderer: {exc}") from exc
    _save({**_settings(), "enabled": True})
    return status()


def set_enabled(enabled: Any) -> Dict[str, Any]:
    if bool(enabled) and not (_folder() / ASSET_NAME).is_file():
        raise LatexError("Download the LaTeX renderer before turning it on.")
    _save({**_settings(), "enabled": bool(enabled)})
    return status()


def remove() -> Dict[str, Any]:
    try:
        (_folder() / ASSET_NAME).unlink()
    except FileNotFoundError:
        pass
    try:
        _folder().rmdir()
    except OSError:
        pass
    _save({"enabled": False})
    return status()


def script_path() -> Path | None:
    info = status()
    return _folder() / ASSET_NAME if info["installed"] else None
