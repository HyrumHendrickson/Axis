# HTTP API

The page talks to Axis through a small JSON API served on `127.0.0.1`. It exists for the page; it is documented so contributors can work on either side, and so that scripts on the same computer can use it. It is not a stable public interface yet: routes may change between minor versions, and changes are listed in the [changelog](../CHANGELOG.md).

## Conventions

- **Address.** `http://127.0.0.1:8765` by default (the port printed when Axis starts).
- **Who may call it.** Requests must be addressed to `127.0.0.1:<port>` or `localhost:<port>`. A request with an `Origin` header from any other site is refused with `403`. This is what stops other websites in your browser from using Axis.
- **Bodies** are JSON (`Content-Type: application/json`), up to 5 MB, except uploads and saves, which send the file's bytes.
- **Errors** are `{"error": "message"}` with status `400`, or `404` when something does not exist. The message is written for the user and is shown as-is.
- **Streams.** `/api/chat`, `/api/images/generate` and `/api/ocr/run` answer with newline-delimited JSON: one event per line, sent as soon as it happens.
- **Places.** File routes take `<name>`, which is an agent's name or `_shared` for the shared folder. Paths in `path` and `to` are relative to that folder and can never leave it.
- **Source of truth.** The route list at the top of `axis/server.py`. When you add or change a route, update both.

## Axis itself

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/` | the app |
| `GET` | `/api/ping` | identifies a running Axis |
| `POST` | `/api/quit` | stops Axis (and the models it started) |
| `POST` | `/api/page?id=x[&closing=1]` | an open page says it is still there (or is closing) |
| `GET` | `/api/status` | model server and loaded models |
| `GET` | `/api/update` | installed and newest version (the first call starts the check) |
| `POST` | `/api/update` | download and install the newest Axis in the background |
| `POST` | `/api/update/check` | check GitHub again now |
| `GET` | `/api/models` | details for each model (memory, context, size) |

## Chat

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/api/chat` | stream a reply as newline-delimited JSON (a user message may carry "images": [{place, path}] for models that read pictures; "terminal": id shows the agent that terminal's newest commands and their output, and "terminal_commands": n how many (0 to 100, 0 for none, default 5); "workspace": {place, path, file} shows the agent the Workspace folder the user is looking at, as names, sizes, and item counts only; "folder": a full path keeps the agent in that folder for this chat instead of the workspace folder) |
| `GET` | `/api/workspace` | the workspace folder {folder, default, is_default, missing} (missing names a chosen folder that is gone) |
| `PUT` | `/api/workspace` | use another folder as the workspace {folder}; `""` goes back to `Axis/shared`. The top of a drive and folders inside `Axis` (except `Axis/shared`) are refused |
| `GET` | `/api/folders?path=p` | the folders inside p, for choosing a folder {path, parent, folders: [{name, path}], more, roots, home, usable}; without path, the home folder. Folder names only, never files |

## Agents

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/agents` | list agents |
| `POST` | `/api/agents` | create an agent |
| `GET` | `/api/agents/<name>` | one agent |
| `PUT` | `/api/agents/<name>` | update (or rename) an agent |
| `DELETE` | `/api/agents/<name>` | delete an agent and its folder |
| `GET` | `/api/agents/<name>/files` | every file in the agent's folder (flat list) |
| `POST` | `/api/agents/<name>/open-folder` | show the folder in Explorer/Finder |

## Files (Workspace)

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/files/<name>?path=dir` | one folder of an agent's folder (the Workspace tab) (<name> "_shared" means the workspace folder, "_images" the folder of pictures Axis made) |
| `GET` | `/api/files/<name>/view?path=file` | a file prepared for preview |
| `GET` | `/api/files/<name>/raw?path=file` | the file itself (images, downloads) |
| `POST` | `/api/files/<name>/reveal?path=x` | select the file in Explorer/Finder |
| `POST` | `/api/files/<name>/upload?path=dir&name=f` | save the request body as a file (&mkdirs=1 makes the folder first when it is missing) |
| `POST` | `/api/files/<name>/mkdir?path=dir` | make a folder |
| `POST` | `/api/files/<name>/create?path=file` | make a new, empty file in a folder that exists (`201`; `400` if the name is taken or unusable; never replaces a file) |
| `POST` | `/api/files/<name>/rename?path=x&to=name` | rename a file or folder in place; `to` is a plain name, not a path. Never replaces anything. Returns `{renamed: new path}` |
| `POST` | `/api/files/<name>/move?path=x&to=dir` | move a file or folder into another folder of the same place |
| `PUT` | `/api/files/<name>/save?path=file` | replace a text file with the request body (UTF-8 text) |
| `POST` | `/api/files/<name>/run?path=file` | run a Python, Rust, or terminal program selected by extension (`.py`, `.rs`, `.sh`, `.ps1`, `.bat`, and related files) and return its output |
| `DELETE` | `/api/files/<name>?path=x` | delete a file or folder |

## Skills

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/skills` | list skills (built-in and custom). Each has `group`: `"Art Craft apps"` for the skill of a Craft app, otherwise empty |
| `POST` | `/api/skills` | create a custom skill |
| `GET` | `/api/skills/<id>` | one skill |
| `PUT` | `/api/skills/<id>` | update a skill; for a built-in skill only `name`, `description`, and `instructions` change, and the edit is kept apart from the default |
| `DELETE` | `/api/skills/<id>` | delete a custom skill (and remove it from agents) |
| `GET` | `/api/skills/<id>/sends` | exactly what the skill sends to the model: `system` (text added to the system message), `tools` (action definitions), `tools_text` (the same actions as text), `notes` |
| `POST` | `/api/skills/<id>/reset` | put a built-in skill back to its default text (`400` for a custom skill) |

## Saved chats

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/chats` | saved chats (newest first, without messages) |
| `GET` | `/api/chats/<id>` | one saved chat with its messages |
| `PUT` | `/api/chats/<id>` | create or replace a saved chat {agent, title?, folder?, messages}; folder is the chat's own folder ("" for the workspace folder) |
| `PATCH` | `/api/chats/<id>` | rename a saved chat |
| `DELETE` | `/api/chats/<id>` | delete a saved chat |

## Terminal

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/terminal` | open terminals |
| `POST` | `/api/terminal` | open a terminal {place?, path?, shell?} in that folder (default: the workspace folder); Windows accepts `cmd`, `powershell`, or `pwsh` |
| `GET` | `/api/terminal/<id>?since=n` | output from offset n {text, next, reset, alive, exit} |
| `POST` | `/api/terminal/<id>/input` | type into the terminal {text} |
| `POST` | `/api/terminal/<id>/interrupt` | stop the running command (Ctrl+C) |
| `DELETE` | `/api/terminal/<id>` | close a terminal |

## Images

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/images` | sd-cli status, settings and image models |
| `PUT` | `/api/images/settings` | set the sd-cli path, working folder, default model |
| `POST` | `/api/images/models` | add an image model |
| `PUT` | `/api/images/models/<id>` | change an image model |
| `DELETE` | `/api/images/models/<id>` | remove an image model (its files are not touched) |
| `GET` | `/api/images/gallery` | pictures Axis made, in `Axis/images` (newest first); read them through the file routes as `_images` |
| `POST` | `/api/images/generate` | make a picture, streaming progress as newline-delimited JSON ("preview": true also streams each step as a small picture; "init_image": {place, path} and "strength" edit that picture) |

## OCR

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/ocr` | Tesseract, the PyTorch environment, settings and OCR models |
| `PUT` | `/api/ocr/settings` | Tesseract path, Python for PyTorch, device, PyTorch package index |
| `POST` | `/api/ocr/run` | read the text in a picture {model, image: {place, path}, languages?, psm?}, streaming progress as newline-delimited JSON |

## Models and engines

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/manage` | engines, installed models, what is running, and jobs |
| `POST` | `/api/manage/install` | install a model {engine, name, choice?, role?, target?} (engine "ocr" takes {kind: tesseract&#124;easyocr&#124;doctr, name}) (answers {choose: [...]} when the user must pick a file; an image name such as z-image-turbo installs every file it needs) |
| `POST` | `/api/manage/start` | start a model {engine, model} |
| `POST` | `/api/manage/stop` | stop a model {engine, model} |
| `POST` | `/api/manage/delete` | delete a model and its files {engine, model, files?} |
| `POST` | `/api/manage/ollama/start` | start the Ollama server |
| `POST` | `/api/manage/ollama/stop` | stop the Ollama server (only if Axis started it) |
| `POST` | `/api/manage/engines/install` | install or update llama.cpp, stable-diffusion.cpp, Tesseract or PyTorch for OCR {engine: llama&#124;sd&#124;tesseract&#124;pytorch, build?} |
| `POST` | `/api/manage/engines/remove` | remove the copy Axis installed {engine} |
| `PUT` | `/api/manage/settings` | llama-server path, context size, extra arguments |
| `POST` | `/api/manage/jobs/<id>/cancel` | cancel an install or start |
| `DELETE` | `/api/manage/jobs/<id>` | remove a finished job from the list |

## Art Craft

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/artcraft` | the 13 Craft apps and Rust: what is installed (version, path), whether this computer is supported, and the Art Craft install jobs |
| `POST` | `/api/artcraft/install` | install an app, or Rust, in the background: `{"app": "photocraft"}` or `{"app": "rust"}`. Answers `202` with `{"job": ...}`; refuses what is already installed |
| `POST` | `/api/artcraft/open` | start an installed app: `{"app": "photocraft"}`. It is not stopped when Axis stops |

| `POST` | `/api/artcraft/check` | ask whether newer versions exist for what is installed (each app's `check` field: `update`, `latest`, or `error`); answers like `GET /api/artcraft` |
| `POST` | `/api/artcraft/update` | update an installed app, or Rust, in the background: `{"app": ...}`. Answers `202` with `{"job": ...}` |
| `POST` | `/api/artcraft/remove` | delete an installed app (all its files), or Rust: `{"app": ...}` |

These jobs are not listed by `GET /api/manage`. Cancel one, or clear a finished one, with the `/api/manage/jobs/<id>` routes above.

## CAS

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/cas` | Maxima and SymPy (`systems`: installed, version, path, how Axis would install it), `settings`, the `default` agents use, and the CAS install jobs |
| `PUT` | `/api/cas/settings` | `{"maxima": "path"}` and/or `{"default": "maxima" \| "sympy" \| ""}`; answers like `GET /api/cas` |
| `POST` | `/api/cas/install` | install Maxima or SymPy in the background: `{"system": "maxima"}`. Answers `202` with `{"job": ...}`; refuses what is already installed |
| `POST` | `/api/cas/remove` | delete the SymPy environment: `{"system": "sympy"}`. A system-wide Maxima is never removed |
| `POST` | `/api/cas/run` | run code in the workspace folder: `{"system"?: "maxima" \| "sympy", "code": "..."}`. Answers `{system, exit, stdout, stderr, timed_out, question, saved?}`; `question` is what Maxima stopped to ask, and `saved` the files a run moved into the folder |

These jobs are not listed by `GET /api/manage` either; use the `/api/manage/jobs/<id>` routes.

## Example

```bash
# list the files at the top of the shared folder
curl -s 'http://127.0.0.1:8765/api/files/_shared?path='

# make a file, then rename it, inside agent 'writer'
curl -s -X POST 'http://127.0.0.1:8765/api/files/writer/create?path=notes/draft.md'
curl -s -X POST 'http://127.0.0.1:8765/api/files/writer/rename?path=notes/draft.md&to=final.md'

# move notes/today.md into archive/ inside agent 'writer'
curl -s -X POST 'http://127.0.0.1:8765/api/files/writer/move?path=notes/today.md&to=archive'
```
