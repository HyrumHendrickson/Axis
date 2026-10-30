"""Tests for Axis 4.3.8: run_craft, repairs for code escaped twice, tolerant tool-call JSON, and the
file actions' fixes. A stand-in <app>-cli script is used; no app is downloaded.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axis import backend, runner, tools  # noqa: E402
from test_craftskills import Home, POSIX_ONLY  # noqa: E402


class TestRepairs(unittest.TestCase):
    def test_fence_is_removed(self):
        self.assertEqual(tools.strip_fence("```python\nprint(1)\n```"), "print(1)")
        self.assertEqual(tools.strip_fence("print(1)"), "print(1)")

    def test_python_escaped_twice(self):
        self.assertEqual(tools.fix_escaped_code('x = 1\\nprint(x)'), "x = 1\nprint(x)")
        self.assertEqual(tools.fix_escaped_code('print(\\"hi\\")'), 'print("hi")')
        self.assertEqual(tools.fix_escaped_code('import os\\nprint(\\"hi\\")'), 'import os\nprint("hi")')

    def test_python_that_compiles_is_never_changed(self):
        for code in ('print("a\\n")', 'print("say \\"hi\\"")', "def f(:\n  pass"):
            self.assertEqual(tools.fix_escaped_code(code), code)

    def test_shell_json_with_backslashed_quotes(self):
        head = "photocraft-cli run --params "
        self.assertEqual(tools.fix_shell_code(head + "'{\\\"a\\\":1}'", "bash"), head + "'{\"a\":1}'")
        fine = head + "'{\"a\":\"b\\\"c\"}'"   # a legitimate escaped quote inside JSON
        self.assertEqual(tools.fix_shell_code(fine, "bash"), fine)

    def test_shell_literal_breaks_outside_quotes_only(self):
        self.assertEqual(tools.fix_shell_code("cd notes\\nls", "bash"), "cd notes\nls")
        self.assertEqual(tools.fix_shell_code('printf "a\\n"', "bash"), 'printf "a\\n"')
        self.assertEqual(tools.fix_shell_code('echo "a\\nb"\nls', "bash"), 'echo "a\\nb"\nls')

    def test_cmd_gets_double_quotes_and_keeps_its_backslashes(self):
        self.assertEqual(tools.fix_shell_code("app run --params '{\"w\":1}'", "cmd"), 'app run --params "{\\"w\\":1}"')
        self.assertEqual(tools.fix_shell_code("dir new\\notes", "cmd"), "dir new\\notes")
        self.assertEqual(tools.fix_shell_code("echo it's fine", "cmd"), "echo it's fine")

    def test_rust_breaks_outside_strings(self):
        self.assertEqual(tools.fix_rust_code('fn main() {\\n    println!("a\\n");\\n}'),
                         'fn main() {\n    println!("a\\n");\n}')
        ok = 'fn main() { println!("a\\n"); }'
        self.assertEqual(tools.fix_rust_code(ok), ok)
        self.assertEqual(tools.fix_rust_code("fn f<'a>(x: &'a str) -> char { '\\n' }\\nfn main() {}"),
                         "fn f<'a>(x: &'a str) -> char { '\\n' }\nfn main() {}")

    def test_cas_code(self):
        self.assertEqual(tools.fix_cas_code('plot2d(sin(x),[x,0,6],[png_file,\\"p.png\\"]);', None),
                         'plot2d(sin(x),[x,0,6],[png_file,"p.png"]);')
        self.assertEqual(tools.fix_cas_code("a:1;\\nb:2;", "maxima"), "a:1;\nb:2;")
        self.assertEqual(tools.fix_cas_code("import sympy\\nprint(1)", None), "import sympy\nprint(1)")
        self.assertEqual(tools.fix_cas_code('print("x")$', "maxima"), 'print("x")$')

    def test_normalize_call_applies_them(self):
        name, args = tools.normalize_call("run_terminal", {"code": "echo a\\necho b", "shell": "bash"}, ["run_terminal"])
        self.assertEqual(args["code"], "echo a\necho b")
        name, args = tools.normalize_call("run_python", {"code": "```py\nprint(1)\n```"}, ["run_python"])
        self.assertEqual(args["code"], "print(1)")


class TestToolCallJson(unittest.TestCase):
    def test_a_raw_line_break_inside_a_string_is_accepted(self):
        raw = '{"name": "write_file", "arguments": {"path": "a.txt", "content": "one\ntwo"}}'
        name, args = runner.parse_tool_block("```tool\n" + raw + "\n```")
        self.assertEqual((name, args["content"]), ("write_file", "one\ntwo"))
        self.assertEqual(backend._parse_args('{"content": "a\nb"}'), {"content": "a\nb"})
        self.assertEqual(runner.find_text_call(raw, ["write_file"])[1]["content"], "one\ntwo")


class TestFileActions(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ws, True)

    def run_tool(self, name, **args):
        return tools.execute(self.ws, name, args, [name])

    def test_json_content_is_saved_as_json(self):
        self.assertTrue(self.run_tool("write_file", path="s.json", content={"a": ["x", 1], "t": "it's"})[0])
        self.assertEqual(json.loads((self.ws / "s.json").read_text()), {"a": ["x", 1], "t": "it's"})
        self.run_tool("write_file", path="s.jsonl", content=[{"a": 1}, {"a": 2}])
        self.assertEqual((self.ws / "s.jsonl").read_text().splitlines(), ['{"a": 1}', '{"a": 2}'])

    def test_read_file_continues_from_start(self):
        (self.ws / "big.txt").write_text("x" * (tools.MAX_READ + 500))
        ok, first = self.run_tool("read_file", path="big.txt")
        self.assertIn(f"start={tools.MAX_READ}", first)
        ok, rest = self.run_tool("read_file", path="big.txt", start=tools.MAX_READ)
        self.assertEqual(rest, "x" * 500)

    def test_a_program_that_succeeds_is_not_reported_as_an_error(self):
        ok = {"exit": 0, "stdout": "", "stderr": "wrote a.docx", "timed_out": False}
        self.assertIn("Messages:\nwrote a.docx", tools._report(ok))
        self.assertIn("Errors:\n", tools._report({**ok, "exit": 1}))
        self.assertIn("The program printed nothing", tools._report({**ok, "stderr": ""}, "(The program printed nothing.)"))


class TestSpecs(unittest.TestCase):
    def test_every_action_has_a_short_definition(self):
        for name in tools.TOOL_SPECS:
            spec = tools.tool_spec(name)
            size = len(json.dumps(spec))
            self.assertLess(size, 1000, name)
            self.assertEqual(sorted(spec["parameters"]["required"]),
                             sorted(k for k in spec["parameters"]["required"] if k in spec["parameters"]["properties"]))


@POSIX_ONLY
class TestRunCraft(Home):
    def setUp(self):
        super().setUp()
        self.ws = Path(self.home) / "ws"
        folder = self.install("wordcraft", with_cli=False)
        cli = folder / "wordcraft-cli"   # prints each argument on a line of its own, so quoting mistakes show
        cli.write_text('#!/bin/sh\nfor a in "$@"; do printf "<%s>\\n" "$a"; done\nprintf "%s\\n" "$0" >&2\n')
        cli.chmod(0o755)

    def run_craft(self, **args):
        return tools.execute(self.ws, "run_craft", args, ["run_craft"])

    def test_arguments_arrive_exactly_as_written(self):
        text = "It's \"fine\"\nsecond $HOME `x`"
        ok, out = self.run_craft(app="wordcraft", args=["run", "--cmd", {"join": ["text.insert=", {"text": text}]},
                                                         "--params", {"a": [1, 2]}, True, 3])
        self.assertTrue(ok, out)
        expected = json.dumps({"text": text}, separators=(",", ":"), ensure_ascii=False)
        self.assertIn("<text.insert=" + expected + ">", out)
        self.assertIn('<{"a":[1,2]}>', out)
        self.assertIn("<true>", out)
        self.assertIn("<3>", out)

    def test_the_program_name_and_backslashed_json_are_tidied(self):
        ok, out = self.run_craft(app="WordCraft-cli", args=["wordcraft-cli", "run", '--cmd', 'x={\\"a\\":1}'])
        self.assertTrue(ok, out)
        self.assertNotIn("<wordcraft-cli>", out)
        self.assertIn('<x={"a":1}>', out)

    def test_args_as_text_or_a_list_in_text(self):
        self.assertIn("<a b.png>", self.run_craft(app="wordcraft", args="info 'a b.png'")[1])
        self.assertIn("<a b.png>", self.run_craft(app="wordcraft", args='["info", "a b.png"]')[1])

    def test_a_call_named_for_the_program_is_routed(self):
        self.assertEqual(tools.normalize_call("wordcraft-cli", {"args": ["info"]}, ["run_craft"]),
                         ("run_craft", {"args": ["info"], "app": "wordcraft"}))
        name, args = tools.normalize_call("run_craft", {"command": "wordcraft-cli info a.docx"}, ["run_craft"])
        self.assertEqual((name, args["app"], args["args"]), ("run_craft", "wordcraft", ["wordcraft-cli", "info", "a.docx"]))

    def test_places_outside_the_folder_are_refused_but_text_is_not(self):
        for bad in (["text", "/etc/passwd"], ["text", "../x.docx"], ["--out=~/x"], ["run", "--cmd", {"join": ["a=", {"path": "/tmp/x"}]}]):
            ok, out = self.run_craft(app="wordcraft", args=bad)
            self.assertFalse(ok, bad)
            self.assertIn("outside your folder", out)
        ok, out = self.run_craft(app="wordcraft", args=["run", "--cmd", {"join": ["t=", {"text": "~5 and /usr and a/b and ../x"}]}])
        self.assertTrue(ok, out)

    def test_find_keeps_matching_lines_and_their_indented_notes(self):
        listing = "a.x  A\n   params: p\nb.y  B\n   params: q\n"
        self.assertEqual(tools.find_in_output(listing, "Q"), "b.y  B\n   params: q")
        items = json.dumps([{"id": "a", "p": 1}, {"id": "b", "p": 2}], indent=2)
        self.assertEqual(tools.find_in_output(items, '"id":"b"'), '{"id":"b","p":2}')
        ok, out = self.run_craft(app="wordcraft", args=["x", "needle", "y"], find="needle")
        self.assertIn("<needle>", out)
        self.assertNotIn("<x>", out)

    def test_missing_app_and_bad_input(self):
        ok, out = self.run_craft(app="photocraft", args=["info"])
        self.assertFalse(ok)
        self.assertIn("not installed", out)
        self.assertFalse(self.run_craft(app="nothing", args=[])[0])
        self.assertFalse(self.run_craft(app="wordcraft", args=5)[0])
        self.assertFalse(self.run_craft(app="wordcraft", args=[None])[0])

    def test_the_definition_lists_installed_apps(self):
        props = tools.tool_spec("run_craft")["parameters"]["properties"]
        self.assertEqual(props["app"]["enum"], ["wordcraft"])

    def test_a_craft_skill_keeps_the_agent_in_its_folder_note(self):
        from axis import agents
        self.assertTrue(any(t in agents.tool_names(agents.Agent(name="a", skills=["wordcraft"])) for t in tools.FOLDER_TOOLS))


if __name__ == "__main__":
    unittest.main()
