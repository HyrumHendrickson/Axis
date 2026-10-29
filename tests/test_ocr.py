"""Tests for OCR (ocr.py and ocr_worker.py).

Everything runs against fakes: a fake GitHub serving tessdata files, a small program that
stands in for Tesseract, and fake torch and easyocr packages for the PyTorch worker.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from axis import jobs, manager, ocr, procs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manage import POSIX_ONLY, Base, request, serve, wait_job

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def git_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


class FakeTessdata:
    """tessdata repositories: {"tesseract-ocr/tessdata_fast": {"eng.traineddata": bytes}}."""

    def __init__(self, repos, api=True, wrong_sha=()):
        self.repos, self.api, self.wrong_sha = repos, api, set(wrong_sha)
        ref = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(self.path).path.split("/") if p]
                if parts[:1] == ["api"]:   # /api/repos/<o>/<r>/git/trees/main
                    if not ref.api:
                        return self._send(403, b'{"message":"rate limit"}')
                    files = ref.repos.get("/".join(parts[2:4]), {})
                    tree = [{"path": p, "type": "blob", "size": len(b),
                             "sha": "0" * 40 if p in ref.wrong_sha else git_sha(b)} for p, b in files.items()]
                    return self._send(200, json.dumps({"tree": tree, "truncated": False}).encode())
                if parts[:1] == ["raw"]:   # /raw/<o>/<r>/main/<path>
                    data = ref.repos.get("/".join(parts[1:3]), {}).get("/".join(parts[4:]))
                    return self._send(200, data, "application/octet-stream") if data is not None else self._send(404)
                return self._send(404)

        self.srv, self.url = serve(H)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


FAKE_TESSERACT = r'''
import os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("tesseract 5.9.0"); sys.exit(0)
if "--list-langs" in args:
    print('List of available languages in "/fake/tessdata/" (2):'); print("eng"); print("osd"); sys.exit(0)
data = sys.stdin.buffer.read()
with open(os.environ["FAKE_TESS_LOG"], "w") as f:
    f.write(" ".join(args) + "\n" + str(len(data)))
rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t90\tArma",
        "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t80\tvirumque",
        "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t70\tcano",
        "5\t1\t1\t2\t1\t1\t0\t0\t1\t1\t-1\tTroiae"]
print("\n".join(rows))
'''

FAKE_TORCH = '''__version__ = "9.9.9+fake"
class _Cuda:
    @staticmethod
    def is_available():
        return False
cuda = _Cuda()
class backends:
    mps = None
'''

FAKE_EASYOCR = '''import os
__version__ = "1.7.2-fake"
class Reader:
    def __init__(self, lang_list, gpu=True, model_storage_directory=None, user_network_directory=None,
                 download_enabled=True, verbose=True, **kw):
        if "xx" in lang_list:
            raise ValueError("xx is not supported")
        os.makedirs(model_storage_directory, exist_ok=True)
        path = os.path.join(model_storage_directory, "craft_mlt_25k.pth")
        if not os.path.exists(path):
            print("Downloading detection model, please wait.")
            print("Progress: |#####| 50.0% Complete")
            with open(path, "wb") as f:
                f.write(b"w" * 1000)
        self.langs = lang_list
    def readtext(self, data, detail=1, paragraph=False):
        assert isinstance(data, bytes)
        return [([[60, 0], [100, 0], [100, 10], [60, 10]], "world", 0.5),
                ([[0, 1], [50, 1], [50, 11], [0, 11]], "hello", 0.9),
                ([[0, 30], [40, 30], [40, 40], [0, 40]], "+".join(self.langs), 1.0)]
'''


class OcrBase(Base):
    def setUp(self):
        super().setUp()
        for key in ("AXIS_TESSERACT", "AXIS_OCR_PYTHON"):
            os.environ.pop(key, None)
        ocr._PROBE.clear()
        self.which = mock.patch("axis.ocr.shutil.which", return_value=None)
        self.which.start()
        self.usual = mock.patch("axis.ocr._usual_tesseract", return_value=[])
        self.usual.start()

    def tearDown(self):
        self.which.stop()
        self.usual.stop()
        super().tearDown()

    def tessdata(self, repos, **kw):
        fake = FakeTessdata(repos, **kw)
        self.cleanups.append(fake.close)
        os.environ["AXIS_GITHUB_API"] = fake.url + "/api"
        os.environ["AXIS_GITHUB_RAW"] = fake.url + "/raw"
        return fake

    def fake_tesseract(self):
        script = Path(self.home) / "fake_tesseract.py"
        script.write_text(FAKE_TESSERACT)
        prog = Path(self.home) / "bin" / "tesseract"
        prog.parent.mkdir(parents=True, exist_ok=True)
        prog.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        prog.chmod(0o755)
        os.environ["AXIS_TESSERACT"] = str(prog)
        os.environ["FAKE_TESS_LOG"] = str(Path(self.home) / "tess.log")
        return prog

    def picture(self, name="page.png"):
        folder = Path(self.home) / "shared" / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(PNG)
        return {"place": "_shared", "path": f"uploads/{name}"}


def stream(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as resp:
        return [json.loads(line) for line in resp.read().decode().splitlines() if line.strip()]


# ------------------------------------------------------------------ names


class TestNames(unittest.TestCase):
    def test_tesseract_langs(self):
        self.assertEqual(ocr.tesseract_langs("eng"), (["eng"], "fast"))
        self.assertEqual(ocr.tesseract_langs("eng+lat:best"), (["eng", "lat"], "best"))
        self.assertEqual(ocr.tesseract_langs("English, Latin"), (["eng", "lat"], "fast"))
        self.assertEqual(ocr.tesseract_langs("script/Latin")[0], ["script/Latin"])
        for bad in ("", "e", "../eng", "eng;rm"):
            with self.assertRaises(ocr.OcrError):
                ocr.tesseract_langs(bad)

    def test_easyocr_and_doctr(self):
        self.assertEqual(ocr.easyocr_langs("en, fr"), ["en", "fr"])
        self.assertEqual(ocr.easyocr_langs("ch_sim+English"), ["ch_sim", "en"])
        with self.assertRaises(ocr.OcrError):
            ocr.easyocr_langs("en;fr!")
        self.assertEqual(ocr.doctr_pair(""), ocr.DOCTR_DEFAULT)
        self.assertEqual(ocr.doctr_pair("db_resnet50+parseq"), ("db_resnet50", "parseq"))
        self.assertEqual(ocr.doctr_pair("parseq"), ("fast_base", "parseq"))
        self.assertEqual(ocr.doctr_pair("doctr-small"), ocr.DOCTR_PRESETS["small"])
        with self.assertRaises(ocr.OcrError):
            ocr.doctr_pair("resnet9000")

    def test_text_from_tsv(self):
        tsv = "\n".join(["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                         "1\t1\t0\t0\t0\t0\t0\t0\t9\t9\t-1\t",
                         "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t90\tIn",
                         "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t80\tprincipio",
                         "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t70\terat",
                         "5\t1\t1\t2\t1\t1\t0\t0\t1\t1\t60\tVerbum"])
        self.assertEqual(ocr.text_from_tsv(tsv), ("In principio\nerat\n\nVerbum", 75.0, 4))

    def test_lines_from_boxes(self):
        sys.path.insert(0, str(Path(ocr.WORKER).parent))
        try:
            import ocr_worker
        finally:
            sys.path.pop(0)
        items = [([[60, 0], [99, 0], [99, 10], [60, 10]], "b", 1), ([[0, 2], [50, 2], [50, 12], [0, 12]], "a", 1),
                 ([[0, 40], [9, 40], [9, 50], [0, 50]], "c", 1)]
        self.assertEqual(ocr_worker.lines_from_boxes(items), "a b\nc")


# ------------------------------------------------------------------ Tesseract


class TestTesseract(OcrBase):
    def test_install_languages_verified_and_listed(self):
        eng, lat = b"E" * 3000, b"L" * 2000
        self.tessdata({"tesseract-ocr/tessdata_fast": {"eng.traineddata": eng, "lat.traineddata": lat},
                       "tesseract-ocr/tessdata_best": {"eng.traineddata": eng * 2}})
        r = manager.install({"engine": "ocr", "kind": "tesseract", "name": "english+latin"}, None)
        job = wait_job(r["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        folder = ocr.tessdata_dir()
        self.assertEqual((folder / "lat.traineddata").read_bytes(), lat)
        langs = ocr.axis_langs()
        self.assertEqual(sorted(langs), ["eng", "lat"])
        self.assertEqual(langs["eng"]["variant"], "fast")
        job = wait_job(manager.install({"engine": "ocr", "kind": "tesseract", "name": "eng:best"}, None)["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(ocr.axis_langs()["eng"], {"variant": "best", "size": 6000, "installed": mock.ANY})
        ids = [m["id"] for m in ocr.list_models()]
        self.assertEqual(ids, ["tesseract:eng", "tesseract:lat"])
        self.assertFalse(any(m["usable"] for m in ocr.list_models()))   # no Tesseract program yet

        job = wait_job(manager.delete({"engine": "ocr", "model": "tesseract:lat"}, None)["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertFalse((folder / "lat.traineddata").exists())

    def test_unknown_language_and_bad_checksum(self):
        self.tessdata({"tesseract-ocr/tessdata_fast": {"eng.traineddata": b"E" * 100}}, wrong_sha=["eng.traineddata"])
        job = wait_job(manager.install({"engine": "ocr", "kind": "tesseract", "name": "zzz"}, None)["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("no language named 'zzz'", job.error)
        job = wait_job(manager.install({"engine": "ocr", "kind": "tesseract", "name": "eng"}, None)["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertIn("checksum", job.error)
        self.assertEqual(list(ocr.tessdata_dir().glob("*")), [])

    def test_download_without_github_api(self):
        self.tessdata({"tesseract-ocr/tessdata_fast": {"grc.traineddata": b"G" * 500}}, api=False)
        job = wait_job(manager.install({"engine": "ocr", "kind": "tesseract", "name": "grc"}, None)["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        job = wait_job(manager.install({"engine": "ocr", "kind": "tesseract", "name": "xyz"}, None)["job"]["id"])
        self.assertIn("no language named 'xyz'", job.error)

    @POSIX_ONLY
    def test_read_text_through_the_page_api(self):
        self.fake_tesseract()
        url = self.axis(None)
        st = request(url + "/api/ocr")[1]
        self.assertEqual(st["tesseract"]["version"], "5.9.0")
        self.assertEqual([m["id"] for m in st["models"]], ["tesseract:eng"])   # osd is not offered
        self.assertTrue(st["ready"])
        events = stream(url + "/api/ocr/run", {"model": "tesseract:eng", "image": self.picture(), "psm": 6})
        result = events[-2]["result"]
        self.assertEqual(result["text"], "Arma virumque\ncano\n\nTroiae")
        self.assertEqual(result["confidence"], 80.0)
        self.assertEqual(result["source"], "_shared:uploads/page.png")
        self.assertEqual(events[-1], {"done": True})
        args, size = (Path(self.home) / "tess.log").read_text().splitlines()
        self.assertEqual(int(size), len(PNG))   # the picture goes in on stdin
        self.assertIn("-l eng --psm 6", args)
        self.assertNotIn("--tessdata-dir", args)  # only Tesseract's own languages are needed

        # A language Axis does not have is reported before Tesseract runs.
        events = stream(url + "/api/ocr/run", {"model": "tesseract:eng", "languages": "eng+lat", "image": self.picture()})
        self.assertIn("lat is not installed", events[-1]["error"])
        # Errors in the request itself are plain HTTP errors.
        self.assertEqual(request(url + "/api/ocr/run", "POST", {"model": "tesseract:eng",
                                                               "image": {"place": "_shared", "path": "nope.png"}})[0], 404)
        (Path(self.home) / "shared" / "a.gif").write_bytes(b"GIF89a")
        code, body = request(url + "/api/ocr/run", "POST", {"model": "tesseract:eng", "image": {"place": "_shared", "path": "a.gif"}})
        self.assertEqual(code, 400)
        self.assertIn("PNG, JPEG", body["error"])

    @POSIX_ONLY
    def test_languages_from_both_folders(self):
        self.fake_tesseract()
        system = Path(self.home) / "system-tessdata"
        system.mkdir()
        (system / "eng.traineddata").write_bytes(b"E")
        ocr.tessdata_dir().mkdir(parents=True)
        (ocr.tessdata_dir() / "lat.traineddata").write_bytes(b"L")
        with mock.patch("axis.ocr.tesseract_info", return_value={"version": "5", "langs": ["eng", "osd"],
                                                                  "dir": str(system), "works": True}):
            path = Path(self.home) / "shared" / "uploads"
            self.picture()
            result = ocr.run({"model": "tesseract:lat", "languages": "eng+lat"}, path / "page.png", lambda e: None)
        self.assertEqual(result["model"], "eng+lat")
        args = (Path(self.home) / "tess.log").read_text().splitlines()[0]
        self.assertIn(f"--tessdata-dir {ocr.tessdata_dir()}", args)
        self.assertEqual((ocr.tessdata_dir() / "eng.traineddata").read_bytes(), b"E")   # copied next to lat
        self.assertEqual(ocr.axis_langs()["eng"]["variant"], "copied")

    def test_settings(self):
        st = ocr.save_settings({"tesseract": " /opt/t/tesseract ", "device": "cpu"})
        self.assertEqual(st["settings"]["tesseract"], "/opt/t/tesseract")
        self.assertEqual(st["settings"]["device"], "cpu")
        with self.assertRaises(ocr.OcrError):
            ocr.save_settings({"device": "gpu"})
        with self.assertRaises(ocr.OcrError):
            ocr.save_settings({"torch_index": "file:///etc"})

    def test_engine_routes(self):
        url = self.axis(None)
        code, body = request(url + "/api/manage/engines/remove", "POST", {"engine": "tesseract"})
        self.assertEqual(code, 400)
        code, body = request(url + "/api/manage/install", "POST", {"engine": "ocr", "kind": "nonsense", "name": "x"})
        self.assertEqual(code, 400)
        st = request(url + "/api/manage")[1]
        self.assertIn("ocr", st)
        self.assertIn("tesseract", st["ocr"]["catalog"])


# ------------------------------------------------------------------ PyTorch models (fake torch and EasyOCR)


@POSIX_ONLY
class TestTorchModels(OcrBase):
    def setUp(self):
        super().setUp()
        site = Path(self.home) / "fake-site"
        (site / "torch").mkdir(parents=True)
        (site / "torch" / "__init__.py").write_text(FAKE_TORCH)
        (site / "easyocr.py").write_text(FAKE_EASYOCR)
        os.environ["PYTHONPATH"] = str(site)
        py = ocr.env_dir() / "bin" / "python3"
        py.parent.mkdir(parents=True)
        py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        py.chmod(0o755)
        ocr._write_json(ocr._env_record(), {"build": "cpu", "torch": "9.9.9+fake", "packages": {"easyocr": "1.7.2-fake"}})

    def test_install_run_start_stop_delete(self):
        job = wait_job(manager.install({"engine": "ocr", "kind": "easyocr", "name": "en,fr"}, None)["job"]["id"], 60)
        self.assertEqual(job.state, "done", job.error)
        folder = ocr.model_dir("easyocr", "en,fr")
        self.assertTrue((folder / "craft_mlt_25k.pth").is_file())
        models = {m["id"]: m for m in ocr.list_models()}
        self.assertTrue(models["easyocr:en,fr"]["usable"])
        self.assertEqual(models["easyocr:en,fr"]["size"], 1000)

        url = self.axis(None)
        pic = self.picture()
        events = stream(url + "/api/ocr/run", {"model": "easyocr:en,fr", "image": pic})   # not started: loaded for this picture
        result = events[-2]["result"]
        self.assertEqual(result["text"], "hello world\nen+fr")
        self.assertEqual(result["confidence"], 80.0)
        self.assertFalse(result["kept_on"])

        job = wait_job(manager.start({"engine": "ocr", "model": "easyocr:en,fr"}, None)["job"]["id"], 60)
        self.assertEqual(job.state, "done", job.error)
        self.assertTrue(ocr.list_models()[0]["running"])
        events = stream(url + "/api/ocr/run", {"model": "easyocr:en,fr", "image": pic})
        self.assertTrue(events[-2]["result"]["kept_on"])
        self.assertEqual(events[-2]["result"]["words"], 3)
        m = procs.get("ocr:easyocr:en,fr")
        code = procs.http_status(m.url + "/ocr")   # GET is not an OCR request
        self.assertEqual(code, 404)
        req = urllib.request.Request(m.url + "/ocr", data=b'{"image": "/etc/hostname"}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:   # no token, no answer
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

        code, body = request(url + "/api/manage/engines/remove", "POST", {"engine": "pytorch"})
        self.assertEqual(code, 400)   # a model is on

        job = wait_job(manager.stop({"engine": "ocr", "model": "easyocr:en,fr"}, None)["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertIsNone(procs.get("ocr:easyocr:en,fr"))
        job = wait_job(manager.delete({"engine": "ocr", "model": "easyocr:en,fr"}, None)["job"]["id"])
        self.assertEqual(job.state, "done", job.error)
        self.assertFalse(folder.exists())
        self.assertEqual([m for m in ocr.list_models() if m["engine"] == "easyocr"], [])

    def test_failed_download_leaves_nothing(self):
        job = wait_job(manager.install({"engine": "ocr", "kind": "easyocr", "name": "xx"}, None)["job"]["id"], 60)
        self.assertEqual(job.state, "failed")
        self.assertIn("xx is not supported", job.error)
        self.assertFalse(ocr.model_dir("easyocr", "xx").exists())

    def test_missing_package_is_installed_with_pip(self):
        ocr._write_json(ocr._env_record(), {"build": "cpu", "torch": "9.9.9+fake", "packages": {}})
        calls = []

        def fake_pip(py, args, job, what):
            calls.append(args)

        with mock.patch("axis.ocr._pip", side_effect=fake_pip):
            job = wait_job(manager.install({"engine": "ocr", "kind": "easyocr", "name": "en"}, None)["job"]["id"], 60)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(calls, [["easyocr"]])
        self.assertEqual(ocr.env_info()["packages"], {"easyocr": "1.7.2-fake"})

    def test_torch_index_choice(self):
        with mock.patch("axis.engines.this_platform", return_value=("windows", "x64")):
            self.assertEqual(ocr.torch_index_args("cpu"), ["--index-url", ocr.CPU_INDEX])
            self.assertEqual(ocr.torch_index_args("cuda"), ["--index-url", ocr.CUDA_INDEX_WINDOWS])
        with mock.patch("axis.engines.this_platform", return_value=("linux", "x64")):
            self.assertEqual(ocr.torch_index_args("cuda"), [])
        with mock.patch("axis.engines.this_platform", return_value=("macos", "arm64")):
            self.assertEqual(ocr.torch_builds(), ["default"])
            self.assertEqual(ocr.torch_index_args("default"), [])
        ocr.save_settings({"torch_index": "https://example.org/whl"})
        self.assertEqual(ocr.torch_index_args("cpu"), ["--index-url", "https://example.org/whl"])


if __name__ == "__main__":
    unittest.main()
