# Design principles

These principles decide what goes into Axis and how it is built. When a proposal conflicts with one of them, the principle wins unless the maintainers agree to change it here first.

## 1. Local only

Axis runs on the user's computer and serves its page only to that computer. Models run in local engines. There is no Axis server, no hosted service, and no feature that requires one.

- Nothing about the user, their chats, their files, or their usage is ever sent anywhere.
- Axis goes online only for things the user asked for or can turn off: downloading engines and models, the update check against this repository (`AXIS_NO_UPDATE_CHECK=1`), and web actions an agent was given.
- The page refuses requests from other sites (see `_trusted` in `axis/server.py`). Do not weaken that check.

## 2. No accounts

There is no sign-up, login, licence key, or user database. The person at the keyboard owns the data. Gated Hugging Face models use the user's own `HF_TOKEN`; Axis never asks for credentials of its own.

## 3. Standard library only

The `axis` package depends on nothing outside the Python standard library (`dependencies = []` in `pyproject.toml`). This keeps installs fast and small, removes supply-chain risk, and means Axis runs on any Python 3.9 or newer.

- A feature that needs a heavy library runs it out of process, in its own environment, the way the PyTorch OCR models run in `~/Axis/engines/ocr-python` through `axis/ocr_worker.py`.
- Engines (llama.cpp, stable-diffusion.cpp, Tesseract) are separate programs that Axis downloads, verifies, and starts. They are never linked into Axis.

## 4. One page, no build step

The whole interface is `axis/web/index.html`: HTML, CSS, and plain JavaScript in one file, served as-is. There is no bundler, transpiler, package manager, or framework, and no file is fetched from a CDN at runtime. Anyone can read and change the interface with a text editor.

## 5. Light and fast

Axis should start almost instantly and use very little memory next to the models it runs. Prefer simple code over clever code, and do the work on demand rather than in the background. Polling intervals, file sizes, and history lengths are bounded on purpose; keep them bounded.

## 6. Your data is plain files

Agents, skills, chats, settings, and models live under one folder (`~/Axis`, or `AXIS_HOME`) as JSON and ordinary files. A user can back it up, inspect it, or move it with ordinary tools. Axis never deletes user data on its own; deleting an agent deletes its chats and folder only because the user asked.

## 7. Safe by default

- File actions are confined to the agent's own folder (and `shared/` when allowed). Paths that escape are refused.
- Downloads are checked against published sizes and SHA-256 checksums before use.
- Files are never silently overwritten by uploads, moves, or downloads.
- Run Python has a guard and a time limit. It is not a security sandbox, and the documentation says so plainly.

## 8. Plain language

Messages, labels, and documentation are written for someone who is not a programmer. Say what happened and what to do next. Avoid jargon where an ordinary word will do.

## 9. Works everywhere

Windows, macOS, and Linux are equal targets. A feature that cannot work on one of them needs a clear fallback there.

## What Axis is not

- Not a hosted chat service, and not a client for one. Support for an OpenAI-compatible server exists for servers the user runs locally.
- Not a model trainer or fine-tuning tool.
- Not a security sandbox for untrusted code.
- Not a full IDE. The Workspace is a light editor (create, rename, edit, and run files, with a terminal and an agent chat beside them) for working on what agents make. It has no debugger, language servers, or extensions, and will not grow them.

## Judging a change

Before proposing or merging a change, ask:

1. Does it keep every principle above?
2. Does it add a dependency, a build step, or a network call? If so, it needs a strong reason and a maintainer's agreement.
3. Does it make Axis slower to start or heavier to run?
4. Is it tested, and documented where users will look?
5. Could a new contributor understand it from the code and comments alone?
