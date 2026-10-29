"""Tests for installing, starting, stopping and deleting models.

Everything runs against fakes: a fake Ollama, a fake Hugging Face, and a small
Python program that stands in for llama-server and sd-server.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from axis import backend, hub, images, jobs, manager, procs, server

POSIX_ONLY = unittest.skipIf(os.name == "nt", "uses a shell wrapper for the fake servers")


def request(url, method="GET", body=None, headers=None):
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


class QuickServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the getfqdn() reverse-DNS lookup, which stalls for seconds on macOS."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]


def serve(handler):
    srv = QuickServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def wait_job(job_id, timeout=30):
    job = jobs.get(job_id)
    assert job is not None, job_id
    jobs.wait(job, timeout)
    return job


# ------------------------------------------------------------- fake Hugging Face


class FakeHub:
    """Files by repository: {"owner/repo": {"path/in/repo": bytes}}."""

    def __init__(self, repos, gated=()):
        self.repos = repos
        self.gated = set(gated)
        self.auth_seen = []        # Authorization headers seen on downloads (None when absent)
        self.ranges = []
        self.corrupt = set()       # paths whose bytes are changed after the checksum is published
        hubref = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
                if parts[:2] == ["api", "models"]:
                    repo = "/".join(parts[2:4])
                    if repo in hubref.gated and not self.headers.get("Authorization"):
                        return self._send(401, b'{"error":"gated"}')
                    files = hubref.repos.get(repo)
                    if files is None:
                        return self._send(401, b'{"error":"Repository not found"}')
                    sib = [{"rfilename": p, "size": len(b),
                            "lfs": {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}} for p, b in files.items()]
                    return self._send(200, json.dumps({"id": repo, "siblings": sib}).encode())
                if len(parts) >= 5 and parts[2] == "resolve":
                    repo, path = "/".join(parts[:2]), "/".join(parts[4:])
                    data = (hubref.repos.get(repo) or {}).get(path)
                    if data is None:
                        return self._send(404)
                    if "redirect" not in url.query and self.headers.get("Host", "").startswith("127.0.0.1"):
                        # Like the real site: send the download to another host (the CDN).
                        port = self.server.server_address[1]
                        return self._send(302, extra={"Location": f"http://localhost:{port}{url.path}?redirect=1"})
                    hubref.auth_seen.append(self.headers.get("Authorization"))
                    if path in hubref.corrupt:
                        data = b"X" + data[1:]
                    rng = self.headers.get("Range")
                    hubref.ranges.append(rng)
                    if rng:
                        start = int(rng.split("=")[1].split("-")[0])
                        if start >= len(data):
                            return self._send(416)
                        return self._send(206, data[start:], "application/octet-stream",
                                          {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
                    return self._send(200, data, "application/octet-stream")
                return self._send(404)

        self.srv, self.url = serve(H)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# ------------------------------------------------------------------ fake Ollama


class FakeOllama:
    def __init__(self, installed=(), loaded=()):
        self.installed = list(installed)
        self.loaded = list(loaded)
        self.calls = []
        ref = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

            def do_GET(self):
                if self.path == "/api/version":
                    return self._json(200, {"version": "0.12.0"})
                if self.path == "/api/ps":
                    return self._json(200, {"models": [{"name": m, "model": m} for m in ref.loaded]})
                if self.path == "/api/tags":
                    return self._json(200, {"models": [{"name": m, "model": m, "size": 100} for m in ref.installed]})
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                body = self._body()
                ref.calls.append((self.path, body))
                if self.path == "/api/pull":
                    name = body["model"]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    if name.startswith("missing"):
                        self.wfile.write(b'{"error":"pull model manifest: file does not exist"}\n')
                        return
                    for line in ({"status": "pulling manifest"},
                                 {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 500},
                                 {"status": "pulling abc", "digest": "sha256:abc", "total": 1000, "completed": 1000},
                                 {"status": "verifying sha256 digest"}, {"status": "success"}):
                        self.wfile.write(json.dumps(line).encode() + b"\n")
                    if name not in ref.installed:
                        ref.installed.append(name)
                    return
                if self.path == "/api/generate":
                    name = body["model"]
                    if name not in ref.installed:
                        return self._json(404, {"error": f"model '{name}' not found"})
                    if body.get("keep_alive") == 0:
                        if name in ref.loaded:
                            ref.loaded.remove(name)
                    elif name not in ref.loaded:
                        ref.loaded.append(name)
                    return self._json(200, {"model": name, "done": True})
                return self._json(404, {"error": "not found"})

            def do_DELETE(self):
                body = self._body()
                ref.calls.append((self.path, body))
                if body.get("model") not in ref.installed:
                    return self._json(404, {"error": "model not found"})
                ref.installed.remove(body["model"])
                return self._json(200, {})

        self.srv, self.url = serve(H)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# ------------------------------------------- fake llama-server and sd-server

FAKE_SERVER = r'''
import base64, json, os, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

args = sys.argv[1:]
def opt(*names, default=None):
    for n in names:
        if n in args:
            return args[args.index(n) + 1]
    return default

log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_FAIL"):
    print("error: failed to load model: out of memory", flush=True)
    sys.exit(1)
sd = "--listen-port" in args
port = int(opt("--listen-port", "--port"))
alias = opt("--alias", default="model")
print("loading model " + str(opt("-m", "--diffusion-model", "--model")), flush=True)
time.sleep(float(os.environ.get("FAKE_DELAY", "0.3")))
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
JOBS = {}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": return self._json(200, {"status": "ok"})
        if self.path == "/v1/models": return self._json(200, {"data": [{"id": alias}]})
        if self.path == "/sdcpp/v1/models/status": return self._json(200, {"state": "ready"})
        if self.path.startswith("/sdcpp/v1/jobs/"):
            jid = self.path.split("/")[4]
            j = JOBS.get(jid)
            if not j: return self._json(404, {"error": "no job"})
            j["polls"] += 1
            if j["polls"] < 3:
                return self._json(200, {"id": jid, "status": "generating",
                                        "preview": {"pass": 1, "step": j["polls"], "total_steps": j["steps"], "b64_json": PNG}})
            return self._json(200, {"id": jid, "status": "completed", "result": {"images": [{"index": 0, "b64_json": PNG}]}})
        return self._json(404, {"error": "not found"})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/sdcpp/v1/img_gen":
            jid = "job_%d" % (len(JOBS) + 1)
            JOBS[jid] = {"polls": 0, "steps": body["sample_params"]["sample_steps"]}
            with open(os.environ.get("FAKE_REQ", os.devnull), "w") as f: f.write(json.dumps(body))
            return self._json(202, {"id": jid, "status": "queued"})
        return self._json(404, {"error": "not found"})

import socketserver
class Quick(ThreadingHTTPServer):
    def server_bind(self):   # skip getfqdn(): a reverse-DNS lookup that stalls for seconds on macOS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]
Quick(("127.0.0.1", port), H).serve_forever()
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"AXIS_HOME": self.home, "HF_TOKEN": "", "OLLAMA_HOST": "",
                                                "AXIS_LLAMA_SERVER": "", "AXIS_SD_SERVER": "", "AXIS_SD_CLI": "",
                                                "AXIS_NO_ENGINE_INSTALL": "1", "AXIS_GITHUB": "http://127.0.0.1:9",
                                                "AXIS_GITHUB_API": "http://127.0.0.1:9"})
        self.env.start()
        for key in ("AXIS_URL", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            os.environ.pop(key, None)
        os.environ["HF_HOME"] = str(Path(self.home) / "hf")   # no token file from the machine running the tests
        procs.reset_for_tests()
        jobs.reset_for_tests()
        self.cleanups = []

    def tearDown(self):
        procs.reset_for_tests()
        jobs.reset_for_tests()
        for c in self.cleanups:
            c()
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def fake_program(self, name):
        script = Path(self.home) / "fake_server.py"
        script.write_text(FAKE_SERVER)
        prog = Path(self.home) / "bin" / name
        prog.parent.mkdir(parents=True, exist_ok=True)
        prog.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        prog.chmod(0o755)
        return prog

    def hub(self, repos, gated=()):
        fake = FakeHub(repos, gated)
        self.cleanups.append(fake.close)
        os.environ["HF_ENDPOINT"] = fake.url
        return fake

    def ollama(self, installed=(), loaded=()):
        fake = FakeOllama(installed, loaded)
        self.cleanups.append(fake.close)
        return fake

    def axis(self, model_url):
        srv = server.make_server(0, model_url)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.cleanups.append(lambda: (srv.shutdown(), srv.server_close()))
        return srv.url


# ------------------------------------------------------------------- names


class TestNames(Base):
    def test_hub_parse(self):
        r = hub.parse("bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M")
        self.assertEqual((r.repo, r.tag, r.path, r.revision), ("bartowski/Llama-3.2-3B-Instruct-GGUF", "Q4_K_M", "", "main"))
        r = hub.parse("https://huggingface.co/Comfy-Org/z_image_turbo/blob/main/split_files/vae/ae.safetensors")
        self.assertEqual((r.repo, r.path), ("Comfy-Org/z_image_turbo", "split_files/vae/ae.safetensors"))
        r = hub.parse("https://huggingface.co/leejet/Z-Image-Turbo-GGUF/tree/main")
        self.assertEqual((r.repo, r.path), ("leejet/Z-Image-Turbo-GGUF", ""))
        self.assertEqual(hub.parse("hf.co/a/b").repo, "a/b")
        self.assertEqual(hub.parse("llama-server -hf a/b:Q8_0").tag, "Q8_0")
        self.assertEqual(hub.parse("a/b/sub/f.gguf").path, "sub/f.gguf")
        for bad in ("", "justaname", "a/b/../c", "https://example.com/a/b", "a/b:bad tag", "-x/y",
                    "https://huggingface.co/datasets/a/b"):
            with self.assertRaises(hub.HubError, msg=bad):
                hub.parse(bad)

    def test_ollama_names(self):
        self.assertEqual(manager.ollama_name("llama3.1:8b"), "llama3.1:8b")
        self.assertEqual(manager.ollama_name("ollama run qwen3:4b --keepalive -1m"), "qwen3:4b")
        self.assertEqual(manager.ollama_name("https://ollama.com/library/gemma3:4b"), "gemma3:4b")
        self.assertEqual(manager.ollama_name("https://ollama.com/someone/model"), "someone/model")
        self.assertEqual(manager.ollama_name("https://huggingface.co/bartowski/x-GGUF"), "hf.co/bartowski/x-GGUF")
        self.assertEqual(manager.ollama_name("huggingface.co/a/b:Q4_K_M"), "hf.co/a/b:Q4_K_M")
        for bad in ("", "../x", "a b", "https://example.com/x", "https://ollama.com/search", "x:"):
            with self.assertRaises(manager.ManageError, msg=bad):
                manager.ollama_name(bad)

    def test_pick_gguf(self):
        f = lambda *names: [{"path": n, "size": 10, "sha256": None} for n in names]  # noqa: E731
        ref = hub.parse("a/b")
        files = f("m-Q8_0.gguf", "m-Q4_K_M.gguf", "mmproj-m-f16.gguf", "README.md")
        self.assertEqual(hub.pick_gguf(files, ref)[0]["path"], "m-Q4_K_M.gguf")     # the default
        self.assertEqual(hub.pick_gguf(files, hub.parse("a/b:q8_0"))[0]["path"], "m-Q8_0.gguf")
        with self.assertRaises(hub.NeedChoice) as ctx:
            hub.pick_gguf(f("m-Q8_0.gguf", "m-Q6_K.gguf"), ref)
        self.assertEqual(len(ctx.exception.options), 2)
        with self.assertRaises(hub.NeedChoice):                                       # matches both
            hub.pick_gguf(f("m-Q4_K_M.gguf", "m-Q4_K_S.gguf"), hub.parse("a/b:Q4_K"))
        self.assertEqual(hub.pick_gguf(f("m-Q4_K_M.gguf", "m-Q4_K_S.gguf"), hub.parse("a/b:Q4_K_S"))[0]["path"], "m-Q4_K_S.gguf")
        split = f("big/m-Q4_K_M-00001-of-00002.gguf", "big/m-Q4_K_M-00002-of-00002.gguf")
        parts = hub.pick_gguf(split, ref)
        self.assertEqual([p["path"] for p in parts], [s["path"] for s in split])
        with self.assertRaisesRegex(hub.HubError, "missing"):
            hub.pick_gguf(split[:1], ref)
        with self.assertRaisesRegex(hub.HubError, "no GGUF"):
            hub.pick_gguf(f("model.safetensors"), ref)
        self.assertEqual(hub.pick_gguf(files, ref, choice="m-Q8_0.gguf")[0]["path"], "m-Q8_0.gguf")

    def test_pick_mmproj(self):
        f = lambda *names: [{"path": n, "size": 10, "sha256": None} for n in names]  # noqa: E731
        files = f("m-Q4_K_M.gguf", "mmproj-m-Q8_0.gguf", "mmproj-m-f16.gguf", "mmproj-m-bf16.gguf", "sub/mmproj-other-f16.gguf")
        self.assertEqual(hub.pick_mmproj(files, "m-Q4_K_M.gguf")["path"], "mmproj-m-f16.gguf")
        self.assertEqual(hub.pick_mmproj(files, "sub/x.gguf")["path"], "sub/mmproj-other-f16.gguf")
        self.assertIsNone(hub.pick_mmproj(f("m-Q4_K_M.gguf"), "m-Q4_K_M.gguf"))
        self.assertEqual(hub.pick_mmproj(f("mmproj-q8_0.gguf", "mmproj-F32.gguf"), "m.gguf")["path"], "mmproj-q8_0.gguf")

    def test_pick_image_file(self):
        files = [{"path": p, "size": 1, "sha256": None} for p in
                 ("v1-5.safetensors", "unet/diffusion_pytorch_model.safetensors", "README.md")]
        with self.assertRaises(hub.NeedChoice) as ctx:
            hub.pick_image_file(files, hub.parse("a/b"))
        self.assertEqual(ctx.exception.options[0]["file"], "v1-5.safetensors")  # top-level files first
        self.assertEqual(hub.pick_image_file(files, hub.parse("a/b/unet"))["path"], "unet/diffusion_pytorch_model.safetensors")
        with self.assertRaisesRegex(hub.HubError, "not a model file"):
            hub.pick_image_file(files, hub.parse("a/b/README.md"))


# --------------------------------------------------------------- downloads


class _Job:
    def __init__(self):
        self.cancel = False
        self.done = None

    def check(self):
        if self.cancel:
            raise jobs.Cancelled("Cancelled.")

    def progress(self, phase=None, done=None, total=None):
        if done is not None:
            self.done = done


class TestDownloads(Base):
    def test_download_verifies_resumes_and_strips_token_on_redirect(self):
        data = os.urandom(3 * 1024 * 1024 + 17)
        fake = self.hub({"o/r": {"sub/m.gguf": data}})
        os.environ["HF_TOKEN"] = "secret"
        ref = hub.parse("o/r")
        files = hub.list_files(ref)
        self.assertEqual(files[0]["sha256"], hashlib.sha256(data).hexdigest())
        root = Path(self.home) / "models"
        dest = hub.safe_dest(root, "o/r", "sub/m.gguf")
        dest.parent.mkdir(parents=True)
        # A half-finished earlier download of the same file is resumed, not restarted.
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(data[:1000])
        dest.with_name(dest.name + ".part.json").write_text(json.dumps({"size": len(data), "sha256": files[0]["sha256"]}))
        out = hub.download_all(ref, files, root, _Job())
        self.assertEqual(out[0].read_bytes(), data)
        self.assertEqual(fake.ranges[-1], "bytes=1000-")
        self.assertEqual(fake.auth_seen, [None])  # the token never reached the other host
        self.assertFalse(part.exists())

    def test_bad_checksum_is_deleted(self):
        fake = self.hub({"o/r": {"m.gguf": b"abcdef" * 1000}})
        fake.corrupt.add("m.gguf")
        ref = hub.parse("o/r")
        root = Path(self.home) / "models"
        with self.assertRaisesRegex(hub.HubError, "checksum"):
            hub.download_all(ref, hub.list_files(ref), root, _Job())
        self.assertEqual(list((root / "o__r").iterdir()), [])

    def test_missing_and_gated(self):
        self.hub({"o/gated": {"m.gguf": b"x"}}, gated=["o/gated"])
        with self.assertRaisesRegex(hub.HubError, "gated"):
            hub.list_files(hub.parse("o/gated"))
        with self.assertRaisesRegex(hub.HubError, "No Hugging Face model named"):
            hub.list_files(hub.parse("o/nothing"))
        os.environ["HF_TOKEN"] = "t"
        self.assertEqual(len(hub.list_files(hub.parse("o/gated"))), 1)

    def test_cancel(self):
        self.hub({"o/r": {"m.gguf": os.urandom(4 * 1024 * 1024)}})
        job = _Job()
        job.cancel = True
        with self.assertRaises(jobs.Cancelled):
            hub.download_all(hub.parse("o/r"), hub.list_files(hub.parse("o/r")), Path(self.home) / "m", job)


# ------------------------------------------------------------------ Ollama


class TestOllama(Base):
    def test_shutdown_unloads_every_model(self):
        fake = self.ollama(installed=["a:1b", "b:1b"], loaded=["a:1b", "b:1b"])  # b was not loaded by Axis
        unloaded = manager.shutdown(fake.url)
        self.assertEqual(sorted(unloaded), ["a:1b", "b:1b"])
        self.assertEqual(fake.loaded, [])
        self.assertTrue(manager.ollama_version(fake.url))      # Ollama itself keeps running

    def test_install_start_stop_delete(self):
        fake = self.ollama(installed=["llama3.2:3b"])
        url = self.axis(fake.url)
        code, r = request(url + "/api/manage/install", "POST", {"engine": "ollama", "name": "ollama run qwen3:4b"})
        self.assertEqual(code, 202, r)
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual((job.done, job.total), (1000, 1000))
        self.assertIn("qwen3:4b", fake.installed)

        code, r = request(url + "/api/manage/start", "POST", {"engine": "ollama", "model": "qwen3:4b"})
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        load = [b for p, b in fake.calls if p == "/api/generate"][-1]
        self.assertEqual((load["keep_alive"], load["options"]["num_ctx"]), (-1, backend.NUM_CTX))
        self.assertEqual(request(url + "/api/status")[1]["models"], ["qwen3:4b"])

        code, r = request(url + "/api/manage/stop", "POST", {"engine": "ollama", "model": "qwen3:4b"})
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        self.assertEqual(fake.loaded, [])

        fake.loaded.append("llama3.2:3b")
        code, r = request(url + "/api/manage/delete", "POST", {"engine": "ollama", "model": "llama3.2:3b"})
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        self.assertNotIn("llama3.2:3b", fake.installed)
        self.assertNotIn("llama3.2:3b", fake.loaded)            # unloaded before deleting

    def test_errors(self):
        fake = self.ollama()
        url = self.axis(fake.url)
        code, r = request(url + "/api/manage/install", "POST", {"engine": "ollama", "name": "missing-model"})
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("ollama.com/library", job.error)
        code, r = request(url + "/api/manage/start", "POST", {"engine": "ollama", "model": "nope"})
        job = wait_job(r["job"]["id"])
        self.assertIn("not installed", job.error)
        code, r = request(url + "/api/manage/install", "POST", {"engine": "ollama", "name": "bad name!"})
        self.assertEqual(code, 400)
        code, r = request(url + "/api/manage/install", "POST", {"engine": "nothing", "name": "x"})
        self.assertEqual(code, 400)

    def test_one_job_per_model(self):
        gate = threading.Event()
        jobs.submit("install", "ollama", "m", "Installing m", lambda j: gate.wait(5))
        with self.assertRaisesRegex(jobs.JobError, "still in progress"):
            jobs.submit("start", "ollama", "m", "Starting m", lambda j: None)
        gate.set()

    def test_ollama_not_installed(self):
        url = self.axis("http://127.0.0.1:1")
        with mock.patch.object(manager, "ollama_binary", return_value=None), \
                mock.patch.object(manager, "ollama_url", return_value="http://127.0.0.1:1"):
            code, r = request(url + "/api/manage/install", "POST", {"engine": "ollama", "name": "llama3.1:8b"})
        self.assertEqual(code, 400)
        self.assertIn("ollama.com/download", r["error"])


# --------------------------------------------------------------- llama.cpp


@POSIX_ONLY
class TestLlamaCpp(Base):
    def setUp(self):
        super().setUp()
        self.prog = self.fake_program("llama-server")
        manager.save_settings({"llama_server": str(self.prog), "llama_args": "-ngl 99"})
        self.log = Path(self.home) / "args.log"
        os.environ["FAKE_LOG"] = str(self.log)

    def tearDown(self):
        os.environ.pop("FAKE_LOG", None)
        os.environ.pop("FAKE_FAIL", None)
        super().tearDown()

    def test_full_cycle(self):
        data = b"GGUF" + os.urandom(5000)
        self.hub({"bartowski/Tiny-GGUF": {"Tiny-Q4_K_M.gguf": data, "Tiny-Q8_0.gguf": b"GGUF8"}})
        ollama = self.ollama()
        url = self.axis(ollama.url)

        code, r = request(url + "/api/manage/install", "POST", {"engine": "llamacpp", "name": "bartowski/Tiny-GGUF:Q8_0x"})
        self.assertEqual(code, 200)
        self.assertEqual(len(r["choose"]), 2)               # nothing matched the tag: choose
        code, r = request(url + "/api/manage/install", "POST", {"engine": "llamacpp", "name": "bartowski/Tiny-GGUF"})
        self.assertEqual(code, 202, r)
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        st = request(url + "/api/manage")[1]
        (m,) = st["llamacpp"]["models"]
        self.assertEqual((m["name"], m["repo"], m["size"], m["running"]), ("Tiny-Q4_K_M", "bartowski/Tiny-GGUF", len(data), False))

        code, r = request(url + "/api/manage/start", "POST", {"engine": "llamacpp", "model": m["id"]})
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        args = json.loads(self.log.read_text().splitlines()[-1])
        self.assertIn("--jinja", args)
        self.assertEqual(args[args.index("--alias") + 1], "Tiny-Q4_K_M")
        self.assertEqual(args[-2:], ["-ngl", "99"])
        status = request(url + "/api/status")[1]
        self.assertTrue(status["ready"])
        self.assertEqual(status["models"], ["Tiny-Q4_K_M"])  # found alongside Ollama, which has nothing loaded
        conn, model = backend.pick(ollama.url, "Tiny-Q4_K_M")
        self.assertEqual((conn.kind, model), ("openai", "Tiny-Q4_K_M"))

        code, r = request(url + "/api/manage/delete", "POST", {"engine": "llamacpp", "model": m["id"]})
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")  # stops it first
        self.assertEqual(procs.running("llama"), [])
        self.assertEqual(request(url + "/api/manage")[1]["llamacpp"]["models"], [])
        self.assertFalse((Path(self.home) / "models" / "llm" / "bartowski__Tiny-GGUF").exists())

    def test_vision_model(self):
        data = b"GGUF" + os.urandom(3000)
        self.hub({"ggml-org/Eye-GGUF": {"Eye-Q4_K_M.gguf": data, "mmproj-Eye-f16.gguf": b"GGUFproj", "mmproj-Eye-Q8_0.gguf": b"GGUFq8"}})
        url = self.axis(self.ollama().url)
        code, r = request(url + "/api/manage/install", "POST", {"engine": "llamacpp", "name": "ggml-org/Eye-GGUF"})
        self.assertEqual(code, 202, r)
        self.assertIn("vision file", r["job"]["label"])
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        (m,) = request(url + "/api/manage")[1]["llamacpp"]["models"]
        self.assertTrue(m["vision"])
        self.assertTrue(m["mmproj"].endswith("mmproj-Eye-f16.gguf"))
        folder = Path(self.home) / "models" / "llm" / "ggml-org__Eye-GGUF"
        self.assertFalse((folder / "mmproj-Eye-Q8_0.gguf").exists())          # only the one that is used
        code, r = request(url + "/api/manage/start", "POST", {"engine": "llamacpp", "model": m["id"]})
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        args = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(args[args.index("--mmproj") + 1], m["mmproj"])
        code, r = request(url + "/api/manage/delete", "POST", {"engine": "llamacpp", "model": m["id"]})
        self.assertEqual(wait_job(r["job"]["id"]).state, "done")
        self.assertFalse(folder.exists())                                      # the vision file went with it

    def test_start_failure_shows_log(self):
        root = Path(self.home) / "models" / "llm"
        root.mkdir(parents=True)
        (root / "mine.gguf").write_bytes(b"GGUF")                 # copied in by hand
        os.environ["FAKE_FAIL"] = "1"
        job = jobs.submit("start", "llamacpp", "mine.gguf", "Starting", lambda j: manager.llama_start("mine.gguf", j))
        jobs.wait(job, 20)
        self.assertEqual(job.state, "failed")
        self.assertIn("did not fit in memory", job.error)
        self.assertIn("out of memory", job.error)

    def test_stop_on_shutdown_and_adopt(self):
        root = Path(self.home) / "models" / "llm"
        root.mkdir(parents=True)
        (root / "mine.gguf").write_bytes(b"GGUF")
        job = jobs.submit("start", "llamacpp", "mine.gguf", "Starting", lambda j: manager.llama_start("mine.gguf", j))
        self.assertEqual(jobs.wait(job, 20).state, "done", job.error)
        m = procs.get("llama:mine.gguf")
        pid, port = m.pid, m.port
        # Pretend Axis was killed: forget the process without stopping it, then start "a new Axis".
        with procs._LOCK:
            procs._PROCS.clear()
            procs._ADOPTED = False
        adopted = procs.get("llama:mine.gguf")
        self.assertIsNotNone(adopted)
        self.assertTrue(adopted.adopted)
        self.assertEqual((adopted.pid, adopted.port), (pid, port))
        self.assertIn(adopted.url, manager.chat_urls())
        manager.shutdown()
        m.proc.wait(timeout=10)                     # the adopted copy was stopped by process id
        m.proc.stdout.close()
        self.assertIsNone(procs.http_status(adopted.url))


# ------------------------------------------------------------------ images


@POSIX_ONLY
class TestImageModels(Base):
    def setUp(self):
        super().setUp()
        self.server_prog = self.fake_program("sd-server")
        images.save_settings({"sd_server": str(self.server_prog)})
        self.req = Path(self.home) / "req.json"
        os.environ["FAKE_REQ"] = str(self.req)

    def tearDown(self):
        os.environ.pop("FAKE_REQ", None)
        super().tearDown()

    def install(self, url, **body):
        code, r = request(url + "/api/manage/install", "POST", {"engine": "image", **body})
        self.assertEqual(code, 202, r)
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        return job.result

    def test_install_start_generate_delete(self):
        self.hub({"leejet/Z-Image-Turbo-GGUF": {"z_image_turbo-Q4_0.gguf": b"Z" * 300},
                  "Comfy-Org/z_image_turbo": {"split_files/vae/ae.safetensors": b"V" * 200,
                                              "split_files/text_encoders/qwen.safetensors": b"Q" * 100}})
        url = self.axis("http://127.0.0.1:1")
        code, r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "Comfy-Org/z_image_turbo", "role": "vae"})
        self.assertEqual(code, 400)                     # a VAE needs a model to belong to
        res = self.install(url, name="leejet/Z-Image-Turbo-GGUF", role="diffusion_model")
        mid = res["id"]
        model = images.get_model(mid)
        self.assertEqual((model["name"], model["steps"], model["cfg_scale"], model["width"]), ("z_image_turbo-Q4_0", 4, 1.0, 1024))
        code, r = request(url + "/api/manage/install", "POST", {"engine": "image", "name": "Comfy-Org/z_image_turbo",
                                                                "role": "vae", "target": mid})
        self.assertEqual(code, 200)
        self.assertEqual(len(r["choose"]), 2)
        self.install(url, name="Comfy-Org/z_image_turbo/split_files/vae/ae.safetensors", role="vae", target=mid)
        self.assertTrue(images.get_model(mid)["vae"].endswith("ae.safetensors"))

        # A second model that shares the VAE: deleting the first must keep it.
        images.create_model({"name": "Other", "diffusion_model": images.get_model(mid)["diffusion_model"],
                             "vae": images.get_model(mid)["vae"]})

        code, r = request(url + "/api/manage/start", "POST", {"engine": "image", "model": mid})
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertIsNotNone(manager.image_server_url(mid))
        self.assertEqual(request(url + "/api/manage")[1]["image"]["running"]["model"], mid)

        events = []
        rec = images.generate({"prompt": "a lighthouse", "model": mid, "seed": 7}, Path(self.home) / "out", on_event=events.append)
        self.assertTrue((Path(self.home) / "out" / rec["path"]).read_bytes().startswith(b"\x89PNG"))
        sent = json.loads(self.req.read_text())
        self.assertEqual((sent["prompt"], sent["seed"], sent["sample_params"]["sample_steps"]), ("a lighthouse", 7, 4))
        self.assertIn({"progress": {"step": 1, "steps": 4}}, events)
        self.assertFalse([e for e in events if "preview" in e])
        events = []
        images.generate({"prompt": "a lighthouse", "model": mid}, Path(self.home) / "out", on_event=events.append, preview=True)
        shot = next(e["preview"] for e in events if "preview" in e)
        self.assertEqual((shot["step"], shot["steps"]), (1, 4))
        self.assertTrue(shot["image"].startswith("data:image/png;base64,"))

        code, r = request(url + "/api/manage/delete", "POST", {"engine": "image", "model": mid})
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertIsNone(manager.image_server_url(mid))              # stopped first
        self.assertEqual(job.result["removed_files"], [])            # both files are shared with "Other"
        self.assertEqual(len(job.result["kept_files"]), 2)
        other = images.find_model("Other")
        result = images.delete_model(other["id"], files="axis")
        self.assertEqual(len(result["removed_files"]), 2)
        self.assertFalse(any(p.is_file() for p in (Path(self.home) / "models" / "image").rglob("*")))

    def test_generate_starts_model_without_sd_cli(self):
        f = Path(self.home) / "m.safetensors"
        f.write_bytes(b"x")
        m = images.create_model({"name": "Mine", "model": str(f)})
        rec = images.generate({"prompt": "p", "model": m["id"]}, Path(self.home) / "out")
        self.assertTrue(rec["file"].endswith(".png"))
        self.assertIsNotNone(manager.image_server_url(m["id"]))   # left on for the next picture

    def test_keep_external_files_unless_asked(self):
        f = Path(self.home) / "mine.safetensors"
        f.write_bytes(b"x")
        m = images.create_model({"name": "Mine", "model": str(f)})
        self.assertEqual(images.delete_model(m["id"], files="axis")["kept_files"], [str(f)])
        self.assertTrue(f.exists())
        m = images.create_model({"name": "Mine", "model": str(f)})
        self.assertEqual(images.delete_model(m["id"], files="all")["removed_files"], [str(f)])
        self.assertFalse(f.exists())


if __name__ == "__main__":
    unittest.main()
