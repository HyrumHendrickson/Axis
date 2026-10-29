"""Tests for installing engines (engines.py) and installing image models by name (catalog.py).

Everything runs against fakes: a fake GitHub (API and release pages) serving small
archives with stand-in programs, and the fake Hugging Face from test_manage.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import unittest
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from axis import catalog, engines, images, jobs, manager

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manage import POSIX_ONLY, Base, request, serve, wait_job

LINUX = ("linux", "x64")


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, (data, mode) in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | mode) << 16
            z.writestr(info, data)
    return buf.getvalue()


PROGRAM = b'#!/bin/sh\necho "version: fake 1.0"\n'
BROKEN = b"#!/bin/sh\necho 'missing libvulkan.so.1' >&2\nexit 127\n"


class FakeGitHub:
    """Releases by repository: {"owner/repo": [(tag, {asset name: bytes})]}, newest first."""

    def __init__(self, repos, api=True, bad_digest=()):
        self.repos, self.api, self.bad_digest = repos, api, set(bad_digest)
        self.downloads = []
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="text/html"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
                if parts[:1] == ["api"]:   # /api/repos/<o>/<r>/releases
                    if not gh.api:
                        return self._send(429, b"{}", "application/json")
                    rels = gh.repos.get("/".join(parts[2:4]), [])
                    out = [{"tag_name": tag, "draft": False, "assets": [
                        {"name": n, "size": len(b), "browser_download_url": f"{base}/{parts[2]}/{parts[3]}/releases/download/{tag}/{n}",
                         "digest": "sha256:" + gh.digest(n, b)} for n, b in assets.items()]} for tag, assets in rels]
                    return self._send(200, json.dumps(out).encode(), "application/json")
                repo = "/".join(parts[:2])
                rels = dict(gh.repos.get(repo, []))
                if parts[2:] == ["releases"]:
                    links = "".join(f'<a href="/{repo}/releases/expanded_assets/{t}">x</a>' for t, _ in gh.repos[repo])
                    return self._send(200, links.encode())
                if parts[2:4] == ["releases", "expanded_assets"]:
                    html = "".join(
                        f'<a href="/{repo}/releases/download/{parts[4]}/{n}">{n}</a>'
                        f'<clipboard-copy aria-label="Copy to clipboard digest for {n}" type="button" '
                        f'value="sha256:{gh.digest(n, b)}">' for n, b in rels.get(parts[4], {}).items())
                    return self._send(200, html.encode())
                if parts[2:4] == ["releases", "download"]:
                    data = rels.get(parts[4], {}).get(parts[5])
                    if data is None:
                        return self._send(404)
                    gh.downloads.append(parts[5])
                    return self._send(200, data, "application/octet-stream")
                return self._send(404)

        self.srv, self.url = serve(H)

    def digest(self, name, data):
        return ("0" * 64) if name in self.bad_digest else hashlib.sha256(data).hexdigest()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class EngineBase(Base):
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

    def run_job(self, work, timeout=30):
        job = jobs.submit("install", "test", str(id(work)), "Test", work)
        return jobs.wait(job, timeout)


def llama_release(tag="b100", program=PROGRAM):
    return (tag, {f"llama-{tag}-bin-ubuntu-x64.tar.gz": _tar({f"llama-{tag}/llama-server": (program, 0o755),
                                                             f"llama-{tag}/libllama.so": (b"lib", 0o755)}),
                  f"llama-{tag}-bin-ubuntu-vulkan-x64.tar.gz": _tar({f"llama-{tag}/llama-server": (BROKEN, 0o755)}),
                  f"llama-{tag}-bin-win-cpu-x64.zip": b"windows"})


def sd_release(tag="master-1-abc"):
    return (tag, {f"sd-master-abc-bin-Linux-Ubuntu-24.04-x86_64.zip": _zip({"sd-cli": (PROGRAM, 0o755),
                                                                           "sd-server": (PROGRAM, 0o755)})})


# ------------------------------------------------------------------- catalog


class TestCatalog(unittest.TestCase):
    def test_image_names(self):
        entry, tag = catalog.image_entry("Z-Image-Turbo")
        self.assertEqual((entry["name"], tag), ("z-image-turbo", "q4"))
        self.assertEqual(catalog.image_entry("zimage:Q8_0")[1], "q8")
        self.assertEqual(catalog.image_entry("sd15")[0]["name"], "sd1.5")
        self.assertEqual(catalog.image_entry("flux:latest")[1], "q4")
        self.assertIsNone(catalog.image_entry("owner/repo"))
        self.assertIsNone(catalog.image_entry("nothing-like-it"))
        with self.assertRaisesRegex(catalog.CatalogError, "z-image-turbo:q3"):
            catalog.image_entry("z-image-turbo:q5")
        with self.assertRaisesRegex(catalog.CatalogError, "only one version"):
            catalog.image_entry("sdxl:q8")

    def test_chat_names(self):
        self.assertEqual(catalog.chat_ref("llama3.1:8b"), "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF:Q4_K_M")
        self.assertEqual(catalog.chat_ref("ollama run llama3.2"), "bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M")
        self.assertIsNone(catalog.chat_ref("bartowski/Tiny-GGUF"))

    def test_pick_prefers_listed_files_then_quantization(self):
        part = {"repo": "o/r", "files": ["m-{TAG}.gguf"], "tagged": True}
        files = [{"path": "m-Q4_0.gguf"}, {"path": "m-Q8_0.gguf"}]
        self.assertEqual(catalog.pick(files, part, "Q8_0")["path"], "m-Q8_0.gguf")
        renamed = [{"path": "new_name-Q4_0.gguf"}, {"path": "new_name-Q8_0.gguf"}]
        self.assertEqual(catalog.pick(renamed, part, "Q4_0")["path"], "new_name-Q4_0.gguf")
        with self.assertRaisesRegex(catalog.CatalogError, "no longer has"):
            catalog.pick([{"path": "other.gguf"}], part, "Q4_0")
        nested = {"repo": "o/r", "files": ["split_files/vae/ae.safetensors"]}
        self.assertEqual(catalog.pick([{"path": "split_files/vae/ae.safetensors"}], nested, "")["path"],
                         "split_files/vae/ae.safetensors")

    def test_every_entry_is_complete(self):
        for e in catalog.IMAGE:
            roles = {p["role"] for p in e["parts"]}
            self.assertTrue(roles & {"checkpoint", "diffusion_model"}, e["name"])
            self.assertTrue(set(roles) <= set(manager.ROLES), e["name"])
            if e.get("tags"):
                self.assertIn(e["default_tag"], e["tags"])


# -------------------------------------------------------------- choosing builds


class TestBuilds(unittest.TestCase):
    def test_preferred_builds(self):
        win = ("windows", "x64")
        self.assertEqual(engines.preferred_builds("llama", win, gpus=["NVIDIA GeForce RTX 4070"]), ["cuda", "vulkan", "cpu"])
        self.assertEqual(engines.preferred_builds("sd", win, gpus=["AMD Radeon RX 7800"]), ["vulkan", "cpu"])
        self.assertEqual(engines.preferred_builds("sd", win, gpus=["Microsoft Basic Display Adapter"]), ["cpu"])
        self.assertEqual(engines.preferred_builds("llama", LINUX, has_vulkan=True), ["vulkan", "cpu"])

    def test_default_build_is_cpu(self):
        self.assertEqual(engines.default_build("llama", ("windows", "x64")), "cpu")
        self.assertEqual(engines.default_build("sd", LINUX), "cpu")
        self.assertEqual(engines.default_build("llama", ("macos", "x64")), "cpu")
        self.assertEqual(engines.default_build("llama", ("macos", "arm64")), "auto")  # Metal only
        self.assertEqual(engines.preferred_builds("sd", ("macos", "arm64")), ["metal"])
        self.assertEqual(engines.preferred_builds("sd", ("linux", "arm64"), has_vulkan=False), [])

    def test_choose_pairs_cuda_runtime_and_skips_empty_releases(self):
        a = lambda n: {"name": n, "url": "u/" + n, "size": 1, "sha256": None}  # noqa: E731
        rels = [{"tag": "v0.8.0", "assets": [a("nightly-tag.txt")]},
                {"tag": "b2", "assets": [a("llama-b2-bin-win-cuda-12.4-x64.zip"), a("llama-b2-bin-win-cuda-13.4-x64.zip"),
                                         a("cudart-llama-bin-win-cuda-12.4-x64.zip"), a("cudart-llama-bin-win-cuda-13.4-x64.zip"),
                                         a("llama-b2-bin-ubuntu-x64.tar.gz"), a("llama-b2-bin-ubuntu-vulkan-x64.tar.gz")]}]
        rel, got = engines.choose("llama", "cuda", rels, ("windows", "x64"))
        self.assertEqual([x["name"] for x in got], ["llama-b2-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"])
        _, got = engines.choose("llama", "cpu", rels, LINUX)
        self.assertEqual(got[0]["name"], "llama-b2-bin-ubuntu-x64.tar.gz")   # not the vulkan one
        with self.assertRaisesRegex(engines.EngineError, "no 'metal' build"):
            engines.choose("llama", "metal", rels, LINUX)
        with self.assertRaisesRegex(engines.EngineError, "no ready-built"):
            engines.choose("sd", "cpu", rels, ("linux", "arm64"))


# ------------------------------------------------------------- installing engines


@POSIX_ONLY
class TestEngineInstall(EngineBase):
    def test_install_from_api_falls_back_from_broken_build(self):
        gh = self.github({"ggml-org/llama.cpp": [("v0.8.0", {}), llama_release()]})
        with mock.patch.object(engines, "preferred_builds", return_value=["vulkan", "cpu"]):
            job = self.run_job(lambda j: engines.install("llama", "auto", j))
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual((job.result["build"], job.result["version"]), ("cpu", "version: fake 1.0"))
        self.assertIn("vulkan", job.result["note"])
        p = engines.program("llama-server")
        self.assertTrue(p and p.is_file() and engines.root() in p.parents)
        self.assertEqual(manager.llama_binary(), p)                    # found without any setting
        self.assertEqual(gh.downloads[-1], "llama-b100-bin-ubuntu-x64.tar.gz")
        self.assertIn("LD_LIBRARY_PATH", engines.run_env(p))
        # Updating replaces the old version.
        gh.repos["ggml-org/llama.cpp"].insert(0, llama_release("b200"))
        self.assertEqual(self.run_job(lambda j: engines.install("llama", "cpu", j)).state, "done")
        self.assertEqual([d.name for d in (engines.root() / "llama").iterdir()], ["b200-cpu"])

    def test_html_fallback_and_checksum(self):
        gh = self.github({"leejet/stable-diffusion.cpp": [sd_release()]}, api=False)
        job = self.run_job(lambda j: engines.install("sd", "cpu", j))
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(images.find_cli(), engines.program("sd-cli"))
        self.assertEqual(manager.sd_server_binary(), engines.program("sd-server"))
        self.assertEqual(images.workdir(), Path(self.home))            # not the engine folder, which moves
        engines.remove("sd")
        self.assertIsNone(engines.program("sd-cli"))
        gh.bad_digest.add("sd-master-abc-bin-Linux-Ubuntu-24.04-x86_64.zip")
        job = self.run_job(lambda j: engines.install("sd", "cpu", j))
        self.assertEqual(job.state, "failed")
        self.assertIn("checksum", job.error)
        self.assertIsNone(engines.installed("sd"))

    def test_unsafe_archive_is_refused(self):
        evil = ("b1", {"llama-b1-bin-ubuntu-x64.tar.gz": _tar({"../escape": (b"x", 0o644)})})
        self.github({"ggml-org/llama.cpp": [evil]})
        job = self.run_job(lambda j: engines.install("llama", "cpu", j))
        self.assertEqual(job.state, "failed")
        self.assertIn("unsafe", job.error)
        self.assertFalse((engines.root() / "llama" / "escape").exists())
        self.assertFalse((Path(self.home) / "engines" / "llama" / "b1-cpu").exists())

    def test_auto_install_once(self):
        self.github({"ggml-org/llama.cpp": [llama_release()], "leejet/stable-diffusion.cpp": [sd_release()]})
        os.environ["AXIS_NO_ENGINE_INSTALL"] = "1"
        self.assertEqual(manager.auto_install_engines(), [])
        os.environ.pop("AXIS_NO_ENGINE_INSTALL")
        with mock.patch.object(engines, "preferred_builds", return_value=["cpu"]):
            started = manager.auto_install_engines()
            self.assertEqual(sorted(j.target for j in started), ["llama", "sd"])
            for j in started:
                self.assertEqual(jobs.wait(j, 30).state, "done", j.error)
            self.assertEqual(manager.auto_install_engines(), [])     # only on the first start
        st = request(self.axis("http://127.0.0.1:1") + "/api/manage")[1]["engines"]
        self.assertTrue(st["llama"]["axis"] and st["sd"]["axis"])
        self.assertEqual(st["llama"]["installed"]["tag"], "b100")

    def test_model_install_installs_engine_first(self):
        self.github({"ggml-org/llama.cpp": [llama_release()]})
        self.hub({"bartowski/Meta-Llama-3.1-8B-Instruct-GGUF": {"Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf": b"GGUF" * 50}})
        url = self.axis("http://127.0.0.1:1")
        with mock.patch.object(engines, "preferred_builds", return_value=["cpu"]):
            code, r = request(url + "/api/manage/install", "POST", {"engine": "llamacpp", "name": "llama3.1:8b"})
            self.assertEqual(code, 202, r)
            job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertIsNotNone(engines.program("llama-server"))
        (m,) = request(url + "/api/manage")[1]["llamacpp"]["models"]
        self.assertEqual(m["name"], "Meta-Llama-3.1-8B-Instruct-Q4_K_M")

    def test_engine_routes(self):
        url = self.axis("http://127.0.0.1:1")
        code, r = request(url + "/api/manage/engines/install", "POST", {"engine": "nope"})
        self.assertEqual(code, 400)
        self.github({"leejet/stable-diffusion.cpp": [sd_release()]})
        code, r = request(url + "/api/manage/engines/install", "POST", {"engine": "sd", "build": "cpu"})
        self.assertEqual(code, 202, r)
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        code, r = request(url + "/api/manage/engines/remove", "POST", {"engine": "sd"})
        self.assertEqual((code, r), (200, {"removed": "sd"}))


# ------------------------------------------------------- image models by name


@POSIX_ONLY
class TestCatalogInstall(EngineBase):
    def setUp(self):
        super().setUp()
        images.save_settings({"sd_server": str(self.fake_program("sd-server"))})

    def zimage_hub(self):
        return self.hub({
            "leejet/Z-Image-Turbo-GGUF": {"z_image_turbo-Q4_0.gguf": b"Z" * 300, "z_image_turbo-Q8_0.gguf": b"8" * 500},
            "unsloth/Qwen3-4B-Instruct-2507-GGUF": {"Qwen3-4B-Instruct-2507-Q4_K_M.gguf": b"Q" * 200,
                                                    "Qwen3-4B-Instruct-2507-Q8_0.gguf": b"q" * 400},
            "Comfy-Org/z_image_turbo": {"split_files/vae/ae.safetensors": b"V" * 100,
                                        "split_files/text_encoders/other.safetensors": b"T"}})

    def test_install_by_name(self):
        self.zimage_hub()
        url = self.axis("http://127.0.0.1:1")
        code, r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "z-image-turbo"})
        self.assertEqual(code, 202, r)
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(job.total, 600)                               # one progress bar for all three files
        m = images.get_model(job.result["id"])
        self.assertEqual(m["name"], "Z-Image Turbo")
        self.assertEqual((m["steps"], m["cfg_scale"], m["width"]), (8, 1.0, 1024))
        self.assertEqual(m["extra_args"], "--flash-attn off,diffusion=on")
        self.assertTrue(m["diffusion_model"].endswith("z_image_turbo-Q4_0.gguf"))
        self.assertTrue(m["llm"].endswith("Qwen3-4B-Instruct-2507-Q4_K_M.gguf"))
        self.assertTrue(m["vae"].endswith("ae.safetensors"))
        self.assertEqual(m["missing"], [])
        self.assertTrue(all(f["managed"] for f in m["files"]))

        # Installing it again finds the same model; the q8 version is a second model sharing the parts.
        r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "z-image-turbo"})[1]
        self.assertEqual(wait_job(r["job"]["id"]).result["id"], m["id"])
        r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "z-image-turbo:q8"})[1]
        q8 = images.get_model(wait_job(r["job"]["id"]).result["id"])
        self.assertEqual(q8["name"], "Z-Image Turbo (Q8_0)")
        self.assertEqual((q8["vae"], q8["llm"]), (m["vae"], m["llm"]))
        self.assertEqual(len(images.list_models()), 2)

    def test_errors_are_reported_before_downloading(self):
        self.hub({"leejet/Z-Image-Turbo-GGUF": {"z_image_turbo-Q4_0.gguf": b"Z"}})   # the other repositories are missing
        url = self.axis("http://127.0.0.1:1")
        code, r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "z-image-turbo"})
        self.assertEqual(code, 400)
        self.assertIn("unsloth/Qwen3-4B-Instruct-2507-GGUF", r["error"])
        code, r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "sd9"})
        self.assertEqual(code, 400)
        self.assertIn("z-image-turbo", r["error"])
        self.assertEqual(images.list_models(), [])
        self.assertFalse((Path(self.home) / "models" / "image").exists())

    def test_cancel_removes_partial_files(self):
        self.hub({"leejet/Z-Image-Turbo-GGUF": {"z_image_turbo-Q4_0.gguf": os.urandom(6 * 1024 * 1024)},
                  "unsloth/Qwen3-4B-Instruct-2507-GGUF": {"Qwen3-4B-Instruct-2507-Q4_K_M.gguf": b"Q"},
                  "Comfy-Org/z_image_turbo": {"split_files/vae/ae.safetensors": b"V"}})
        entry, tag = catalog.image_entry("z-image-turbo")
        plan = manager.catalog_plan(entry, tag)
        job = jobs.submit("install", "image", "x", "x", lambda j: (j._cancel.set(), manager.catalog_image_install(entry, tag, plan, j)))
        self.assertEqual(jobs.wait(job, 20).state, "cancelled")
        root = Path(self.home) / "models" / "image"
        self.assertEqual([p for p in root.rglob("*.part*")] if root.exists() else [], [])
        self.assertEqual(images.list_models(), [])

    def test_installs_engine_when_missing(self):
        images.save_settings({"sd_server": ""})
        self.github({"leejet/stable-diffusion.cpp": [sd_release()]})
        self.hub({"stable-diffusion-v1-5/stable-diffusion-v1-5": {"v1-5-pruned-emaonly-fp16.safetensors": b"S" * 64,
                                                                  "v1-5-pruned-emaonly.safetensors": b"s" * 128}})
        url = self.axis("http://127.0.0.1:1")
        with mock.patch.object(engines, "preferred_builds", return_value=["cpu"]):
            r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "sd1.5"})[1]
            job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertIsNotNone(images.find_cli())
        m = images.get_model(job.result["id"])
        self.assertTrue(m["model"].endswith("v1-5-pruned-emaonly-fp16.safetensors"))
        self.assertEqual((m["sampler"], m["width"]), ("euler_a", 512))


if __name__ == "__main__":
    unittest.main()
