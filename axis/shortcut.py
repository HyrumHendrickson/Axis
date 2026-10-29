"""Put Axis in the Start menu (Windows), Applications (macOS) or the app menu (Linux).

The launcher runs `python -m axis --app` with the Python Axis was installed with, so it
keeps working wherever pipx put Axis. The `axis` command is not affected.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

from .paths import axis_home

APP_NAME = "Axis"
BUNDLE_ID = "com.hyrumhendrickson.axis"
ICONS = Path(__file__).resolve().parent / "icons"
MARKER = ".shortcut-created"


class ShortcutError(Exception):
    pass


def _windowless_python() -> str:
    """pythonw.exe on Windows (no console window), otherwise the current Python."""
    exe = Path(sys.executable)
    if os.name == "nt":
        w = exe.with_name("pythonw.exe")
        if w.is_file():
            return str(w)
    return str(exe)


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------- Windows

_PS_CREATE = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$paths = @([IO.Path]::Combine([Environment]::GetFolderPath('Programs'), 'Axis.lnk'))
if ($env:AXIS_LNK_DESKTOP -eq '1') { $paths += [IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Axis.lnk') }
foreach ($p in $paths) {
  $s = $shell.CreateShortcut($p)
  $s.TargetPath = $env:AXIS_LNK_TARGET
  $s.Arguments = $env:AXIS_LNK_ARGS
  $s.WorkingDirectory = $env:AXIS_LNK_WORKDIR
  $s.IconLocation = $env:AXIS_LNK_ICON + ',0'
  $s.Description = 'Chat with local AI models and your agents'
  $s.Save()
  Write-Output $p
}
"""

_PS_REMOVE = r"""
foreach ($d in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
  $p = [IO.Path]::Combine($d, 'Axis.lnk')
  if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force; Write-Output $p }
}
"""


def _powershell(script: str, env: dict) -> List[str]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise ShortcutError("PowerShell was not found, so the shortcut could not be made.")
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       env={**os.environ, **env}, capture_output=True, text=True,
                       creationflags=_no_window(), timeout=60)
    if r.returncode != 0:
        raise ShortcutError((r.stderr or r.stdout).strip() or "PowerShell could not make the shortcut.")
    return [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]


def _windows_create(desktop: bool) -> List[str]:
    return _powershell(_PS_CREATE, {
        "AXIS_LNK_TARGET": _windowless_python(),
        "AXIS_LNK_ARGS": "-m axis --app",
        "AXIS_LNK_WORKDIR": str(Path.home()),
        "AXIS_LNK_ICON": str(ICONS / "axis.ico"),
        "AXIS_LNK_DESKTOP": "1" if desktop else "0",
    })


def _windows_remove() -> List[str]:
    return _powershell(_PS_REMOVE, {})


# --------------------------------------------------------------------- macOS

def _mac_bundle() -> Path:
    return Path.home() / "Applications" / f"{APP_NAME}.app"


def _mac_create() -> List[str]:
    app = _mac_bundle()
    if app.exists():
        shutil.rmtree(app)
    macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    shutil.copyfile(ICONS / "axis.icns", res / "axis.icns")
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": APP_NAME,
            "CFBundleIconFile": "axis",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": _version(),
            "LSMinimumSystemVersion": "10.13",
        }, f)
    launcher = macos / APP_NAME
    # Start Axis in the background and exit, so the Dock icon does not sit there "running".
    launcher.write_text(
        "#!/bin/sh\n"
        f"cd \"$HOME\"\nnohup {shlex.quote(sys.executable)} -m axis --app >/dev/null 2>&1 &\n",
        encoding="utf-8")
    launcher.chmod(0o755)
    lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                  "LaunchServices.framework/Support/lsregister")
    if os.path.exists(lsregister):
        subprocess.run([lsregister, "-f", str(app)], capture_output=True)
    return [str(app)]


def _mac_remove() -> List[str]:
    app = _mac_bundle()
    if app.exists():
        shutil.rmtree(app)
        return [str(app)]
    return []


# --------------------------------------------------------------------- Linux

def _linux_entry() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "applications" / "axis.desktop"


def _linux_create() -> List[str]:
    entry = _linux_entry()
    entry.parent.mkdir(parents=True, exist_ok=True)
    exe = sys.executable.replace("\\", "\\\\").replace('"', '\\"')
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Chat with local AI models and your agents\n"
        f"Exec=\"{exe}\" -m axis --app\n"
        f"Icon={ICONS / 'axis.png'}\n"
        "Terminal=false\n"
        "Categories=Utility;Development;\n",
        encoding="utf-8")
    entry.chmod(0o755)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(entry.parent)], capture_output=True)
    return [str(entry)]


def _linux_remove() -> List[str]:
    entry = _linux_entry()
    if entry.exists():
        entry.unlink()
        return [str(entry)]
    return []


# ---------------------------------------------------------------------- API

def _version() -> str:
    from . import __version__
    return __version__


def create(desktop: bool = True) -> List[str]:
    """Make the app launcher. Returns the paths written."""
    try:
        if os.name == "nt":
            made = _windows_create(desktop)
        elif sys.platform == "darwin":
            made = _mac_create()
        else:
            made = _linux_create()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(str(exc)) from exc
    home = axis_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / MARKER).write_text("\n".join(made), encoding="utf-8")
    return made


def remove(forget: bool = False) -> List[str]:
    """Remove the app launcher. Returns the paths removed.

    The first-run marker is kept, so `axis` does not put the shortcut back; `forget`
    (used by `axis uninstall`) clears it so a later reinstall adds the shortcut again.
    """
    try:
        if os.name == "nt":
            gone = _windows_remove()
        elif sys.platform == "darwin":
            gone = _mac_remove()
        else:
            gone = _linux_remove()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(str(exc)) from exc
    marker = axis_home() / MARKER
    if forget and marker.exists():
        marker.unlink()
    return gone


def ensure_once() -> List[str]:
    """Make the launcher the first time Axis runs. Never again, so a deleted shortcut stays deleted."""
    if os.environ.get("AXIS_NO_SHORTCUT") == "1" or (axis_home() / MARKER).exists():
        return []
    return create()
