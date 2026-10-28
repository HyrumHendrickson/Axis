"""The `axis` command.

    axis              start the web app and open it in the browser
    axis setup        install llama.cpp and stable-diffusion.cpp (Axis also does this on its first start)
    axis update       download the latest Axis from GitHub and install it
    axis uninstall    remove the Axis program (optionally your data too)
    axis shortcut     add Axis to the Start menu / Applications / app menu (or --remove it)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import List, Optional

from . import __version__, engines, jobs, manager, server, shortcut, tools, updates
from .paths import axis_home

DEFAULT_PORT = 8765
PACKAGE = "axis-agent-manager"
REPO_ZIP = updates.REPO_ZIP


# ---------------------------------------------------------------------- serve


def _log_to_file() -> None:
    """The app launcher has no terminal (pythonw on Windows has no stdout at all), so write to a log."""
    home = axis_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        log = open(home / "axis.log", "w", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr = log


def _first_run_shortcut() -> None:
    try:
        made = shortcut.ensure_once()
    except shortcut.ShortcutError as exc:
        print(f"Could not add Axis to your apps: {exc}. Run `axis shortcut` to try again.")
        return
    if made:
        print("Added Axis to your apps (" + ", ".join(made) + "). Remove it with `axis shortcut --remove`.")


def cmd_serve(args) -> int:
    if args.app or sys.stdout is None:
        _log_to_file()
    if not 0 <= args.port <= 65535:
        print("Error: --port must be between 0 and 65535.", file=sys.stderr)
        return 2

    if args.port and server.find_running(args.port):
        url = f"http://127.0.0.1:{args.port}"
        print(f"Axis is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    try:
        httpd = server.make_server(args.port, args.url)
    except OSError as exc:
        print(f"Error: could not start the web app: {exc}", file=sys.stderr)
        return 1

    print(f"Axis {__version__} is running at {httpd.url}")
    if args.app:
        print("Started from the Axis app. Use Quit Axis in the page to stop it.")
    else:
        print("Keep this window open while you use Axis. Press Ctrl+C to stop.")
    if not args.app:
        _first_run_shortcut()
    if not args.no_engine_install:
        started = manager.auto_install_engines()
        if started:
            print("First start: installing " + " and ".join(j.label.split(" ", 1)[1] for j in started)
                  + " in the background. Progress is on the Models page.")
    _stop_on_terminate()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(httpd.url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the models Axis started...")
        print("Axis stopped.")
    finally:
        httpd.server_close()
        manager.shutdown()  # llama-server, sd-server and ollama serve, when Axis started them
    return 0


def _stop_on_terminate() -> None:
    """Treat a request to terminate (closing the terminal, `kill`) like Ctrl+C, so models are stopped."""
    import signal

    def handler(signum, frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------- setup


def cmd_setup(args) -> int:
    """Install the engines from the terminal, for example right after `pipx install`."""
    wanted = [args.only] if args.only else list(engines.ENGINES)
    failed = False
    for engine in wanted:
        label = engines.ENGINES[engine]["label"]
        current = manager.ENGINE_FINDERS[engine]()
        if current is not None and not args.force:
            print(f"{label}: found at {current}. Use --force to install Axis's own copy anyway.")
            continue
        if not engines.builds_for(engine):
            osname, arch = engines.this_platform()
            print(f"{label}: there is no ready-built program for {osname} on {arch}; skipped.")
            continue
        job = engines.submit(engine, args.build)
        last = ""
        try:
            while job.state == "running":
                pct = f" {job.public()['percent']:.0f}%" if job.public()["percent"] is not None else ""
                line = f"{label}: {job.phase}{pct}"
                if line != last:
                    print("\r" + line.ljust(78)[:78], end="", flush=True)
                    last = line
                time.sleep(0.3)
        except KeyboardInterrupt:
            jobs.cancel(job.id)
            jobs.wait(job, 30)
            print(f"\n{label}: cancelled.")
            return 1
        print("\r" + " " * 78 + "\r", end="")
        if job.state == "done":
            print(f"{label}: installed {job.result.get('tag')} ({job.result.get('build')} build).")
        else:
            failed = True
            print(f"{label}: {job.error}", file=sys.stderr)
    _first_run_shortcut()
    return 1 if failed else 0


# ------------------------------------------------------------------ shortcut


def cmd_shortcut(args) -> int:
    try:
        if args.remove:
            gone = shortcut.remove()
            print("Removed " + ", ".join(gone) if gone else "There was no Axis shortcut to remove.")
        else:
            made = shortcut.create(desktop=not args.no_desktop)
            print("Axis is now in your apps:\n  " + "\n  ".join(made))
            print("The axis command still works as before.")
    except shortcut.ShortcutError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------- update


def cmd_update(args) -> int:
    source = args.source or REPO_ZIP
    print(f"Updating Axis {__version__} from {source}")
    result = subprocess.run(updates.pip_command(source))
    if result.returncode != 0:
        print("\nUpdate failed. Check your internet connection and try again.", file=sys.stderr)
        print(f"You can also reinstall by hand:  pipx install --force {REPO_ZIP}", file=sys.stderr)
        return result.returncode
    check = subprocess.run([sys.executable, "-c", "import axis; print(axis.__version__)"],
                           capture_output=True, text=True)
    new = check.stdout.strip() or "unknown"
    print(f"\nAxis updated: {__version__} -> {new}.")
    print("If Axis is open in another window, stop it with Ctrl+C and run axis again.")
    return 0


# ------------------------------------------------------------------ uninstall


def installed_with_pipx() -> bool:
    prefix = Path(sys.prefix)
    return prefix.parent.name == "venvs" and (prefix / "pipx_metadata.json").is_file()


def find_pipx() -> Optional[List[str]]:
    exe = shutil.which("pipx")
    if exe:
        return [exe]
    for py in dict.fromkeys([tools.python_executable(), getattr(sys, "_base_executable", ""), "python3", "python", "py"]):
        if not py:
            continue
        try:
            ok = subprocess.run([py, "-c", "import pipx"], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return [py, "-m", "pipx"]
    return None


def uninstall_command() -> Optional[List[str]]:
    if installed_with_pipx():
        pipx = find_pipx()
        return pipx + ["uninstall", PACKAGE] if pipx else None
    return [sys.executable, "-m", "pip", "uninstall", "-y", "--disable-pip-version-check", PACKAGE]


WINDOWS_HELPER = r'''
import ctypes, subprocess, sys, time
pid, log = int(sys.argv[1]), sys.argv[2]
k = ctypes.windll.kernel32
h = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
if h:
    k.WaitForSingleObject(h, 60000)
    k.CloseHandle(h)
time.sleep(2)  # let the axis.exe launcher exit too
r = subprocess.run(sys.argv[3:], capture_output=True, text=True)
with open(log, "w", encoding="utf-8") as f:
    f.write("exit code %d\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
'''


def is_windows() -> bool:
    return os.name == "nt"


def _ask(question: str) -> bool:
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def cmd_uninstall(args) -> int:
    data = axis_home()
    if not args.yes and not _ask("Remove the Axis program from this computer?"):
        print("Cancelled.")
        return 1
    delete_data = args.delete_data
    if not delete_data and not args.yes and data.exists():
        delete_data = _ask(f"Also delete your agents, skills and agent files in {data}?")

    cmd = uninstall_command()
    if cmd is None:
        print("Could not find pipx, which installed Axis. Remove Axis with:", file=sys.stderr)
        print(f"  pipx uninstall {PACKAGE}", file=sys.stderr)
        return 1

    try:
        for path in shortcut.remove(forget=True):
            print(f"Removed {path}")
    except shortcut.ShortcutError as exc:
        print(f"Could not remove the Axis shortcut: {exc}", file=sys.stderr)

    if delete_data and data.exists():
        shutil.rmtree(data, ignore_errors=True)
        print(f"Deleted {data}")
    elif data.exists():
        print(f"Kept your data in {data}")

    if is_windows():
        # Windows cannot delete the files of a running program, so a helper
        # finishes the job after this command exits.
        helper = Path(tempfile.gettempdir()) / "axis_uninstall_helper.py"
        log = Path(tempfile.gettempdir()) / "axis-uninstall.log"
        helper.write_text(WINDOWS_HELPER, encoding="utf-8")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([tools.python_executable(), str(helper), str(os.getpid()), str(log)] + cmd,
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Axis will finish uninstalling a few seconds after this command ends.")
        print(f"Details are written to {log}")
        return 0

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("Uninstall failed. Try: pipx uninstall " + PACKAGE, file=sys.stderr)
        return result.returncode
    print("Axis has been uninstalled.")
    return 0


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axis",
        description="Run `axis` to open the Axis web app. Axis installs llama.cpp and stable-diffusion.cpp "
        "itself, and chat and image models from its Models page.",
    )
    p.add_argument("--version", action="version", version=f"axis {__version__}")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port for the web app (default {DEFAULT_PORT})")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.add_argument("--url", help="model server address (default: auto-detect; env: AXIS_URL)")
    p.add_argument("--no-engine-install", action="store_true",
                   help="do not install llama.cpp and stable-diffusion.cpp on the first start "
                        "(env: AXIS_NO_ENGINE_INSTALL=1)")
    p.add_argument("--app", action="store_true", help=argparse.SUPPRESS)  # used by the app shortcut
    p.set_defaults(func=cmd_serve)

    sub = p.add_subparsers(dest="command", metavar="[command]")
    s = sub.add_parser("setup", help="install llama.cpp and stable-diffusion.cpp for Axis")
    s.add_argument("--build", choices=engines.BUILDS, default="auto",
                   help="which build to install (default: the best one for this computer)")
    s.add_argument("--only", choices=list(engines.ENGINES), help="install only llama.cpp (llama) or stable-diffusion.cpp (sd)")
    s.add_argument("--force", action="store_true", help="install even when a copy is already found")
    s.set_defaults(func=cmd_setup)

    u = sub.add_parser("update", help="install the latest Axis from GitHub")
    u.add_argument("--source", help="install from this URL, ZIP file or folder instead of GitHub")
    u.set_defaults(func=cmd_update)

    sc = sub.add_parser("shortcut", help="add Axis to the Start menu, Applications or app menu")
    sc.add_argument("--remove", action="store_true", help="remove the shortcut instead")
    sc.add_argument("--no-desktop", action="store_true", help="Windows: do not also put a shortcut on the desktop")
    sc.set_defaults(func=cmd_shortcut)

    x = sub.add_parser("uninstall", help="remove Axis from this computer")
    x.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation (keeps your data)")
    x.add_argument("--delete-data", action="store_true", help="also delete your agents, skills and agent files")
    x.set_defaults(func=cmd_uninstall)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
