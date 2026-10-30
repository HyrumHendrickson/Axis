"""Tests for the CAS page and actions (cas.py), and the run_cas and read_text actions an agent uses.

Maxima is replaced by a small shell script that behaves like `maxima --very-quiet`: it answers
--version, echoes each command it reads, prints Maxima's help-system warning, and asks a question
without end when the code contains ASK. SymPy's environment is the Python running the tests. No
CAS, OCR model or network is needed.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from axis import agents, cas, jobs, ocr, server, skills, tool_skills, tools

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manage import POSIX_ONLY, Base, request

FAKE_MAXIMA = r'''#!/bin/sh
if [ "$1" = "--version" ]; then echo "Maxima 5.47.0"; exit 0; fi
echo "Warning: SIMPLE-WARNING: Maxima is unable to set up the help system." >&2
echo "(Details: no index)" >&2
echo "" >&2
echo ""
pwd > where.txt
echo "$MAXIMA_TEMPDIR" > tempdir.txt
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    display2d*) ;;
    *ASK*) while true; do printf 'Is n equal to -1?\n\nAcceptable answers are yes, y, Y, no, n, N. \n'; done ;;
    *SLEEP*) sleep 30 ;;
    *PLOT*) echo png > "$MAXIMA_TEMPDIR/plot.png"; echo png > "$MAXIMA_TEMPDIR/maxout_1.gnuplot"; echo "[$MAXIMA_TEMPDIR/plot.png]" ;;
    *) echo "out: $line" ;;
  esac
done
'''


class CasBase(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(cas._PROBE.clear)
        cas._PROBE.clear()
        os.environ.pop("AXIS_MAXIMA", None)
        # Nothing on this computer is found unless a test puts it there.
        patch = mock.patch.object(cas, "_usual_maxima", return_value=[])
        patch.start()
        self.addCleanup(patch.stop)
        which = shutil.which
        patch = mock.patch.object(cas.shutil, "which", side_effect=lambda n, *a, **k: None if n == "maxima" else which(n, *a, **k))
        patch.start()
        self.addCleanup(patch.stop)
        self.ws = Path(self.home) / "ws"
        self.ws.mkdir()

    def fake_maxima(self) -> Path:
        prog = Path(self.home) / "bin" / "maxima"
        prog.parent.mkdir(exist_ok=True)
        prog.write_text(FAKE_MAXIMA)
        prog.chmod(0o755)
        os.environ["AXIS_MAXIMA"] = str(prog)
        return prog

    def fake_sympy(self) -> None:
        folder = cas.env_dir() / "bin"
        folder.mkdir(parents=True)
        os.symlink(sys.executable, folder / "python3")
        cas._remember("sympy", {"version": "1.13.3", "installed": "now"})


class TestSkills(unittest.TestCase):
    def test_one_skill_for_each_tool(self):
        built = tool_skills.build()
        self.assertEqual([s["id"] for s in built], ["cas", "images", "ocr"])
        self.assertEqual([s["tools"] for s in built], [["run_cas"], ["generate_image"], ["read_text"]])
        for s in built:
            self.assertEqual(s["group"], tool_skills.GROUP)
            self.assertIn(s["id"], skills.BUILTIN_IDS)
            self.assertLess(len(s["instructions"]), 3000)
            self.assertLessEqual(len(s["description"]), 300)
            for t in s["tools"]:
                self.assertIn(t, tools.TOOL_SPECS)

    def test_agent_gets_the_actions_and_the_folder_note(self):
        with mock.patch.dict(os.environ, {"AXIS_HOME": __import__("tempfile").mkdtemp()}):
            agent = agents.create({"name": "mathy", "skills": ["cas", "ocr"]})
            self.assertEqual(agents.tool_names(agent), ["run_cas", "read_text"])
            system = agents.build_messages(agent, [{"role": "user", "content": "hi"}])[0]["content"]
            self.assertIn("## CAS", system)
            self.assertIn("folder", system)   # these actions work in the agent's folder

    def test_aliases(self):
        self.assertEqual(tools.normalize_call("maxima", {"code": "1+1;"}, ["run_cas"])[0], "run_cas")
        self.assertEqual(tools.normalize_call("ocr", {"image": "a.png"}, ["read_text"]), ("read_text", {"path": "a.png"}))


class TestMaximaCodeCheck(CasBase):
    def check(self, code):
        cas.check_maxima_code(tools.Places(self.ws), code)

    def test_refused(self):
        for code, why in (('system("ls");', "starts other programs"), (":lisp (print 1)", "Lisp"),
                          ("to_lisp();", "Lisp"), ("?print(1);", "Lisp"), ('load("x.lisp");', "Lisp files"),
                          ('stringout("/etc/x.txt", 1);', "outside your folder"),
                          ('batch("../up.mac");', "outside your folder"), ('load("~/init.mac");', "outside your folder")):
            with self.subTest(code=code):
                with self.assertRaisesRegex(cas.CasError, why):
                    self.check(code)

    def test_allowed(self):
        for code in ("integrate(x^2, x);", 'print("1 / 2 is a half")$', 'plot2d(x, [x,0,1], [png_file, "p.png"])$',
                     "/* system(\"ls\") in a comment */ 2+2;", 'print("system(x) as text")$', "a: [1,2]$ a[1];"):
            with self.subTest(code=code):
                self.check(code)

    def test_program_and_cleaning(self):
        self.assertTrue(cas._maxima_program("2+3").endswith("2+3;\n"))
        self.assertTrue(cas._maxima_program("2+3$ /* note */").endswith("/* note */\n"))
        text, question = cas._clean_maxima(
            "Warning: SIMPLE-WARNING: Maxima is unable to set up the help system.\n(Details: x)\n\n5\n"
            "Is n equal to -1?\n\nAcceptable answers are yes, y, Y, no, n, N.\nIs n equal to -1?\n")
        self.assertEqual(text, "5")
        self.assertEqual(question, "Is n equal to -1?")


@POSIX_ONLY
class TestRunning(CasBase):
    def test_nothing_installed(self):
        self.assertIsNone(cas.default_system())
        with self.assertRaisesRegex(cas.CasError, "CAS page"):
            cas.run(tools.Places(self.ws), None, "1+1;")
        ok, out = tools.execute(self.ws, "run_cas", {"code": "1+1;"}, ["run_cas"])
        self.assertFalse(ok)
        self.assertIn("CAS page", out)

    def test_maxima(self):
        self.fake_maxima()
        st = cas.status()
        maxima = st["systems"][0]
        self.assertTrue(maxima["installed"])
        self.assertEqual(maxima["version"], "5.47.0")
        self.assertEqual(st["default"], "maxima")
        ok, out = tools.execute(self.ws, "run_cas", {"code": "diff(x^3, x);\nfactor(x^2-1)"}, ["run_cas"])
        self.assertTrue(ok, out)
        self.assertIn("out: diff(x^3, x);", out)
        self.assertIn("out: factor(x^2-1);", out)       # the missing ; was added
        self.assertNotIn("help system", out)
        self.assertEqual((self.ws / "where.txt").read_text().strip(), str(self.ws.resolve()))
        temp = Path((self.ws / "tempdir.txt").read_text().strip())
        self.assertEqual(temp.parent, self.ws / ".axis-tmp")   # scratch files stay in the folder ...
        self.assertFalse(temp.exists())                       # ... and are removed afterwards
        ok, out = tools.execute(self.ws, "run_cas", {"code": 'system("rm -rf x");'}, ["run_cas"])
        self.assertFalse(ok)
        self.assertIn("other programs", out)

    def test_a_question_stops_maxima(self):
        self.fake_maxima()
        started = time.time()
        result = cas.run(tools.Places(self.ws), "maxima", "1;\nintegrate(x^n, x); /* ASK */\n2;")
        self.assertLess(time.time() - started, 15)
        self.assertEqual(result["question"], "Is n equal to -1?")
        self.assertIn("out: 1;", result["stdout"])
        self.assertNotIn("Acceptable", result["stdout"])
        self.assertIn("assume(", cas.report(result))

    def test_plots_are_moved_into_the_folder_without_replacing_anything(self):
        self.fake_maxima()
        (self.ws / "plot.png").write_text("mine")
        ok, out = tools.execute(self.ws, "run_cas", {"code": "PLOT;"}, ["run_cas"])
        self.assertTrue(ok, out)
        self.assertIn("Saved in your folder: plot-2.png", out)
        self.assertIn("[plot-2.png]", out)          # the scratch path is not shown
        self.assertEqual((self.ws / "plot.png").read_text(), "mine")
        self.assertTrue((self.ws / "plot-2.png").is_file())
        self.assertFalse(list(self.ws.glob("maxout*")))

    def test_timeout(self):
        self.fake_maxima()
        with mock.patch.object(cas, "RUN_TIMEOUT", 1):
            result = cas.run(tools.Places(self.ws), "maxima", "SLEEP;")
        self.assertTrue(result["timed_out"])
        self.assertIn("Stopped", cas.report(result))

    def test_sympy_runs_under_the_python_guard(self):
        self.fake_sympy()
        self.assertEqual(cas.default_system(), "sympy")
        ok, out = tools.execute(self.ws, "run_cas", {"code": "print(6 * 7)"}, ["run_cas"])
        self.assertTrue(ok, out)
        self.assertIn("42", out)
        ok, out = tools.execute(self.ws, "run_cas", {"code": "open('/etc/hostname-x', 'w')", "system": "sympy"}, ["run_cas"])
        self.assertTrue(ok, out)
        self.assertIn("outside", out)   # the Run Python guard refused it

    def test_the_default_can_be_chosen(self):
        self.fake_maxima()
        self.fake_sympy()
        self.assertEqual(cas.default_system(), "maxima")
        cas.save_settings({"default": "sympy"})
        self.assertEqual(cas.default_system(), "sympy")
        with self.assertRaisesRegex(cas.CasError, "Maxima or SymPy"):
            cas.save_settings({"default": "mathematica"})
        # Python sent without naming the system goes to SymPy.
        self.assertIn("42", tools.run_cas(tools.Places(self.ws), {"code": "import math\nprint(42)"}))

    def test_the_agent_is_shown_only_the_cas_in_use(self):
        self.fake_maxima()
        self.fake_sympy()
        agent = agents.Agent(name="m", skills=["cas"])
        for chosen, shown, hidden in (("maxima", "run_maxima", "run_sympy"), ("sympy", "run_sympy", "run_maxima")):
            cas.save_settings({"default": chosen})
            self.assertEqual(tools.model_tools(["run_cas"]), [shown])
            self.assertEqual([t["function"]["name"] for t in tools.specs([shown])], [shown])
            system = agents.skills_prompt(agent.skills, agents.tool_names(agent))
            self.assertIn(shown, system)
            self.assertNotIn(hidden, system)
            self.assertNotIn("run_cas", system)
            self.assertNotIn("system", tools.tool_spec(shown)["parameters"]["properties"])
            # a call to the shown name runs that system
            name, args = tools.normalize_call(shown, {"code": "1"}, ["run_cas"])
            self.assertEqual((name, args["system"]), ("run_cas", chosen))

    def test_the_agent_is_told_when_no_cas_is_installed(self):
        self.assertEqual(tools.model_tools(["run_cas"]), ["run_cas"])
        self.assertIn("No computer algebra system is installed", tool_skills.adapt("cas", "Use run_cas."))

    def test_settings_path(self):
        prog = self.fake_maxima()
        os.environ.pop("AXIS_MAXIMA")
        self.assertIsNone(cas.maxima_binary())
        cas.save_settings({"maxima": str(prog.parent)})
        self.assertEqual(cas.maxima_binary(), prog.resolve())


class TestInstalling(CasBase):
    def test_refusals(self):
        with self.assertRaisesRegex(cas.CasError, "did not install this Maxima"):
            cas.remove({"system": "maxima"})
        with self.assertRaisesRegex(cas.CasError, "Maxima or SymPy"):
            cas.install({"system": "mathematica"})

    def test_plans(self):
        with mock.patch.object(cas.engines, "this_platform", return_value=("windows", "x64")), \
                mock.patch.object(cas.shutil, "which", return_value="C:/winget.exe"):
            plan = cas.maxima_install_plan()
        self.assertEqual(plan["how"], "winget")
        self.assertIn(cas.WINGET_ID, plan["command"])
        with mock.patch.object(cas.engines, "this_platform", return_value=("windows", "x64")), \
                mock.patch.object(cas.shutil, "which", return_value=None):
            self.assertIsNone(cas.maxima_install_plan()["how"])

    @POSIX_ONLY
    def test_install_sympy_and_delete(self):
        def fake_run(cmd, job, on_line=None, env=None, cwd=None, timeout=None, beat=None):
            self.assertEqual(cmd[1:4], ["-m", "pip", "install"])
            self.assertIn("sympy", cmd)
            return 0, mock.Mock(tail=lambda n=12: "")

        def fake_quiet(cmd, timeout=20.0):
            if "venv" in cmd:
                (Path(cmd[-1]) / "bin").mkdir(parents=True)
                os.symlink(sys.executable, Path(cmd[-1]) / "bin" / "python3")
                return 0, ""
            return 0, "1.13.3\n"

        with mock.patch.object(ocr, "_run", side_effect=fake_run), mock.patch.object(cas.engines, "_quiet_run", side_effect=fake_quiet):
            job = jobs.wait(jobs.get(cas.install({"system": "sympy"})["job"]["id"]), 30)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(cas.status()["systems"][1]["version"], "1.13.3")
        with self.assertRaisesRegex(cas.CasError, "already installed"):
            cas.install({"system": "sympy"})
        st = cas.remove({"system": "sympy"})
        self.assertFalse(st["systems"][1]["installed"])
        self.assertFalse(cas.env_dir().exists())


class TestReadText(CasBase):
    def test_pick_model(self):
        models = [{"id": "tesseract:eng", "model": "eng", "name": "Tesseract eng", "usable": True},
                  {"id": "tesseract:lat", "model": "lat", "name": "Tesseract lat", "usable": True},
                  {"id": "easyocr:en", "model": "en", "name": "EasyOCR en", "usable": False}]
        with mock.patch.object(ocr, "list_models", return_value=models), mock.patch.object(ocr, "running_model", return_value=None):
            self.assertEqual(ocr.pick_model(), "tesseract:eng")
            self.assertEqual(ocr.pick_model("lat"), "tesseract:lat")
            with self.assertRaisesRegex(ocr.OcrError, "no usable OCR model"):
                ocr.pick_model("easyocr:en")
        with mock.patch.object(ocr, "list_models", return_value=[]), mock.patch.object(ocr, "running_model", return_value=None):
            with self.assertRaisesRegex(ocr.OcrError, "Models page"):
                ocr.pick_model()

    def test_action(self):
        (self.ws / "scan.png").write_bytes(b"\x89PNG")
        seen = {}

        def fake_run(body, path, emit):
            seen.update(body=body, path=path)
            return {"text": "Gallia est omnis divisa", "words": 4, "confidence": 91.4, "seconds": 0.2,
                    "name": "Tesseract lat"}

        with mock.patch.object(ocr, "pick_model", return_value="tesseract:lat"), mock.patch.object(ocr, "run", side_effect=fake_run):
            ok, out = tools.execute(self.ws, "read_text", {"path": "scan.png", "languages": "lat"}, ["read_text"])
        self.assertTrue(ok, out)
        self.assertIn("Gallia est omnis divisa", out)
        self.assertIn("confidence 91%", out)
        self.assertEqual(seen["body"], {"model": "tesseract:lat", "languages": "lat"})
        ok, out = tools.execute(self.ws, "read_text", {"path": "../outside.png"}, ["read_text"])
        self.assertFalse(ok)
        ok, out = tools.execute(self.ws, "read_text", {"path": "missing.png"}, ["read_text"])
        self.assertFalse(ok)
        self.assertIn("not a file", out)


@POSIX_ONLY
class TestRoutes(CasBase):
    def test_routes(self):
        self.fake_maxima()
        srv = server.make_server(0, None)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        status, st = request(srv.url + "/api/cas")
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in st["systems"]], ["maxima", "sympy"])
        status, r = request(srv.url + "/api/cas/run", "POST", {"code": "2+2;"})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["system"], "maxima")
        self.assertIn("out: 2+2;", r["stdout"])
        status, r = request(srv.url + "/api/cas/settings", "PUT", {"default": "nope"})
        self.assertEqual(status, 400)
        status, r = request(srv.url + "/api/cas/remove", "POST", {"system": "maxima"})
        self.assertEqual(status, 400)
        self.assertIn("did not install this Maxima", r["error"])


if __name__ == "__main__":
    unittest.main()
