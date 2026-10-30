# Architecture

This page explains how Axis is put together, so you can find the right file before changing anything. Each module also begins with a docstring describing its own job in more detail; read that next.

## The big picture

```
            your browser (Axis window)                         your computer
  +-----------------------------------------+
  |  axis/web/index.html                    |      HTTP + JSON, 127.0.0.1 only
  |  one page: HTML, CSS, plain JavaScript  | <------------------------------------+
  +-----------------------------------------+                                      |
                                                                                   v
  +--------------------------------------------------------------------------------------+
  |  axis/server.py   ThreadingHTTPServer, routes /api/*                                 |
  |     |                                                                                |
  |     +-- chat ------> runner.py --> backend.py --------> Ollama / llama-server        |
  |     |                   |                                                            |
  |     |                   +--> tools.py, web_tools.py  (agent actions)                 |
  |     +-- agents.py, skills.py, chats.py           (JSON files under ~/Axis)           |
  |     +-- craft_skills.py                          (one skill for each Craft app)      |
  |     +-- images.py ---------------------------------> sd-server / sd-cli              |
  |     +-- ocr.py ------------------------------------> tesseract / ocr_worker.py       |
  |     +-- manager.py, jobs.py, hub.py, catalog.py      (install, start, stop, delete)  |
  |     +-- engines.py                                   (download llama.cpp, sd.cpp)    |
  |     +-- artcraft.py                                  (Craft apps and Rust: install, open) |
  |     +-- procs.py                                     (programs Axis started)         |
  |     +-- terminal.py                                  (shells: Terminal, Workspace)   |
  |     +-- updates.py                                   (update check against GitHub)   |
  +--------------------------------------------------------------------------------------+
         ^
         |  started by
  axis/cli.py (`axis`, `axis setup`, `axis update`, ...)  -->  appwindow.py opens the window
```

## Start-up and shut-down

1. `axis` runs `cli.main`, which builds the argument parser and calls the command.
2. For the default command, `server.make_server` binds `127.0.0.1` on port 8765, or the next free port.
3. `instances.py` records the running copy so `axis quit` can find it later.
4. `engines.py` installs llama.cpp and stable-diffusion.cpp in the background on the first start, unless turned off.
5. `appwindow.py` opens the page in a Chromium "app mode" window, or a normal tab as a fallback.
6. The page reports itself every few seconds (`POST /api/page`). When the last window closes, `server.Pages` notices and Axis stops.
7. On exit, `procs.py` stops every program Axis started. On Windows a job object guarantees this even if Axis is killed; on macOS and Linux the next Axis adopts any leftovers.

## A chat message, end to end

1. The page collects the conversation and posts it to `/api/chat`.
2. `server._chat` loads the agent, attaches pictures (`attach_images`), and optionally the terminal output.
3. `agents.py` assembles the system prompt, skill instructions, and per-message config.
4. `runner.py` streams the reply through `backend.py`. When the model calls a tool, `runner.py` runs it through `tools.py` or `web_tools.py` and continues. It also recovers tool calls written as plain text and protects against loops.
5. Each event (text, tool call, tool result, statistics) is written to the page as one line of JSON.
6. The page draws the events as they arrive, then saves the chat with `PUT /api/chats/<id>`.

Editing an earlier message happens entirely in the page: it keeps the messages before the edited one, replaces that message, saves the chat, and sends again from there.

## Where state lives

| State | Where | Module |
|-------|-------|--------|
| Agents | `~/Axis/agents/<name>/agent.json` | `agents.py` |
| The workspace folder agents work in | `~/Axis/shared`, or the folder named in `~/Axis/workspace-settings.json` | `paths.py` |
| Pictures Axis makes | `~/Axis/images` | `images.py`, `paths.py` |
| Custom skills | `~/Axis/skills/<id>.json` | `skills.py` |
| Edits to built-in skills | `~/Axis/skills/built-in/<id>.json` (removed by Reset to default) | `skills.py` |
| Chats | `~/Axis/chats/<id>.json` | `chats.py` |
| Engine and model settings | `~/Axis/*-settings.json`, `~/Axis/image-models/` | `manager.py`, `images.py`, `ocr.py` |
| Programs Axis started | `~/Axis/run/` | `procs.py`, `instances.py` |
| Downloaded models | `~/Axis/models/` | `hub.py`, `manager.py`, `ocr.py` |
| Engines | `~/Axis/engines/` | `engines.py`, `ocr.py` |
| Craft apps and portable Rust | `~/Axis/engines/artcraft/`, `~/Axis/engines/rust/`, `~/Axis/engines/artcraft.json` | `artcraft.py` |
| Interface preferences (theme, drafts, open chat) | the browser's local storage | `web/index.html` |

`paths.py` is the single source for the data folder; it honours `AXIS_HOME`.

## The page

`axis/web/index.html` holds everything the user sees. Its script is organised in sections marked with comment banners (`/* ---- chat */`, `/* ---- editor */`, and so on). Conventions:

- `h(tag, attrs, ...children)` builds DOM elements; `fill(el, ...)` replaces an element's children. There is no virtual DOM; each `render*` function redraws its area from the state object `S`.
- `api(path, options)` calls the JSON API and throws an `Error` with the server's message on failure.
- Colours come only from CSS custom properties on `:root`. Dark values apply when the system is dark and `data-theme` is not `light`, or when `data-theme` is `dark`.
- `store` and `recall` wrap `localStorage` and never throw.

## Safety boundaries

- **Requests:** `Handler._trusted` accepts only requests whose `Host` (and `Origin`, when present) is this Axis.
- **Paths:** `tools.resolve` turns any user or agent path into an absolute path and refuses anything outside the folder. Every file route uses it.
- **Python:** `tools.py` runs agent code with a guard that blocks writes, listings, folder changes and process launches outside the folder, and reads outside it except Python's own files and the system's program folders, plus a time limit. It is a guard against mistakes, not a sandbox.
- **Terminal and Rust code from agents:** `tools.check_terminal_code` and `tools.check_rust_code` read the code before it runs and refuse it when it names a place outside the folder. Nothing watches the program while it runs.
- **Chosen folders:** `paths.check_work_folder` checks every folder chosen for the workspace or a chat: it must exist, and it may not be the top of a drive or inside `~/Axis` (except `~/Axis/shared`).
- **Downloads:** `hub.py` and `engines.py` verify sizes and SHA-256 checksums before using a file.

## Tests

The tests in `tests/` start fake Ollama, llama.cpp, stable-diffusion.cpp, Tesseract, PyTorch, Hugging Face, and GitHub servers in-process, so no network or real model is needed. See [development.md](development.md).
