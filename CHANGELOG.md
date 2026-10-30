# Changelog

All notable changes to Axis are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Axis uses [semantic versioning](https://semver.org/): a major version for changes that break existing data or habits, a minor version for new features, and a patch version for fixes and small improvements.

## [Unreleased]

## [4.3.3] - 2026-10-29

### Fixed
- The loop guard that answers "you already did exactly this" now forgets earlier file listings and file contents after a file changes, so a rewritten file or a changed folder is no longer reported as it was before.
- The preview on the Skills page of what a skill sends to the model now matches what a chat sends: an agent with an action that works with files is told about the one shared folder.

### Changed
- The test suite follows the current design: one shared workspace for all agents, and no separate file, image-generation or shared-folder skills.

## [4.3.2] - 2026-10-29

### Added
- **One skill for each Art Craft app.** PhotoCraft, LightCraft, FilmCraft, DesignCraft, VectorCraft, PdfCraft, EffectCraft, WordCraft, DeckCraft, GridCraft, CADCraft, SoundCraft and FigCraft each have a built-in skill that teaches an agent the app's command line: how to look commands up, write their parameters, run them, and check the result. They were written from each app's documentation and checked against the released Linux programs. Each gives the agent the Run terminal code actions. They are grouped under **Art Craft apps** on the Skills page and in an agent's settings, and can be edited and reset like other built-in skills.
- Programs an agent starts now find the `<app>-cli` program of every installed Art Craft app on their PATH. Axis does not change your own PATH. API: skills have a `group` field.

### Changed
- **Workspace:** the folder menu is gone. There is only one shared folder, so it had nothing to choose.

### Notes
- The macOS download of a Craft app holds only the app, so the new skills need the separate command-line zip there; Axis does not install it yet.

- Fixed Windows Rust setup so Axis loads `vcvarsall.bat` with `call` and detects the C++ Build Tools installed under `Axis/engines/msvc` without requiring a restart.
- Fixed the Windows MSVC architecture argument: native x64 now uses Microsoft’s `amd64`/`vcvars64.bat` configuration instead of the invalid `x64` argument.

## [4.2.2] - 2026-10-29

### Added
- **Run Rust skill.** Agents can compile and run Rust code or `.rs` files with `run_rust` and `run_rust_file`. Axis uses the portable Rust from Art Craft when available, and Windows Rust setup downloads Microsoft's C++ Build Tools when the MSVC linker needs them.
- **Run terminal code skill.** Agents can run terminal-language code or shell files with `run_terminal` and `run_terminal_file`; bash or sh is used on macOS and Linux, and cmd, PowerShell, or pwsh can be selected on Windows.
- **Workspace program runner.** The Workspace **Run** button now runs Python, Rust, bash/sh, PowerShell, and cmd files and displays their output in the bottom panel.
- Windows C++ Build Tools downloads for Rust use unique temporary installer names, so a locked or interrupted previous download no longer causes WinError 5.

- **Art Craft: Update and Delete.** The tab checks once when it opens (and on **Check for updates**) and marks each installed app, and Rust, as **Update available** or up to date. **Update** installs the newer version beside the old one, copies settings folders kept beside the old program, then removes the old version. **Delete** removes an app or Rust. API: `POST /api/artcraft/check`, `/update`, `/remove`.

## [4.2.1] - 2026-10-29

### Added
- **Art Craft tab.** One **Install** button for each of the 13 Craft apps (PhotoCraft, LightCraft, FilmCraft, DesignCraft, VectorCraft, PdfCraft, EffectCraft, WordCraft, DeckCraft, GridCraft, CADCraft, SoundCraft, FigCraft), which downloads the portable build from the app's latest GitHub release, checks its SHA-256, and unpacks it into `Axis/engines/artcraft`. The button becomes **Open** once the app is installed. Installs show progress and can be cancelled.
- **Install Rust** installs a portable Rust with rustup into `Axis/engines/rust`, without changing the PATH. Apps opened from Axis find it on their PATH.
- API: `GET /api/artcraft`, `POST /api/artcraft/install`, `POST /api/artcraft/open`.
- `docs/user-guide.md` describes the tab.

### Notes
- The version goes from 4.1.2 to 4.2.1 as requested; there is no 4.2.0.
- An installed app is never installed over, so its settings are never deleted. The macOS install path (copying the app out of the disk image) has not been run on a Mac.

## [4.1.2] - 2026-10-29

### Added
- **Workspace is now a light editor.** **New file** and **New folder** add a name box to the list (Enter saves, Esc cancels); a new file opens ready to type in. **Rename** any file or folder with the pencil on its row, **F2**, or **Rename** above an open file. Nothing is ever replaced: a taken name is refused. The folder tools moved to a bar under the folder path, with **More** holding **Refresh**, **Show in folder**, **Open a terminal here**, and **Delete this folder**.
- **Bottom panel in Workspace.** **Terminal** shows or hides a panel under the files with a tab for each terminal and an **Output** tab for Python files you run. Drag its top edge (or use the arrow keys) to resize it. The terminal button on a folder row and **Open a terminal here** now open a terminal in this panel.
- **Agent chat in Workspace.** **Agent** shows or hides a chat with an agent beside the files. With each message the agent is shown the folder you are in (names, sizes, and item counts only, never file contents), the open file's name, and the terminal's newest output only while the terminal is showing. Which panels are open, their size, and the chosen agent are remembered in the browser.
- **Skills can be edited and inspected.** **Edit** now works on built-in skills (name, description, instructions; their actions stay fixed), with **Reset to default** and an **Edited** tag. Every skill has a **What this sends to the model** dropdown showing, exactly as sent, the system text, the action definitions, and the same actions as text. Edits are kept in `skills/built-in/`.
- API: `POST /api/files/<n>/create`, `POST /api/files/<n>/rename`, `GET /api/skills/<id>/sends`, `POST /api/skills/<id>/reset`, and a `workspace` field on `POST /api/chat`.
- Documentation of features that were already in Axis but missing from the user guide: pictures in chats (**Attach picture**, paste, drop, **From Workspace**) and **Edit a picture** on the Images tab.

### Changed
- **Terminals are typed in directly.** The separate input box under the output is gone; your text appears after the prompt with a cursor, and Enter runs it. Cursor keys, Home and End, word movement, Ctrl+U, Ctrl+K, Ctrl+W, command history, and Page Up and Page Down work, and Ctrl+C copies selected text. A pasted line is placed on the line and not run until Enter; pasting several lines asks first.
- Axis now echoes what is typed into a bash terminal itself (bash runs with `--noediting`), as it already did for `sh`. An answer typed to a program that reads input, such as Python's `input()`, now stays visible.
- Python file output moved from a column beside the file to the **Output** tab of the bottom panel, so the Workspace stays readable beside the agent chat.
- The Workspace's **Agent** picker is now called **Folder**, since it chooses whose folder is shown. **Delete folder** moved into **More**.
- Skill cards show their text in the **What this sends to the model** dropdown instead of inline.
- `docs/principles.md` now says Axis is not a *full* IDE: the Workspace is a light editor with no debugger, language servers, or extensions.

### Fixed
- The test workflow uses `actions/checkout@v6` and `actions/setup-python@v6` (Node 24), and cancels a run that a newer push replaces.
- Tests: deleting an agent whose folder, or a folder inside it, was read-only failed on Linux and macOS, and the test suite failed there. Deleting a folder in the **Files** tab now also removes read-only files and folders, and never follows a link out of the folder.
- Run Python: several chats using the same agent at the same time no longer delete each other's scratch files.
- Agents no longer receive an out-of-date answer to "list files" or "read file" after they wrote, changed, or deleted a file (the "you already did this" check now forgets earlier results when something changes). Repeating the same action over and over is still stopped.
- Picture making closes the output pipe of `sd-cli`, so its handles are released (Windows could not clean up the files).
- `axis uninstall` on Windows no longer fails if the PowerShell shortcut removal prints nothing.
- Closing a browser tab during a reply no longer prints error traces in the terminal.
- The test commands for a single class or test in `AGENTS.md` and `docs/development.md` failed with `ModuleNotFoundError`; they now use `unittest discover -s tests -k`.
- Terminal output could be added twice when two reads overlapped; reads now go one at a time per terminal, and lines are sent in order.

### Security
- `fetch_page` checks the address it actually connects to, so a site cannot pass the "not your own computer" check and then redirect to it (DNS rebinding). Shared addresses (`100.64.0.0/10`, used by Tailscale) are refused too, and a site that sends its answer too slowly is given up on after 45 seconds.
- Uploads claim their file name atomically, so two uploads at once, or a file that appears while one is running, can never be overwritten. Saving a text file no longer writes through a link left at a guessable temporary name. Names Windows reserves (`nul`, `con`, ...) and very long names are refused.
- The page can no longer be shown inside a frame on another website (clickjacking), and sends no referrer.
- Run Python keeps only the first 1 MB of output from each stream, so code that prints without end cannot fill Axis's memory.
- Engine downloads: a link in a `.tar.gz` that pointed to a folder whose name merely started like the install folder was accepted by the older-Python check; the check now compares whole path parts. Axis also checks for free disk space before unpacking, and never sends the GitHub token on to another address after a redirect.
- Updates: only a real 40-digit commit id from GitHub is put into the download address.
- Hugging Face downloads: the access token is sent to the same scheme, host, and port only, so a redirect from `https` to `http` does not expose it.
- `axis quit` no longer ends a process whose id was reported by whatever answered on a port. Only copies recorded by Axis itself are ended.
- `axis uninstall` on Windows writes its helper to a private folder instead of a fixed name in the shared temp folder.

## [4.1.1] - 2026-10-29

### Added
- **Edit** under each message you sent. Change the message and send the conversation again from that point. Later messages are replaced, and pictures on the message are kept. Esc cancels.
- **Copy** under each message you sent.
- **Settings** at the top right of every page, with **System default**, **Light**, and **Dark** appearance.
- Workspace: move a file or folder by dragging it onto a folder, onto **Up one folder**, or onto a part of the folder path. Files dragged from your computer onto a folder are uploaded into that folder.
- API: `POST /api/files/<name>/move?path=x&to=dir`. It never overwrites files, never leaves the folder, and never moves a folder into itself.
- Project documentation: `docs/` (installation, user guide, configuration, troubleshooting, architecture, HTTP API, development, design principles), plus `CONTRIBUTING.md`, `AGENTS.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and this changelog.
- GitHub issue and pull request templates, and a test workflow for Windows, macOS, and Linux.

### Changed
- The README is now a short introduction. The full manual moved to `docs/`.
- Fuller package metadata in `pyproject.toml`.

## Earlier versions

Changes before 4.1.1 were not recorded in a changelog. The Git history (`git log`) lists them, including saved chats, web search, image generation, model management, the Axis window and app, the update check, image editing, and OCR.
## 4.2.3

- Streamlined agent skills by removing the file-management, image-generation, and shared-folder skills. Agents can use terminal commands when enabled, while the Images tab remains available for image work.
- Reordered the sidebar tabs: Direct chat, Past chats, Agents, Workspace, Terminal, Images, Models, Art Craft, OCR, Skills.
- Added a temporary “Allow terminal commands” checkbox to Terminal and Workspace agent sidebars.
- Added a per-chat Location selector for the agent folder or Shared folder, and moved the Workspace folder selector beside the Workspace title.
