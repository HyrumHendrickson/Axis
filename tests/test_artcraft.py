"""Tests for the Art Craft tab: installing and opening the Craft apps, and installing portable Rust (artcraft.py).

Everything runs against fakes: a fake GitHub (API, release pages and downloads) serving small archives
with stand-in programs, and a fake rustup server whose installer is a small shell script.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sys
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from axis import artcraft, engines, jobs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engines import _tar
from test_manage import POSIX_ONLY, Base, request, serve, wait_job

LINUX = ("linux", "x64")
TRIPLE = "x86_64-unknown-linux-gnu"


def app_release(app_id, tag="v1.2.3", program=None, sums=False, extra=None):
    """(tag, assets) for one app: a Linux tarball with a stand-in program, and files for other systems."""
    ver = tag.lstrip("v")
    prog = program or f'#!/bin/sh\necho "{app_id} {ver}"\n'.encode()
    folder = f"{app_id}-{ver}-linux-x86_64"
    assets = {
        f"{folder}.tar.gz": _tar({f"{folder}/bin/{app_id}": (prog, 0o755), f"{folder}/bin/{app_id}-cli": (prog, 0o755),
                                  f"{folder}/share/doc/README.md": (b"readme", 0o644)}),
        f"{app_id}-{ver}-windows-x64-portable.zip": b"windows",
        f"{app_id}-{ver}-macos-universal.dmg": b"mac",
        f"{app_id}-cli-{ver}-macos-universal.zip": b"cli only",
    }
    assets.update(extra or {})
    if sums:
        assets["SHA256SUMS.txt"] = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in assets.items()).encode()
    return tag, assets


class FakeGitHub:
    """Latest releases by repository: {"owner/repo": (tag, {asset name: bytes})}."""

    def __init__(self, repos, api=True, digests=True, bad_digest=()):
        self.repos, self.api, self.digests, self.bad_digest = repos, api, digests, set(bad_digest)
        self.downloads = []
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="text/html", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
                if parts[:1] == ["api"]:   # /api/repos/<o>/<r>/releases/latest
                    if not gh.api:
                        return self._send(429, b"{}", "application/json")
                    rel = gh.repos.get("/".join(parts[2:4]))
                    if rel is None or parts[4:] != ["releases", "latest"]:
                        return self._send(404, b"{}", "application/json")
                    tag, assets = rel
                    out = {"tag_name": tag, "assets": []}
                    for n, b in assets.items():
                        a = {"name": n, "size": len(b), "browser_download_url": f"{base}/{parts[2]}/{parts[3]}/releases/download/{tag}/{n}"}
                        if gh.digests:
                            a["digest"] = "sha256:" + gh.digest(n, b)
                        out["assets"].append(a)
                    return self._send(200, json.dumps(out).encode(), "application/json")
                repo = "/".join(parts[:2])
                rel = gh.repos.get(repo)
                if rel is None:
                    return self._send(404)
                tag, assets = rel
                if parts[2:] == ["releases", "latest"]:
                    return self._send(302, extra={"Location": f"/{repo}/releases/tag/{tag}"})
                if parts[2:] == ["releases", "tag", tag]:
                    return self._send(200, b"<html>release</html>")
                if parts[2:] == ["releases", "expanded_assets", tag]:
                    html = "".join(f'<a href="/{repo}/releases/download/{tag}/{n}">{n}</a>'
                                   + (f'<clipboard-copy aria-label="Copy to clipboard digest for {n}" type="button" '
                                      f'value="sha256:{gh.digest(n, b)}">' if gh.digests else "") for n, b in assets.items())
                    return self._send(200, html.encode())
                if parts[2:4] == ["releases", "download"] and parts[4] == tag and parts[5] in assets:
                    gh.downloads.append(parts[5])
                    return self._send(200, assets[parts[5]], "application/octet-stream")
                return self._send(404)

        self.srv, self.url = serve(H)

    def digest(self, name, data):
        return ("0" * 64) if name in self.bad_digest else hashlib.sha256(data).hexdigest()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


RUSTUP_INIT = b"""#!/bin/sh
echo "info: syncing channel updates for 'stable'"
echo "$@" > "$CARGO_HOME/args.txt"
echo "$RUSTUP_HOME" > "$CARGO_HOME/rustup_home.txt"
mkdir -p "$CARGO_HOME/bin"
printf '#!/bin/sh\\necho "cargo 9.9.9 (fake)"\\n' > "$CARGO_HOME/bin/cargo"
chmod +x "$CARGO_HOME/bin/cargo"
echo "info: default toolchain set to 'stable'"
"""


class FakeRustup:
    def __init__(self, installer=RUSTUP_INIT, wrong_sum=False):
        self.installer, self.wrong_sum = installer, wrong_sum
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                body, code = b"", 200
                if path == f"/dist/{TRIPLE}/rustup-init":
                    body = fake.installer
                elif path == f"/dist/{TRIPLE}/rustup-init.sha256":
                    sha = "0" * 64 if fake.wrong_sum else hashlib.sha256(fake.installer).hexdigest()
                    body = f"{sha} *rustup-init\n".encode()
                else:
                    code = 404
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv, self.url = serve(H)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ArtBase(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(engines, "this_platform", return_value=LINUX)
        p.start()
        self.cleanups.append(p.stop)

    def github(self, repos, **kw):
        fake = FakeGitHub(repos, **kw)
        self.cleanups.append(fake.close)
        os.environ["AXIS_GITHUB"] = fake.url
        os.environ["AXIS_GITHUB_API"] = fake.url + "/api"
        return fake

    def rustup(self, **kw):
        fake = FakeRustup(**kw)
        self.cleanups.append(fake.close)
        os.environ["AXIS_RUSTUP"] = fake.url
        return fake

    def install(self, app, timeout=30):
        """Run an install the way the page does, and wait for it."""
        out = artcraft.install({"app": app})
        return wait_job(out["job"]["id"], timeout)

    def app(self, app_id):
        return next(a for a in artcraft.status()["apps"] if a["id"] == app_id)


# ------------------------------------------------------------------- choosing the download


class TestChoosing(Base):
    def test_there_are_thirteen_apps(self):
        self.assertEqual(len(artcraft.APPS), 13)
        self.assertEqual(len(set(artcraft.APP_IDS)), 13)
        self.assertIn("photocraft", artcraft.APP_IDS)

    def test_portable_file_for_each_system(self):
        def pick(plat, names, app="photocraft"):
            pattern = artcraft.asset_pattern(app, plat)
            return [n for n in names if pattern and __import__("re").search(pattern, n)]

        names = ["photocraft-0.17.0-windows-x64-portable.zip", "photocraft-0.17.0-windows-arm64-portable.zip",
                 "photocraft-0.17.0-windows-x86-portable.zip", "photocraft-0.17.0-windows-x64.msi",
                 "photocraft-0.17.0-linux-x86_64.tar.gz", "photocraft-0.17.0-linux-aarch64.tar.gz",
                 "photocraft-0.17.0-linux-x86_64.AppImage", "photocraft-0.17.0-linux-x86_64.deb",
                 "photocraft-0.17.0-macos-universal.dmg", "photocraft-cli-0.17.0-macos-universal.zip",
                 "photocraft-web-0.17.0.zip", "SHA256SUMS.txt"]
        self.assertEqual(pick(("windows", "x64"), names), ["photocraft-0.17.0-windows-x64-portable.zip"])
        self.assertEqual(pick(("windows", "arm64"), names), ["photocraft-0.17.0-windows-arm64-portable.zip"])
        self.assertEqual(pick(("windows", "x86"), names), ["photocraft-0.17.0-windows-x86-portable.zip"])
        self.assertEqual(pick(("linux", "x64"), names), ["photocraft-0.17.0-linux-x86_64.tar.gz"])
        self.assertEqual(pick(("linux", "arm64"), names), ["photocraft-0.17.0-linux-aarch64.tar.gz"])
        self.assertEqual(pick(("macos", "arm64"), names), ["photocraft-0.17.0-macos-universal.dmg"])
        self.assertEqual(pick(("macos", "x64"), names), ["photocraft-0.17.0-macos-universal.dmg"])
        self.assertIsNone(artcraft.asset_pattern("photocraft", ("linux", "riscv64")))
        self.assertIsNone(artcraft.asset_pattern("photocraft", ("freebsd", "x64")))

    def test_one_app_never_picks_another_apps_file(self):
        import re
        pattern = artcraft.asset_pattern("gridcraft", ("linux", "x64"))
        self.assertIsNone(re.search(pattern, "photocraft-0.1.0-linux-x86_64.tar.gz"))
        self.assertTrue(re.search(pattern, "gridcraft-0.13.0-linux-x86_64.tar.gz"))

    def test_finds_the_program_on_windows_and_linux(self):
        folder = Path(self.home) / "x"
        (folder / "wordcraft-1.0-windows-x64-portable").mkdir(parents=True)
        for name in ("wordcraft-cli.exe", "wordcraft.exe", "portable.txt"):
            (folder / "wordcraft-1.0-windows-x64-portable" / name).write_bytes(b"x")
        self.assertEqual(artcraft._find_program(folder, "wordcraft", "windows").name, "wordcraft.exe")
        (folder / "t" / "bin").mkdir(parents=True)
        for name in ("wordcraft-cli", "wordcraft"):
            (folder / "t" / "bin" / name).write_bytes(b"x")
        self.assertEqual(artcraft._find_program(folder / "t", "wordcraft", "linux").name, "wordcraft")
        self.assertIsNone(artcraft._find_program(folder / "nothing", "wordcraft", "linux"))

    def test_finds_the_program_inside_a_mac_app(self):
        folder = Path(self.home) / "m"
        macos = folder / "EffectCraft.app" / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (macos / "Other").write_bytes(b"x")
        (macos / "EffectCraft").write_bytes(b"x")
        (folder / "EffectCraft.app" / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "EffectCraft"}))
        self.assertEqual(artcraft._find_program(folder, "effectcraft", "macos").name, "EffectCraft")

    @unittest.skipIf(os.name == "nt", "needs symbolic links")
    def test_disk_image_scan_skips_the_applications_shortcut(self):
        volume = Path(self.home) / "volume"
        (volume / "Real.app").mkdir(parents=True)
        elsewhere = Path(self.home) / "elsewhere" / "System.app"
        elsewhere.mkdir(parents=True)
        (volume / "Applications").symlink_to(elsewhere.parent)
        self.assertEqual([p.name for p in artcraft._apps_in(volume)], ["Real.app"])
        nested = Path(self.home) / "nested"
        (nested / "Disk" / "Inner.app").mkdir(parents=True)
        self.assertEqual([p.name for p in artcraft._apps_in(nested)], ["Inner.app"])


# ------------------------------------------------------------------- installing and opening apps


class TestInstallApp(ArtBase):
    def test_status_before_anything_is_installed(self):
        s = artcraft.status()
        self.assertTrue(s["supported"])
        self.assertEqual(len(s["apps"]), 13)
        self.assertFalse(any(a["installed"] for a in s["apps"]))
        self.assertFalse(s["rust"]["installed"])
        self.assertEqual(s["jobs"], [])
        self.assertEqual(artcraft.rust_env(), {})

    @POSIX_ONLY
    def test_install_unpacks_checks_and_records(self):
        gh = self.github({"storytold/photocraft": app_release("photocraft")})
        job = self.install("photocraft")
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(job.result["model"], "PhotoCraft")
        a = self.app("photocraft")
        self.assertTrue(a["installed"])
        self.assertEqual((a["tag"], a["version"]), ("v1.2.3", "photocraft 1.2.3"))
        program = Path(a["path"])
        self.assertEqual(program, Path(self.home) / "engines" / "artcraft" / "photocraft" / "v1.2.3" / "photocraft-1.2.3-linux-x86_64" / "bin" / "photocraft")
        self.assertTrue(os.access(program, os.X_OK))
        self.assertTrue(os.access(program.with_name("photocraft-cli"), os.X_OK))
        self.assertEqual(gh.downloads, ["photocraft-1.2.3-linux-x86_64.tar.gz"])
        self.assertEqual(list((Path(self.home) / "engines" / "_downloads").glob("*")), [])
        self.assertEqual(list((Path(self.home) / "engines" / "artcraft" / "photocraft").glob("*.partial")), [])
        # Only that app is installed, and the record survives a fresh read.
        self.assertEqual([x["id"] for x in artcraft.status()["apps"] if x["installed"]], ["photocraft"])

    @POSIX_ONLY
    def test_install_reads_release_pages_when_the_api_refuses(self):
        self.github({"storytold/vectorcraft": app_release("vectorcraft", "v0.18.0")}, api=False)
        job = self.install("vectorcraft")
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(self.app("vectorcraft")["tag"], "v0.18.0")

    @POSIX_ONLY
    def test_checksum_can_come_from_the_sha256sums_file(self):
        self.github({"storytold/gridcraft": app_release("gridcraft", sums=True)}, digests=False)
        job = self.install("gridcraft")
        self.assertEqual(job.state, "done", job.error)

    def test_no_published_checksum_means_no_install(self):
        self.github({"storytold/gridcraft": app_release("gridcraft")}, digests=False)
        job = self.install("gridcraft")
        self.assertEqual(job.state, "failed")
        self.assertIn("checksum", job.error)
        self.assertFalse(self.app("gridcraft")["installed"])

    def test_a_damaged_download_is_deleted_and_nothing_is_recorded(self):
        name = "photocraft-1.2.3-linux-x86_64.tar.gz"
        self.github({"storytold/photocraft": app_release("photocraft")}, bad_digest=[name])
        job = self.install("photocraft")
        self.assertEqual(job.state, "failed")
        self.assertIn("checksum", job.error)
        self.assertFalse(self.app("photocraft")["installed"])
        engines_dir = Path(self.home) / "engines"
        self.assertEqual(list((engines_dir / "_downloads").glob("*")), [])
        self.assertEqual(list((engines_dir / "artcraft" / "photocraft").glob("*")), [])

    def test_a_release_without_a_portable_build_says_so(self):
        tag, assets = app_release("photocraft")
        assets = {n: b for n, b in assets.items() if "linux" not in n}
        self.github({"storytold/photocraft": (tag, assets)})
        job = self.install("photocraft")
        self.assertEqual(job.state, "failed")
        self.assertIn("no portable build", job.error)
        self.assertFalse(self.app("photocraft")["installed"])

    @POSIX_ONLY
    def test_an_archive_without_the_program_is_refused(self):
        folder = "photocraft-1.2.3-linux-x86_64"
        tag, assets = app_release("photocraft", extra={f"{folder}.tar.gz": _tar({f"{folder}/share/readme": (b"x", 0o644)})})
        self.github({"storytold/photocraft": (tag, assets)})
        job = self.install("photocraft")
        self.assertEqual(job.state, "failed")
        self.assertIn("not in the PhotoCraft download", job.error)
        self.assertEqual(list((Path(self.home) / "engines" / "artcraft" / "photocraft").glob("*")), [])

    @POSIX_ONLY
    def test_a_program_that_cannot_load_is_refused(self):
        broken = b"#!/bin/sh\necho './x: /lib/libc.so.6: version GLIBC_2.99 not found' >&2\nexit 1\n"
        self.github({"storytold/photocraft": app_release("photocraft", program=broken)})
        job = self.install("photocraft")
        self.assertEqual(job.state, "failed")
        self.assertIn("does not run on this computer", job.error)
        self.assertFalse(self.app("photocraft")["installed"])

    @POSIX_ONLY
    def test_a_program_that_ignores_version_is_still_installed(self):
        silent = b"#!/bin/sh\nexit 0\n"
        self.github({"storytold/photocraft": app_release("photocraft", program=silent)})
        job = self.install("photocraft")
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(self.app("photocraft")["version"], "")

    @POSIX_ONLY
    def test_an_installed_app_is_never_installed_over(self):
        self.github({"storytold/photocraft": app_release("photocraft")})
        self.assertEqual(self.install("photocraft").state, "done")
        settings = Path(self.app("photocraft")["path"]).parent.parent / "PhotoCraftData" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("mine")
        with self.assertRaisesRegex(artcraft.ArtCraftError, "already installed"):
            artcraft.install({"app": "photocraft"})
        self.assertEqual(settings.read_text(), "mine")

    @POSIX_ONLY
    def test_a_folder_without_a_record_is_kept_not_deleted(self):
        self.github({"storytold/photocraft": app_release("photocraft")})
        old = Path(self.home) / "engines" / "artcraft" / "photocraft" / "v1.2.3"
        old.mkdir(parents=True)
        (old / "settings.json").write_text("mine")
        job = self.install("photocraft")
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual((old / "settings.json").read_text(), "mine")
        self.assertNotEqual(Path(self.app("photocraft")["path"]).parts[-5], "v1.2.3")

    def test_unknown_and_missing_app_names(self):
        with self.assertRaisesRegex(artcraft.ArtCraftError, "not one of the Craft apps"):
            artcraft.install({"app": "paintcraft"})
        with self.assertRaisesRegex(artcraft.ArtCraftError, "Say which app"):
            artcraft.install({})
        with self.assertRaisesRegex(artcraft.ArtCraftError, "Say which app"):
            artcraft.open_app({"app": 5})

    def test_a_system_without_portable_builds_is_told_so(self):
        with mock.patch.object(engines, "this_platform", return_value=("freebsd", "x64")):
            self.assertFalse(artcraft.status()["supported"])
            with self.assertRaisesRegex(artcraft.ArtCraftError, "no ready-built program"):
                artcraft.install({"app": "photocraft"})

    @POSIX_ONLY
    def test_two_installs_of_the_same_app_cannot_run_together(self):
        self.github({"storytold/photocraft": app_release("photocraft")})
        first = artcraft.submit("photocraft")
        with self.assertRaises(jobs.JobError):
            artcraft.submit("photocraft")
        jobs.wait(first)

    @POSIX_ONLY
    def test_cancelling_leaves_nothing_behind(self):
        gh = self.github({"storytold/photocraft": app_release("photocraft")})
        job = artcraft.submit("photocraft")
        job._cancel.set()
        jobs.wait(job)
        self.assertEqual(job.state, "cancelled")
        self.assertFalse(self.app("photocraft")["installed"])
        self.assertEqual(list((Path(self.home) / "engines" / "artcraft" / "photocraft").glob("*")), [])
        del gh


class TestOpen(ArtBase):
    def test_opening_something_not_installed_is_refused(self):
        with self.assertRaisesRegex(artcraft.ArtCraftError, "not installed"):
            artcraft.open_app({"app": "photocraft"})

    def wait_for(self, path, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline and not path.exists():
            time.sleep(0.05)
        self.assertTrue(path.exists(), f"{path} never appeared")
        time.sleep(0.1)
        return path.read_text()

    @POSIX_ONLY
    def test_open_starts_the_installed_program_in_its_own_folder(self):
        probe = b'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "x 1.0"; exit 0; fi\npwd > opened.txt\nenv > env.txt\n'
        self.github({"storytold/photocraft": app_release("photocraft", program=probe)})
        self.assertEqual(self.install("photocraft").state, "done")
        program = Path(self.app("photocraft")["path"])
        self.assertEqual(artcraft.open_app({"app": "photocraft"}), {"opened": "photocraft"})
        self.assertEqual(Path(self.wait_for(program.parent / "opened.txt").strip()).resolve(), program.parent.resolve())
        env = self.wait_for(program.parent / "env.txt")
        self.assertNotIn("CARGO_HOME=", env)

    @POSIX_ONLY
    def test_an_opened_app_finds_the_portable_rust(self):
        probe = b'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "x 1.0"; exit 0; fi\nenv > env.txt\n'
        self.github({"storytold/photocraft": app_release("photocraft", program=probe)})
        self.rustup()
        self.assertEqual(wait_job(artcraft.install({"app": "rust"})["job"]["id"]).state, "done")
        self.assertEqual(self.install("photocraft").state, "done")
        program = Path(self.app("photocraft")["path"])
        artcraft.open_app({"app": "photocraft"})
        env = dict(line.split("=", 1) for line in self.wait_for(program.parent / "env.txt").splitlines() if "=" in line)
        home = Path(self.home) / "engines" / "rust"
        self.assertEqual(Path(env["CARGO_HOME"]), home / "cargo")
        self.assertEqual(Path(env["RUSTUP_HOME"]), home / "rustup")
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(home / "cargo" / "bin"))

    def test_an_app_that_is_still_installing_cannot_be_opened_twice_over(self):
        self.github({"storytold/photocraft": app_release("photocraft")})
        artcraft._remember("photocraft", {"tag": "v1", "dir": "artcraft/photocraft/v1", "program": "artcraft/photocraft/v1/p"})
        p = Path(self.home) / "engines" / "artcraft" / "photocraft" / "v1" / "p"
        p.parent.mkdir(parents=True)
        p.write_text("x")
        with mock.patch.object(jobs, "busy", return_value=object()):
            with self.assertRaisesRegex(artcraft.ArtCraftError, "still being installed"):
                artcraft.open_app({"app": "photocraft"})

    def test_a_record_whose_files_are_gone_counts_as_not_installed(self):
        artcraft._remember("photocraft", {"tag": "v1", "dir": "artcraft/photocraft/v1", "program": "artcraft/photocraft/v1/p"})
        self.assertFalse(self.app("photocraft")["installed"])
        artcraft._remember("figcraft", {"tag": "v1", "dir": "x", "program": "../../outside"})
        self.assertFalse(self.app("figcraft")["installed"])


# ------------------------------------------------------------------- Rust


class TestRust(ArtBase):
    def test_windows_vcvarsall_is_called_and_the_axis_install_is_found(self):
        vcvars = Path(self.home) / "engines" / "msvc" / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
        vcvars.parent.mkdir(parents=True)
        vcvars.write_text("", encoding="utf-8")
        engine_root = Path(self.home) / "engines"
        # cmd.exe's `set` prints names the way they were first written, so the search path is "Path", not "PATH"
        result = mock.Mock(returncode=0, stdout="[vcvarsall.bat] Environment initialized\nPath=C:\\msvc\\bin\n"
                                                "VCToolsInstallDir=C:\\msvc\\\n")
        # Path is this system's own kind (the test also runs on Windows, where PosixPath cannot be made), and
        # no real Visual Studio Installer is looked for: the folders named here do not exist.
        nowhere = str(Path(self.home) / "no-program-files")
        native = type(Path())      # looked up before os.name is changed, which would pick the wrong kind
        with mock.patch.object(artcraft.os, "name", "nt"), mock.patch.object(artcraft, "Path", native), \
                mock.patch.dict(os.environ, {"ProgramFiles(x86)": nowhere, "ProgramFiles": nowhere}), \
                mock.patch.object(artcraft.engines, "root", return_value=engine_root), \
                mock.patch.object(artcraft.subprocess, "run", return_value=result) as run:
            env = artcraft._vcvars_env()
            again = artcraft._vcvars_env()
        self.assertEqual(env["VCTOOLSINSTALLDIR"], "C:\\msvc\\")     # names are upper-case, like os.environ on Windows
        self.assertEqual(env["PATH"], "C:\\msvc\\bin")
        self.assertEqual(again, env)
        self.assertEqual(run.call_count, 1)     # a found environment is kept, not looked for again
        # cmd.exe gets one finished command line: a list would reach it with backslash-escaped quotes
        command = run.call_args.args[0]
        self.assertIsInstance(command, str)
        self.assertNotIn('\\"', command)
        self.assertIn(" /s /c ", command)
        self.assertIn('call "', command)
        self.assertIn("vcvarsall.bat", command)
        self.assertIn("amd64", command)

    @POSIX_ONLY
    def test_a_cpp_tools_failure_keeps_rust_installed(self):
        self.rustup()
        with mock.patch.object(artcraft, "_install_cpp_tools", side_effect=artcraft.ArtCraftError("no admin")):
            job = wait_job(artcraft.install({"app": "rust"})["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("Rust is installed", job.error)
        self.assertTrue(artcraft.status()["rust"]["installed"])

    @POSIX_ONLY
    def test_install_rust_into_its_own_folder_without_touching_the_path(self):
        path_before = os.environ["PATH"]
        self.rustup()
        job = wait_job(artcraft.install({"app": "rust"})["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        home = Path(self.home) / "engines" / "rust"
        self.assertEqual((home / "cargo" / "args.txt").read_text().split(),
                         ["-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "stable"])
        self.assertEqual(Path((home / "cargo" / "rustup_home.txt").read_text().strip()), home / "rustup")
        s = artcraft.status()["rust"]
        self.assertTrue(s["installed"])
        self.assertEqual(s["version"], "cargo 9.9.9 (fake)")
        self.assertEqual(os.environ["PATH"], path_before)
        env = artcraft.rust_env()
        self.assertEqual(set(env), {"RUSTUP_HOME", "CARGO_HOME", "PATH"})
        self.assertTrue(env["PATH"].startswith(str(home / "cargo" / "bin") + os.pathsep))
        self.assertEqual(list((Path(self.home) / "engines" / "_downloads").glob("*")), [])
        with self.assertRaisesRegex(artcraft.ArtCraftError, "already installed"):
            artcraft.install({"app": "rust"})

    @POSIX_ONLY
    def test_rust_is_not_installed_when_the_checksum_is_wrong(self):
        self.rustup(wrong_sum=True)
        job = wait_job(artcraft.install({"app": "rust"})["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("checksum", job.error)
        self.assertFalse(artcraft.status()["rust"]["installed"])
        self.assertFalse((Path(self.home) / "engines" / "rust").exists())

    @POSIX_ONLY
    def test_a_failing_rustup_reports_what_it_said_and_cleans_up(self):
        self.rustup(installer=b"#!/bin/sh\necho 'error: could not download component rustc'\nmkdir -p \"$CARGO_HOME/bin\"\nexit 1\n")
        job = wait_job(artcraft.install({"app": "rust"})["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("exit code 1", job.error)
        self.assertIn("could not download component rustc", job.error)
        self.assertFalse(artcraft.status()["rust"]["installed"])
        self.assertFalse((Path(self.home) / "engines" / "rust").exists())

    @POSIX_ONLY
    def test_rustup_that_finishes_without_cargo_is_refused(self):
        self.rustup(installer=b"#!/bin/sh\necho done\n")
        job = wait_job(artcraft.install({"app": "rust"})["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("cargo is not where it should be", job.error)
        self.assertFalse((Path(self.home) / "engines" / "rust").exists())

    @POSIX_ONLY
    def test_cancelling_rust_stops_rustup_and_removes_what_it_made(self):
        self.rustup(installer=b"#!/bin/sh\necho 'info: downloading component rustc'\nmkdir -p \"$CARGO_HOME/bin\"\nexec sleep 60\n")
        job = artcraft.submit("rust")
        deadline = time.time() + 15
        while time.time() < deadline and "downloading" not in job.phase:
            time.sleep(0.05)
        self.assertIn("downloading component rustc", job.phase)   # the page shows what rustup says
        job._cancel.set()
        jobs.wait(job, 15)
        self.assertEqual(job.state, "cancelled")
        self.assertFalse((Path(self.home) / "engines" / "rust").exists())
        self.assertFalse(artcraft.status()["rust"]["installed"])

    def test_rust_for_a_system_rustup_does_not_cover(self):
        with mock.patch.object(engines, "this_platform", return_value=("freebsd", "x64")):
            self.assertFalse(artcraft.status()["rust"]["supported"])
            job = wait_job(artcraft.install({"app": "rust"})["job"]["id"])
            self.assertEqual(job.state, "failed")
            self.assertIn("rustup has no program", job.error)

    def test_every_supported_system_has_a_rustup_triple(self):
        for plat in [("windows", "x64"), ("windows", "arm64"), ("windows", "x86"), ("linux", "x64"), ("linux", "arm64"),
                     ("macos", "x64"), ("macos", "arm64")]:
            self.assertIn(plat, artcraft.RUSTUP_TRIPLES)
        self.assertTrue(artcraft.RUSTUP_TRIPLES[("windows", "x64")].endswith("windows-msvc"))


# ------------------------------------------------------------------- the routes


class TestRoutes(ArtBase):
    def test_status_install_and_the_models_page(self):
        self.github({"storytold/photocraft": app_release("photocraft")})
        url = self.axis("http://127.0.0.1:9")
        code, body = request(url + "/api/artcraft")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["apps"]), 13)
        self.assertTrue(body["supported"])

        code, body = request(url + "/api/artcraft/install", "POST", {"app": "photocraft"})
        self.assertEqual(code, 202)
        job = body["job"]
        self.assertEqual((job["engine"], job["target"], job["label"]), ("artcraft", "photocraft", "Installing PhotoCraft"))
        # The Art Craft tab lists the job; the Models page does not.
        code, body = request(url + "/api/artcraft")
        self.assertIn(job["id"], [j["id"] for j in body["jobs"]])
        code, manage = request(url + "/api/manage")
        self.assertEqual(code, 200)
        self.assertNotIn(job["id"], [j["id"] for j in manage["jobs"]])
        wait_job(job["id"])

    def test_errors_are_plain_400s(self):
        url = self.axis("http://127.0.0.1:9")
        for path, body in (("install", {}), ("install", {"app": "nope"}), ("open", {"app": "photocraft"}), ("open", {})):
            code, answer = request(url + "/api/artcraft/" + path, "POST", body)
            self.assertEqual(code, 400, (path, body))
            self.assertTrue(answer["error"])
        self.assertEqual(request(url + "/api/artcraft/unknown", "POST", {})[0], 404)

    @POSIX_ONLY
    def test_install_then_open_through_the_routes(self):
        probe = b'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "x 1.0"; exit 0; fi\necho opened > opened.txt\n'
        self.github({"storytold/wordcraft": app_release("wordcraft", program=probe)})
        url = self.axis("http://127.0.0.1:9")
        code, body = request(url + "/api/artcraft/install", "POST", {"app": "wordcraft"})
        self.assertEqual(wait_job(body["job"]["id"]).state, "done")
        code, again = request(url + "/api/artcraft/install", "POST", {"app": "wordcraft"})
        self.assertEqual(code, 400)
        self.assertIn("already installed", again["error"])
        code, opened = request(url + "/api/artcraft/open", "POST", {"app": "wordcraft"})
        self.assertEqual((code, opened), (200, {"opened": "wordcraft"}))
        program = Path(self.app("wordcraft")["path"])
        deadline = time.time() + 10
        while time.time() < deadline and not (program.parent / "opened.txt").exists():
            time.sleep(0.05)
        self.assertTrue((program.parent / "opened.txt").exists())

    def test_the_job_can_be_cancelled_and_cleared_with_the_job_routes(self):
        self.rustup(installer=b"#!/bin/sh\nexec sleep 60\n")
        url = self.axis("http://127.0.0.1:9")
        code, body = request(url + "/api/artcraft/install", "POST", {"app": "rust"})
        job_id = body["job"]["id"]
        self.assertEqual(request(url + f"/api/manage/jobs/{job_id}/cancel", "POST")[0], 200)
        wait_job(job_id, 15)
        self.assertEqual(request(url + f"/api/manage/jobs/{job_id}", "DELETE")[0], 200)
        code, body = request(url + "/api/artcraft")
        self.assertEqual(body["jobs"], [])


# ------------------------------------------------------------------- updating and deleting


class TestUpdateAndDelete(ArtBase):
    def update(self, app, timeout=30):
        return wait_job(artcraft.install({"app": app}, update=True)["job"]["id"], timeout)

    @POSIX_ONLY
    def test_check_update_and_delete_an_app(self):
        gh = self.github({"storytold/photocraft": app_release("photocraft", "v1.2.3")})
        self.assertEqual(self.install("photocraft").state, "done")
        program = Path(self.app("photocraft")["path"])
        data = program.parent / "PhotoCraftData"
        data.mkdir()
        (data / "settings.json").write_text("mine")
        old_root = Path(self.home) / "engines" / "artcraft" / "photocraft" / "v1.2.3"

        s = artcraft.check_updates()
        self.assertFalse(next(a for a in s["apps"] if a["id"] == "photocraft")["check"]["update"])
        self.assertIn("already up to date", self.update("photocraft").error)

        gh.repos["storytold/photocraft"] = app_release("photocraft", "v1.3.0")
        s = artcraft.check_updates()
        check = next(a for a in s["apps"] if a["id"] == "photocraft")["check"]
        self.assertEqual((check["update"], check["latest"]), (True, "v1.3.0"))

        job = self.update("photocraft")
        self.assertEqual(job.state, "done", job.error)
        a = self.app("photocraft")
        self.assertEqual((a["tag"], a["version"]), ("v1.3.0", "photocraft 1.3.0"))
        self.assertIsNone(a["check"])
        self.assertEqual((Path(a["path"]).parent / "PhotoCraftData" / "settings.json").read_text(), "mine")
        self.assertFalse(old_root.exists())

        artcraft.remove({"app": "photocraft"})
        self.assertFalse(self.app("photocraft")["installed"])
        self.assertFalse((Path(self.home) / "engines" / "artcraft" / "photocraft").exists())

    def test_update_of_something_not_installed_is_refused(self):
        with self.assertRaisesRegex(artcraft.ArtCraftError, "not installed"):
            artcraft.install({"app": "photocraft"}, update=True)
        with self.assertRaisesRegex(artcraft.ArtCraftError, "not installed"):
            artcraft.install({"app": "rust"}, update=True)

    @POSIX_ONLY
    def test_rust_update_check_update_and_delete(self):
        self.rustup()
        self.assertEqual(wait_job(artcraft.install({"app": "rust"})["job"]["id"]).state, "done")
        bin_dir = Path(self.home) / "engines" / "rust" / "cargo" / "bin"
        rustup = bin_dir / "rustup"
        rustup.write_text('#!/bin/sh\nif [ "$1" = check ]; then echo "stable - Update available : 1.0 -> 2.0"; fi\n')
        rustup.chmod(0o755)
        self.assertTrue(artcraft.check_updates()["rust"]["check"]["update"])
        rustup.write_text('#!/bin/sh\nif [ "$1" = check ]; then echo "stable - Up to date : 2.0"; fi\n'
                          'if [ "$1" = update ]; then echo updated; printf \'#!/bin/sh\\necho "cargo 10.0.0"\\n\' > "$(dirname "$0")/cargo"; fi\n')
        job = wait_job(artcraft.install({"app": "rust"}, update=True)["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(artcraft.status()["rust"]["version"], "cargo 10.0.0")
        self.assertFalse(artcraft.check_updates()["rust"]["check"]["update"])
        artcraft.remove({"app": "rust"})
        self.assertFalse(artcraft.status()["rust"]["installed"])
        self.assertFalse((Path(self.home) / "engines" / "rust").exists())

    def test_routes(self):
        url = self.axis("http://127.0.0.1:9")
        self.assertEqual(request(url + "/api/artcraft/check", "POST", {})[0], 200)
        self.assertEqual(request(url + "/api/artcraft/update", "POST", {"app": "photocraft"})[0], 400)
        self.assertEqual(request(url + "/api/artcraft/remove", "POST", {})[0], 400)
        self.assertEqual(request(url + "/api/artcraft/remove", "POST", {"app": "photocraft"})[0], 200)


if __name__ == "__main__":
    unittest.main()
