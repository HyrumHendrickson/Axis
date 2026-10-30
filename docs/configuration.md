# Running and configuring Axis

How Axis starts, how it stops, and every command, option and environment variable it understands.

## Starting Axis

In a separate terminal:

```bash
axis
```

Axis opens at `http://127.0.0.1:8765` in its own window, with no tabs or address bar, and its own entry in the taskbar or Dock. The status panel shows **Ollama is ready** (or **llama.cpp / OpenAI-compatible is ready**) once a model is on. Running `axis` while it is already running opens another window on the same Axis.

## The Axis window

The window is an "app mode" window of a Chromium-based browser: Chrome, Edge, Brave, or Chromium, in that order. Edge comes with Windows 10 and 11, so every Windows computer has one. On a Mac with only Safari, or a Linux system with only Firefox, Axis opens in an ordinary browser tab instead, as it did before 4.0.

**Closing the Axis window stops Axis.** It first stops every `llama-server`, `sd-server` and `ollama serve` it started, then exits. If a reply, a picture or an install is still running, the window asks before closing. Reloading the page does not stop Axis; it waits a few seconds for the page to come back. With several Axis windows open, Axis stops when the last one closes. If the browser crashes without closing the page, Axis stops after 10 minutes without hearing from it.

In a browser tab (`axis --tab`, `AXIS_BROWSER=tab`, or when no Chromium-based browser is found), closing the tab does **not** stop Axis; press Ctrl+C in its terminal or use **Quit Axis**. To use a different Chromium-based browser for the window, such as Vivaldi or Opera, set `AXIS_BROWSER` to its program.

## The Axis app

The first time you run `axis` (or `axis setup`), Axis adds itself to your apps, so you can open it without a terminal:

| System | Where it appears |
|--------|------------------|
| Windows | Start menu (search "Axis") and a desktop shortcut |
| macOS | `~/Applications/Axis.app`, so it shows in Launchpad and Spotlight; drag it to the Dock to keep it there |
| Linux | Your desktop's app menu (`~/.local/share/applications/axis.desktop`) |

Opening the app starts Axis in the background and opens the Axis window; no terminal window is shown. Close the window to stop Axis, or use **Quit Axis** at the top right of the page (needed when Axis fell back to a browser tab). Its output goes to `axis.log` in your data folder. The `axis` command still works, and both open the same Axis.

Run `axis shortcut` to make the app again (for example after deleting it), `axis shortcut --remove` to remove it (Axis will not add it back), and `axis shortcut --no-desktop` on Windows to skip the desktop shortcut. `axis uninstall` removes it. Set `AXIS_NO_SHORTCUT=1` to stop Axis from adding it on the first run.

To get the app straight after installing, without starting Axis first:

```bash
pipx install git+https://github.com/HyrumHendrickson/Axis.git && axis shortcut
```

When Axis stops, it also stops every `llama-server` and `sd-server` it started, and `ollama serve` if Axis started it. Models loaded into an Ollama that was already running stay loaded until you stop them (Ollama's own behavior). If Axis is killed without a chance to clean up, Windows ends those programs with it; on macOS and Linux the next Axis takes them back and shows them as on, so you can stop them.

## Commands

| Command | Description |
|---------|-------------|
| `axis` | Start Axis and open it in its own window |
| `axis setup` | Install llama.cpp and stable-diffusion.cpp now. `--build auto\|cpu\|vulkan\|cuda\|rocm\|metal`, `--only llama\|sd`, `--force` to replace a copy found elsewhere. |
| `axis shortcut` | Add Axis to the Start menu, Applications, or app menu. `--remove` to remove it, `--no-desktop` to skip the Windows desktop shortcut. |
| `axis update` | Install the latest version from GitHub. Agents, skills, and files are kept. `--check` only reports whether a newer version is available. |
| `axis quit` | Stop every running copy of Axis and every model: each model loaded in Ollama is unloaded (Ollama itself keeps running), and llama-server and sd-server are stopped. A copy that does not stop when asked is ended only if it was started by this version of Axis; for an older copy, close its window or terminal. |
| `axis uninstall` | Remove the program. Asks whether to delete your data as well. |
| `axis --version` | Print the installed version |

When the page opens, and every half hour after, Axis checks GitHub for a newer version. It compares both the version number and the installed files with the newest commit on `main`, so a change pushed without raising the version is found too (when GitHub's API limit of 60 requests an hour is reached, only the version is compared). The badge at the top of the sidebar shows **Up to date** (green), **Out of date**, or **Update check failed** with the reason and a **Check again** button; click **Out of date**, then **Update now**, to install the new version from the page. Restart Axis afterwards (Ctrl+C, then `axis`) and reload the page. The check needs no account and sends nothing about you. Set `AXIS_NO_UPDATE_CHECK=1` to turn it off.

The version number is kept in one place, `axis/VERSION`. To release a new version, change that file and push to `main`.

If `axis update` fails, reinstall manually:

```bash
pipx install --force https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip
```

`axis uninstall --yes` removes the program and keeps your data; add `--delete-data` to remove the data folder too. On Windows the removal completes a few seconds after the command returns; details are written to `axis-uninstall.log` in a new `axis-uninstall-…` folder inside your temp folder (the command prints the exact path). You can also remove Axis with `pipx uninstall axis-agent-manager`.

## Options

| Option | Effect |
|--------|--------|
| `--port 9000` | Use a different port (default 8765; the next free port is used if it is taken) |
| `--no-browser` | Do not open Axis; open the printed address yourself |
| `--tab` | Open Axis in a normal browser tab instead of its own window. Closing the tab does not stop Axis. |
| `--url http://127.0.0.1:9000` | Use a model server at a specific address |
| `--no-engine-install` | Do not install llama.cpp and stable-diffusion.cpp on the first start |
| `--version` | Print the version |

Any server with an OpenAI-compatible `/v1/models` and `/v1/chat/completions` API (for example LM Studio on port 1234) also works through `--url`.

## Environment variables

| Variable | Effect | Default |
|----------|--------|---------|
| `AXIS_URL` | Model server address | Tries `127.0.0.1:11434`, then `127.0.0.1:8080` |
| `AXIS_HOME` | Where agents, skills, chats, and files are saved | `Axis` in your home folder |
| `AXIS_PYTHON` | Python used by the Run Python skill | The Python Axis was installed with |
| `AXIS_NUM_CTX` | Context size requested from Ollama, in tokens (0 = Ollama default) | 8192 |
| `AXIS_HISTORY_CHARS` | Characters of earlier conversation sent with each message | 20000 |
| `AXIS_SEARCH_URL` | A SearXNG server for web search (JSON format must be enabled) | DuckDuckGo |
| `AXIS_SD_CLI` | Path to stable-diffusion.cpp's `sd-cli` | `sd-cli` on PATH |
| `AXIS_IMAGE_TIMEOUT` | Longest time one image may take, in seconds | 1800 |
| `AXIS_NO_UPDATE_CHECK` | Set to `1` to stop Axis checking GitHub for a newer version | Checks |
| `AXIS_LLAMA_SERVER` | Path to llama.cpp's `llama-server` | `llama-server` on PATH |
| `AXIS_SD_SERVER` | Path to stable-diffusion.cpp's `sd-server` | Next to `sd-cli`, then PATH |
| `AXIS_OLLAMA` | Path to the `ollama` program, used to start Ollama | `ollama` on PATH, then the usual install folders |
| `AXIS_START_TIMEOUT` | Longest time a model may take to load, in seconds | 900 |
| `HF_TOKEN` | Hugging Face access token, for gated or private models | The token saved by `hf auth login`, if any |
| `OLLAMA_HOST` | Where Ollama listens, as Ollama itself uses it | `127.0.0.1:11434` |
| `AXIS_NO_ENGINE_INSTALL` | `1` stops Axis installing llama.cpp and stable-diffusion.cpp on its first start | not set |
| `AXIS_BROWSER` | Chromium-based browser for the Axis window (a path or program name), or `tab` for a normal browser tab | Chrome, then Edge, Brave, Chromium |
| `AXIS_TESSERACT` | Path to the `tesseract` program | Found on PATH or where its installer puts it |
| `AXIS_OCR_PYTHON` | Python the OCR environment for PyTorch models is made from | The Python Axis was installed with |
| `AXIS_OCR_TIMEOUT` | Longest time reading one picture may take, in seconds | 900 |
| `GITHUB_TOKEN` | A GitHub token, used only if GitHub limits release lookups (Axis falls back to the release pages anyway) | not set |

Example: `AXIS_NUM_CTX=16384 axis` (macOS and Linux) or `$env:AXIS_NUM_CTX = "16384"; axis` (PowerShell).

The page only answers requests from this computer and only from its own page, so other websites open in your browser cannot use it.

## The workspace folder

Agents and the Workspace work in `Axis/shared` unless you choose another folder with **Folder** on the Workspace page. The choice is saved in `Axis/workspace-settings.json` as `{"folder": "<full path>"}`; an empty value, or a folder that no longer exists, means `Axis/shared`. Pictures Axis makes are always saved in `Axis/images`.
