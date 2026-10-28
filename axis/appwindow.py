"""Open the Axis page in a window of its own instead of a browser tab.

Chromium-based browsers (Chrome, Edge, Brave, Chromium) have an "app mode": started with
--app=URL they show the page in a plain window with no tabs or address bar, and it gets its
own entry in the taskbar or Dock. Edge comes with Windows 10 and 11, so this works on every
Windows computer. When no such browser is found (for example a Mac with only Safari), or
the person asks for a tab, Axis falls back to an ordinary tab in the default browser.

    AXIS_BROWSER=<path or name>   use this Chromium-based browser for the window
    AXIS_BROWSER=tab              always open a normal browser tab (same as `axis --tab`)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

TAB = "tab"

# Program names to look for on the PATH (Linux, and anything else that is not Windows or macOS).
_LINUX_NAMES = ["google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable",
                "brave-browser", "brave", "chromium", "chromium-browser"]

# macOS app bundles: (bundle name, program inside Contents/MacOS).
_MAC_APPS = [("Google Chrome.app", "Google Chrome"), ("Microsoft Edge.app", "Microsoft Edge"),
             ("Brave Browser.app", "Brave Browser"), ("Chromium.app", "Chromium")]

# Windows: (environment variable for the base folder, path below it).
_WINDOWS_PATHS = [
    ("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"),
    ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
    ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
    ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
    ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe"),
    ("LOCALAPPDATA", r"Microsoft\Edge\Application\msedge.exe"),
    ("PROGRAMFILES", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
]


def _candidates() -> List[str]:
    if os.name == "nt":
        found = []
        for var, rel in _WINDOWS_PATHS:
            base = os.environ.get(var)
            if base:
                found.append(str(Path(base) / rel))
        return found
    if sys.platform == "darwin":
        roots = [Path("/Applications"), Path.home() / "Applications"]
        return [str(root / app / "Contents" / "MacOS" / exe) for root in roots for app, exe in _MAC_APPS]
    return [p for p in (shutil.which(n) for n in _LINUX_NAMES) if p]


def find_browser() -> Optional[str]:
    """The Chromium-based browser to open the app window with, or None to use a normal tab."""
    choice = os.environ.get("AXIS_BROWSER", "").strip()
    if choice.lower() == TAB:
        return None
    if choice:
        path = shutil.which(choice) or (choice if Path(choice).is_file() else None)
        if path:
            return path
        print(f"AXIS_BROWSER is set to {choice}, which was not found; looking for Chrome or Edge instead.")
    for path in _candidates():
        if Path(path).is_file():
            return path
    return None


def app_command(browser: str, url: str) -> List[str]:
    return [browser, f"--app={url}"]


def _launch(cmd: List[str]) -> None:
    """Start the browser detached, so it neither blocks Axis nor closes when Axis stops."""
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)


def open_app(url: str, tab: bool = False) -> str:
    """Show Axis at url. Returns "window" or "tab", whichever was opened."""
    browser = None if tab else find_browser()
    if browser:
        try:
            _launch(app_command(browser, url))
            return "window"
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Could not open the Axis window with {browser} ({exc}); opening a browser tab instead.")
    webbrowser.open(url)
    return "tab"
