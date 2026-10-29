# Instructions for AI coding agents

This file is for AI coding agents (Claude Code, Codex, Copilot, Cursor, and similar) working on this repository. Human contributors should read [CONTRIBUTING.md](CONTRIBUTING.md); everything here applies to them too.

## What Axis is

Axis is a local desktop app for chatting with AI models and building agents. A Python server (`axis/`) that uses only the standard library serves a single HTML page (`axis/web/index.html`) on `127.0.0.1`. It installs, starts, and stops local model engines: llama.cpp, stable-diffusion.cpp, Ollama, Tesseract, and PyTorch OCR.

Read [docs/principles.md](docs/principles.md) before making any design decision, and [docs/architecture.md](docs/architecture.md) to find the right file.

## Hard constraints

Never do any of the following, even if a task seems to call for it. Stop and ask the human instead.

1. **Add a dependency to the `axis` package.** `dependencies = []` in `pyproject.toml` must stay empty. Use the standard library. Heavy work belongs in a separate process with its own environment (see `axis/ocr_worker.py`).
2. **Add a build step, framework, bundler, or CDN to the page.** `axis/web/index.html` is one hand-written file of HTML, CSS, and plain JavaScript, with no external URLs.
3. **Add accounts, login, telemetry, analytics, crash reporting, or any call to a cloud service.**
4. **Weaken a safety check:** `Handler._trusted` in `axis/server.py`, `tools.resolve` and the Run Python guard in `axis/tools.py`, or checksum verification in `axis/hub.py` and `axis/engines.py`.
5. **Overwrite or delete user data** without an explicit user action. Uploads and moves never replace existing files.
6. **Change the version** in `axis/VERSION` unless asked. It is the only place the version lives.

## Commands

```bash
python3 -m unittest discover -s tests            # the whole suite: no network or model needed
python3 -m unittest discover -s tests -k TestFileManagement   # one test class
python3 -m axis --no-browser                     # run from the clone; open the printed address
```

Run the full suite before you finish. It must pass.

## Where things are

| To change | Look in |
|-----------|---------|
| Anything the user sees or clicks | `axis/web/index.html` |
| An API route | `axis/server.py` (`Handler._dispatch`), and the route list at the top of the same file |
| How a reply is produced, or tool calling | `axis/runner.py`, `axis/backend.py` |
| An agent action | `axis/tools.py`, `axis/web_tools.py`, `axis/skills.py` |
| Installing, starting, or stopping models | `axis/manager.py`, `axis/jobs.py`, `axis/procs.py`, `axis/hub.py`, `axis/catalog.py` |
| Engine downloads | `axis/engines.py` |
| Images or OCR | `axis/images.py`, `axis/ocr.py`, `axis/ocr_worker.py` |
| Command-line commands | `axis/cli.py` |

## Conventions

**Python**
- Follow the style of the surrounding code. Use type hints, `pathlib`, and a module docstring that says what the module is for.
- Raise the module's own error class (`AgentError`, `ManageError`, and so on) with a plain-language message. The server turns it into `400`, or `404` when the message contains "does not exist", and the page shows it as-is.
- Any path that comes from a user or an agent goes through `tools.resolve`.
- Python 3.9 must still work: no `match` statements and no `X | Y` type unions at runtime. `from __future__ import annotations` is fine.

**The page**
- Build elements with `h(tag, attrs, ...children)` and replace content with `fill(el, ...)`.
- Keep state in `S`, and redraw with the existing `render*` functions.
- Call the API with `api()`.
- Use only the CSS variables on `:root` for colour, so the light and dark themes both work. Check both.
- Preferences that belong to one browser use `store()` and `recall()`; anything else is saved by the server.

**Tests**
- Every behaviour change needs a test in `tests/`. Use the fake servers already defined there (`OllamaHandler`, `LlamaCppHandler`, and the fakes in `test_manage.py`, `test_engines.py`, `test_ocr.py`), not the network.
- There are no browser tests in the suite. After changing the page, at minimum check that its script parses: extract the main `<script>` and run `node --check`, if Node is available.

**Documentation**
- A user-visible change updates `docs/user-guide.md`.
- A new option or environment variable updates `docs/configuration.md`.
- A route change updates the route list in `axis/server.py` and `docs/api.md`.
- Every change adds a line under **Unreleased** in `CHANGELOG.md`.
- Write for non-programmers: short sentences, bold for on-screen labels, no jargon where an ordinary word will do.

## Working style

- Make the smallest change that solves the task. Do not reformat, rename, or reorganise code you were not asked to touch.
- Read the module's docstring and the neighbouring code before editing.
- Do not invent APIs, flags, or file formats for the engines. Check the existing code or the engine's documentation.
- Explain in your summary any trade-off you made or anything you could not verify, such as behaviour on an operating system you could not test.
