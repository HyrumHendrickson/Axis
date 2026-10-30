"""The `axis` command.

    axis              start the web app and open it in its own window (--tab for a browser tab)
    axis setup        install llama.cpp and stable-diffusion.cpp (Axis also does this on its first start)
    axis update       download the latest Axis from GitHub and install it
    axis uninstall    remove the Axis program (optionally your data too)
    axis shortcut     add Axis to the Start menu / Applications / app menu (or --remove it)
    axis quit         stop every running Axis, unload every model and stop what Axis started (including Ollama)
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
from pathlib import Path
from typing import List, Optional

from . import __version__, appwindow, engines, instances, jobs, manager, server, shortcut, tools, updates
from .paths import axis_home

DEFAULT_PORT = instances.FIRST_PORT
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
            appwindow.open_app(url, tab=args.tab)
        return 0

    try:
        httpd = server.make_server(args.port, args.url)
    except OSError as exc:
        print(f"Error: could not start the web app: {exc}", file=sys.stderr)
        return 1

    print(f"Axis {__version__} is running at {httpd.url}")
    if args.app:
        print("Started from the Axis app. Close the Axis window, or use Quit Axis in it, to stop.")
    else:
        print("Keep this terminal open while you use Axis. Press Ctrl+C to stop.")
    if not args.app:
        _first_run_shortcut()
    if not args.no_engine_install:
        started = manager.auto_install_engines()
        if started:
            print("First start: installing " + " and ".join(j.label.split(" ", 1)[1] for j in started)
                  + " in the background. Progress is on the Models page.")
    _stop_on_terminate()
    record = instances.register(httpd.server_address[1])
    if not args.no_browser:
        threading.Timer(0.5, _open_window, args=(httpd, args.tab)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the models...")
        print("Axis stopped.")
    finally:
        # Unload every Ollama model; stop llama-server, sd-server and ollama serve when Axis started them,
        # and every program still running for a picture, a reading or an agent's code.
        try:
            manager.shutdown(httpd.model_url)
        finally:
            httpd.server_close()
            instances.unregister(record)
    return 0


# How long Axis waits after the last page closes (a reload closes and reopens the page),
# and how long a page may go without reporting before Axis treats it as gone.
WINDOW_GRACE = 8.0
WINDOW_STALE = 600.0


def _open_window(httpd, tab: bool) -> None:
    """Open Axis; when it got a window of its own, stop Axis once that window is closed."""
    if appwindow.open_app(httpd.url, tab=tab) == "window":
        httpd.close_with_window = True
        print("Axis opened in its own window. Closing that window stops Axis.")
        _watch_window(httpd)


def _watch_window(httpd, grace: float = WINDOW_GRACE, stale: float = WINDOW_STALE, every: float = 1.0) -> threading.Thread:
    """Once every Axis page has closed: stop the models Axis started, then stop Axis."""
    def watch() -> None:
        while True:
            time.sleep(every)
            if httpd.pages.all_closed(time.monotonic(), stale, grace):
                break
        print("The Axis window was closed. Stopping the models...")
        manager.shutdown(httpd.model_url)
        print("Axis stopped.")
        httpd.shutdown()  # serve_forever returns and cmd_serve finishes

    t = threading.Thread(target=watch, name="axis-window-watch", daemon=True)
    t.start()
    return t


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
    wanted = [args.only] if args.only else list(manager.ENGINE_FINDERS)   # Ollama is installed only on request
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
        job = engines.submit(engine, args.build or engines.default_build(engine))
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


# ----------------------------------------------------------------------- quit


def cmd_quit(args) -> int:
    """Stop every running Axis, then make sure nothing Axis turned on is left running or loaded.

    That is every llama-server, sd-server and `ollama serve` that an Axis started (Ollama too, when Axis
    started it; an Ollama you started yourself keeps running, with no model loaded) and every model in
    memory in Ollama.
    """
    ports = instances.usual_ports()  # where make_server looks for a free port
    if args.port and args.port not in ports:
        ports.append(args.port)
    stopped = instances.quit_all(ports, timeout=args.timeout)
    # Anything an Axis left behind (one that crashed, or did not finish stopping).
    from . import procs
    leftovers = [m.key for m in procs.running()]
    unloaded = manager.shutdown(args.url)
    print(("Stopped " + (f"{stopped} copies of Axis" if stopped != 1 else "1 copy of Axis"))
          if stopped else "No Axis was running.")
    if leftovers:
        print(f"Stopped {len(leftovers)} program(s) an Axis left running: " + ", ".join(leftovers))
    if unloaded:
        print("Unloaded from Ollama: " + ", ".join(unloaded))
    still = [m.key for m in procs.running()]
    if still:
        print("Could not stop: " + ", ".join(still) + ". Close them yourself.", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------- update


def cmd_update(args) -> int:
    if args.check:
        return _check_update()
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


def _check_update() -> int:
    if updates.disabled():
        print("The update check is turned off (AXIS_NO_UPDATE_CHECK).")
        return 0
    updates.check(force=True)
    deadline = time.time() + 30
    while updates.state()["status"] == "checking" and time.time() < deadline:
        time.sleep(0.2)
    st = updates.state()
    if st["status"] == "outdated":
        what = f"Axis {st['latest']} is available (you have {st['current']})"
        if st.get("changed") and not updates.is_newer(st["latest"] or "", st["current"]):
            what = f"A newer build of Axis {st['current']} is on GitHub ({st['changed']} file(s) differ)"
        print(what + ". Run `axis update` to install it.")
        return 0
    if st["status"] == "current":
        print(f"Axis {st['current']} is up to date." + ("" if st.get("compared") else
              " (Only the version number was compared; GitHub did not answer the file check.)"))
        return 0
    print(f"Could not check for updates: {st.get('error') or 'no answer from GitHub'}", file=sys.stderr)
    return 1


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
        # A private folder, so the helper's name cannot be guessed or planted by another program.
        work = Path(tempfile.mkdtemp(prefix="axis-uninstall-"))
        helper = work / "axis_uninstall_helper.py"
        log = work / "axis-uninstall.log"
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
    p.add_argument("--no-browser", action="store_true", help="do not open Axis; open the printed address yourself")
    p.add_argument("--tab", action="store_true",
                   help="open Axis in a normal browser tab instead of its own window (env: AXIS_BROWSER=tab)")
    p.add_argument("--url", help="model server address (default: auto-detect; env: AXIS_URL)")
    p.add_argument("--no-engine-install", action="store_true",
                   help="do not install llama.cpp and stable-diffusion.cpp on the first start "
                        "(env: AXIS_NO_ENGINE_INSTALL=1)")
    p.add_argument("--app", action="store_true", help=argparse.SUPPRESS)  # used by the app shortcut
    p.set_defaults(func=cmd_serve)

    sub = p.add_subparsers(dest="command", metavar="[command]")
    s = sub.add_parser("setup", help="install llama.cpp and stable-diffusion.cpp for Axis")
    s.add_argument("--build", choices=engines.BUILDS, default=None,
                   help="which build to install (default: cpu; auto picks the best one for this computer)")
    s.add_argument("--only", choices=list(manager.ENGINE_FINDERS), help="install only llama.cpp (llama) or stable-diffusion.cpp (sd)")
    s.add_argument("--force", action="store_true", help="install even when a copy is already found")
    s.set_defaults(func=cmd_setup)

    u = sub.add_parser("update", help="install the latest Axis from GitHub")
    u.add_argument("--source", help="install from this URL, ZIP file or folder instead of GitHub")
    u.add_argument("--check", action="store_true", help="only check whether a newer Axis is on GitHub")
    u.set_defaults(func=cmd_update)

    sc = sub.add_parser("shortcut", help="add Axis to the Start menu, Applications or app menu")
    sc.add_argument("--remove", action="store_true", help="remove the shortcut instead")
    sc.add_argument("--no-desktop", action="store_true", help="Windows: do not also put a shortcut on the desktop")
    sc.set_defaults(func=cmd_shortcut)

    q = sub.add_parser("quit", help="stop every running Axis, unload every model and stop what Axis started")
    q.add_argument("--timeout", type=float, default=45.0,
                   help="seconds to wait for each Axis to stop before ending it (default 45)")
    q.set_defaults(func=cmd_quit)

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
