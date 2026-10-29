# Development

How to work on Axis from a clone. Read [CONTRIBUTING.md](../CONTRIBUTING.md) for the contribution process and [principles.md](principles.md) for what changes fit.

## Set up

Python 3.9 or newer and Git are all you need.

```bash
git clone https://github.com/HyrumHendrickson/Axis.git
cd Axis
pipx install --editable .
```

With `--editable`, the `axis` command runs your clone directly, so a change takes effect the next time Axis starts. To run Axis without installing it:

```bash
python3 -m axis --no-browser            # prints the address to open
AXIS_HOME=/tmp/axis-dev python3 -m axis # keep test data apart from your real data
```

Use a separate `AXIS_HOME` while developing, so your real agents and chats are not touched. Setting `AXIS_NO_ENGINE_INSTALL=1` skips the engine downloads on first start, and `AXIS_NO_UPDATE_CHECK=1` stops the update check.

## Tests

```bash
python3 -m unittest discover -s tests -v            # everything
python3 -m unittest tests.test_axis.TestServer      # one class
python3 -m unittest tests.test_axis.TestFileManagement.test_move_into_folder   # one test
```

On Windows use `py`. The suite starts fake Ollama, llama.cpp, stable-diffusion.cpp, Tesseract, PyTorch, Hugging Face, and GitHub servers inside the test process, so it needs no network, no model, and no GPU, and every test uses a temporary `AXIS_HOME`.

| File | Covers |
|------|--------|
| `tests/test_axis.py` | Server routes, agents, skills, chats, tools, the runner, the CLI, the shortcut and app window, and Workspace files |
| `tests/test_manage.py` | Installing, starting, stopping, and deleting models |
| `tests/test_engines.py` | Installing engines from (fake) GitHub releases, and image models by name |
| `tests/test_ocr.py` | Tesseract, the PyTorch environment, and OCR models |
| `tests/test_updates.py` | The update check |

The page has no automated tests. After changing `axis/web/index.html`, check that its script parses (with Node.js installed):

```bash
python3 -c "import re;s=open('axis/web/index.html').read();open('/tmp/axis.js','w').write(re.findall(r'<script>(.*?)</script>',s,re.S)[-1])"
node --check /tmp/axis.js
```

Then try the change by hand in both the light and the dark theme, and in a narrow window.

## Project layout

```
axis/
  __init__.py     reads the version from VERSION
  cli.py          the `axis` command and its subcommands
  server.py       local web server and JSON API (the route list is at the top)
  web/index.html  the whole interface: HTML, CSS, and JavaScript in one file
  agents.py       agent storage, folders, and message assembly
  skills.py       the skill library (built-in actions and custom skills)
  tools.py        agent actions, confined to each agent's folder
  web_tools.py    web search, web pages, and Wikipedia
  runner.py       one reply, with tool use along the way
  backend.py      finds Ollama and llama.cpp servers and streams replies
  chats.py        saved conversations
  images.py       image models, sd-cli and sd-server, and the gallery
  ocr.py          OCR with Tesseract and PyTorch models
  ocr_worker.py   runs PyTorch OCR inside its own environment
  manager.py      installs, starts, stops, and deletes models
  engines.py      installs llama.cpp and stable-diffusion.cpp from GitHub releases
  catalog.py      short model names and the files each needs
  hub.py          Hugging Face names and verified, resumable downloads
  jobs.py         long operations in the background, with progress
  procs.py        programs Axis starts: logs, readiness, stopping, crash recovery
  terminal.py     shells for the Terminal tab
  instances.py    running copies of Axis, for `axis quit`
  appwindow.py    opens the page in its own window
  shortcut.py     the Start menu, Applications, or app menu launcher
  updates.py      the update check and installing updates from the page
  paths.py        where Axis keeps its data
  icons/          the app icon (.ico, .icns, .png)
  VERSION         the version number, and nothing else
tests/            unittest suites with fake servers
docs/             documentation
ModelInstallHelps/  standalone scripts to install stable-diffusion.cpp by hand
```

[architecture.md](architecture.md) explains how these fit together.

## Releasing

The version lives only in `axis/VERSION`. Users running `axis update`, or pressing **Update now**, get whatever is on `main`.

1. Move the **Unreleased** entries in `CHANGELOG.md` under a new version heading with today's date.
2. Change `axis/VERSION`: the patch number for fixes, the minor number for features, and the major number for changes that break existing data or habits.
3. Run the full test suite.
4. Commit, then tag: `git tag v4.1.1 && git push --tags`.
5. Push to `main`. Running copies of Axis will report **Out of date** within half an hour.

## Continuous integration

`.github/workflows/tests.yml` runs the test suite on Linux, macOS, and Windows, on the oldest and newest supported Python, for every push and pull request. It uses only the standard library, like Axis itself.
