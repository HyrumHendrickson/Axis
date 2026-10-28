<p align="center">
  <img src="axislogo.png" alt="Axis logo" width="160">
</p>

<h1 align="center">Axis</h1>

<p align="center">
  A lightweight local app for chatting with AI models and building your own agents.<br>
  No cloud, no accounts, no Python dependencies beyond the standard library.
</p>

---

## Overview

Axis runs a small web app on your computer and opens it in a window of its own. It talks to a model served locally by **Ollama** or **llama.cpp**.

- **Chat** directly with a model, or with agents you design.
- **Agents** have their own system prompt, standing instructions, skills, and private folder.
- **Skills** let agents run Python, read and write files, search the web, read Wikipedia, and generate images.
- **Past chats** are saved automatically and can be resumed at any time.
- **Images** are generated locally with Stable Diffusion (stable-diffusion.cpp).
- **Files** tab lets you browse, preview, upload to, and download from every agent's folder.
- **Models** tab installs, starts, stops, and deletes chat and image models, and shows memory use, context size, and capabilities for each.

Axis manages models itself. Type a model name such as `llama3.1:8b` or `z-image-turbo` on the **Models** page and click **Install**; every installed model then has **Start**, **Stop**, and **Delete** buttons. Axis installs llama.cpp and stable-diffusion.cpp for you the first time it starts, so nothing else is needed. Ollama is optional; Axis uses it when it is installed.

```
pipx install Axis  ->  run `axis`  ->  Models page: type a name, Install, then Start
```

## Quick start

Requirements: Python 3.9+ (3.10+ recommended), a terminal, and a browser. Full per-platform instructions follow.

```bash
# 1. Install pipx, then restart your terminal
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# 2. Install Axis
pipx install git+https://github.com/HyrumHendrickson/Axis.git

# 2. Install Axis (Backup)
# if step two fails, try this step
pipx install https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip

# 3. Run Axis
axis
```

On its first start Axis downloads llama.cpp and stable-diffusion.cpp in the background (progress is on the **Models** page). Go to **Models**, choose **Chat model, llama.cpp**, type `llama3.1:8b`, click **Install**, then **Start**. For pictures, choose **Image model**, type `z-image-turbo` (or `sd1.5` for a small one), click **Install**, then **Start**.

On Windows, replace `python3` with `py`. If `axis` is not found after step 2, see [Fixing PATH problems](#fixing-path-problems).

---

## Installation

Axis is installed with **pipx**, which places the `axis` command in a directory that `pipx ensurepath` adds to your PATH. Install steps differ only in how pipx itself is installed.

> **Important:** `pipx ensurepath` changes PATH for *new* terminals only. After running it, close your terminal and open a new one before using `pipx` or `axis`.

### Windows

Use **PowerShell** (Windows 10 or 11).

**1. Install Python.** Download the latest Python 3 from <https://www.python.org/downloads/>. On the first installer screen, check **Add python.exe to PATH**, then choose **Install Now**. Open a new PowerShell and confirm:

```powershell
py --version
```

**2. Install pipx.**

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Close PowerShell and open a new window.

**3. Install Axis.**

With Git ([download](https://git-scm.com/download/win)):

```powershell
pipx install git+https://github.com/HyrumHendrickson/Axis.git
```

Without Git:

```powershell
pipx install https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip
```

**4. Verify.**

```powershell
axis --version
```

### macOS

Use **Terminal**. macOS 12 or newer is recommended.

**1. Install Homebrew** (if needed) from <https://brew.sh>, then Python and pipx:

```bash
brew install python pipx
pipx ensurepath
```

Close Terminal and open a new window.

**2. Install Axis.**

```bash
pipx install git+https://github.com/HyrumHendrickson/Axis.git
```

Git is included with the Xcode command line tools. Without Git, use the ZIP form:

```bash
pipx install https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip
```

**3. Verify.**

```bash
axis --version
```

### Linux

**1. Install Python 3 and pipx.**

Ubuntu 23.04+ and Debian 12+:

```bash
sudo apt update
sudo apt install python3 pipx git
pipx ensurepath
```

Fedora:

```bash
sudo dnf install python3 pipx git
pipx ensurepath
```

Other distributions:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

Close your terminal and open a new one.

**2. Install Axis.**

```bash
pipx install git+https://github.com/HyrumHendrickson/Axis.git
```

**3. Verify.**

```bash
axis --version
```

### Fixing PATH problems

If `axis` or `pipx` is reported as "not found" or "not recognized":

1. **Open a new terminal.** PATH changes do not apply to terminals that were already open.
2. Run `pipx ensurepath` again (Windows: `py -m pipx ensurepath`), then open another new terminal.
3. Check where pipx put the command:
   ```bash
   pipx list
   ```
   It prints the app directory. This is normally `~/.local/bin` on macOS and Linux, and `%USERPROFILE%\.local\bin` on Windows.
4. Preview what `ensurepath` would change without modifying anything:
   ```bash
   pipx ensurepath --dry-run
   ```
5. As a last resort, run Axis by full path:
   - Windows: `& "$HOME\.local\bin\axis.exe"`
   - macOS and Linux: `~/.local/bin/axis`
6. On Windows, if `pipx` itself is not found right after installing it, call it through Python until you have reopened PowerShell: `py -m pipx install ...`

If a Linux or Homebrew Python refuses `pip install --user` with an "externally-managed-environment" error, install pipx with your system package manager (`apt`, `dnf`, or `brew`) as shown above.

---

## Model engines

Axis runs chat models with **llama.cpp** or **Ollama**, and image models with **stable-diffusion.cpp**. Axis starts and stops them as needed.

### llama.cpp and stable-diffusion.cpp: installed by Axis

pip and pipx cannot run programs while a package is installed, so Axis installs these two itself:

- **On its first start**, in the background, when they are not already on your computer. Turn this off with `axis --no-engine-install` or `AXIS_NO_ENGINE_INSTALL=1`.
- **From the Models page**, with **Install** (or **Update**) next to each engine.
- **Automatically**, as the first step of installing or starting a model that needs one.
- **From a terminal**, with `axis setup`, for example right after `pipx install`.

Axis downloads the latest release from each project's GitHub page, checks it against the published SHA-256 checksum, and keeps it in `Axis/engines` in your home folder. No administrator rights are needed and nothing is installed system-wide. It picks the build for your computer:

| Computer | llama.cpp | stable-diffusion.cpp |
|----------|-----------|----------------------|
| Windows, NVIDIA GPU | CUDA (with its runtime) | CUDA (with its runtime) |
| Windows, other GPU | Vulkan | Vulkan |
| Windows on ARM | CPU / Vulkan | not available |
| macOS, Apple Silicon | Metal | Metal |
| macOS, Intel | CPU | not available |
| Linux x86-64 | Vulkan when `vulkaninfo` works, otherwise CPU | Vulkan when `vulkaninfo` works, otherwise CPU |
| Linux ARM64 | CPU / Vulkan | not available |

If a build does not run (for example, a missing GPU driver), Axis tries the next one. To choose a build yourself, use the menu next to **Install** on the Models page, or `axis setup --build vulkan` (`cpu`, `vulkan`, `cuda`, `rocm`, `metal`). Where no ready-made build exists, build the program from source and set its path on the Models page.

A copy you installed yourself still works: a path set on the Models page or in `AXIS_LLAMA_SERVER`, `AXIS_SD_CLI`, or `AXIS_SD_SERVER` takes priority over the copy Axis installed. Copies on your PATH are used only when Axis has not installed its own.

### Ollama (optional)

| Platform | Install |
|----------|---------|
| Windows | `winget install Ollama.Ollama`, or download from <https://ollama.com/download> |
| macOS | Download the app from <https://ollama.com/download> and open it once. It runs in the menu bar. |
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |

Then install and start models on the **Models** page in Axis. If Ollama is installed but not running, Axis starts it (`ollama serve`) when you install or start a model, or when you click **Start Ollama**.

You can still use Ollama from a terminal; Axis sees models loaded either way:

```bash
ollama run llama3.1:8b --keepalive -1m
```

### llama.cpp details

Axis runs one `llama-server` per model that is on, each on its own port starting at 8080.

A `llama-server` you start yourself in a terminal still works too: Axis connects to it at `http://127.0.0.1:8080`.

### Daily use

| | Models page | Terminal equivalent |
|---|---|---|
| Install a model | **Install a model**, then **Install** | `ollama pull <model>` |
| Turn a model on | **Start** | `ollama run <model> --keepalive -1m` or `llama-server -m <file.gguf>` |
| See what is on | **On** label, and the status panel | `ollama ps` |
| Turn a model off | **Stop** | `ollama stop <model>` or Ctrl+C |
| Remove a model | **Delete** | `ollama rm <model>` |

Models started with **Start** stay loaded until you click **Stop**. You can have several on at once, from Ollama and llama.cpp together, memory permitting, and switch between them with the **Model** menu in any chat.

---

## Running Axis

In a separate terminal:

```bash
axis
```

Axis opens at `http://127.0.0.1:8765` in its own window, with no tabs or address bar, and its own entry in the taskbar or Dock. The status panel shows **Ollama is ready** (or **llama.cpp / OpenAI-compatible is ready**) once a model is on. Running `axis` while it is already running opens another window on the same Axis.

### The Axis window

The window is an "app mode" window of a Chromium-based browser: Chrome, Edge, Brave, or Chromium, in that order. Edge comes with Windows 10 and 11, so every Windows computer has one. On a Mac with only Safari, or a Linux system with only Firefox, Axis opens in an ordinary browser tab instead, as it did before 4.0.

**Closing the Axis window stops Axis.** It first stops every `llama-server`, `sd-server` and `ollama serve` it started, then exits. If a reply, a picture or an install is still running, the window asks before closing. Reloading the page does not stop Axis; it waits a few seconds for the page to come back. With several Axis windows open, Axis stops when the last one closes. If the browser crashes without closing the page, Axis stops after 10 minutes without hearing from it.

In a browser tab (`axis --tab`, `AXIS_BROWSER=tab`, or when no Chromium-based browser is found), closing the tab does **not** stop Axis; press Ctrl+C in its terminal or use **Quit Axis**. To use a different Chromium-based browser for the window, such as Vivaldi or Opera, set `AXIS_BROWSER` to its program.

### The Axis app

The first time you run `axis` (or `axis setup`), Axis adds itself to your apps, so you can open it without a terminal:

| System | Where it appears |
|--------|------------------|
| Windows | Start menu (search "Axis") and a desktop shortcut |
| macOS | `~/Applications/Axis.app`, so it shows in Launchpad and Spotlight; drag it to the Dock to keep it there |
| Linux | Your desktop's app menu (`~/.local/share/applications/axis.desktop`) |

Opening the app starts Axis in the background and opens the Axis window; no terminal window is shown. Close the window to stop Axis, or use **Quit Axis** at the bottom of the sidebar (needed when Axis fell back to a browser tab). Its output goes to `axis.log` in your data folder. The `axis` command still works, and both open the same Axis.

Run `axis shortcut` to make the app again (for example after deleting it), `axis shortcut --remove` to remove it (Axis will not add it back), and `axis shortcut --no-desktop` on Windows to skip the desktop shortcut. `axis uninstall` removes it. Set `AXIS_NO_SHORTCUT=1` to stop Axis from adding it on the first run.

To get the app straight after installing, without starting Axis first:

```bash
pipx install git+https://github.com/HyrumHendrickson/Axis.git && axis shortcut
```

When Axis stops, it also stops every `llama-server` and `sd-server` it started, and `ollama serve` if Axis started it. Models loaded into an Ollama that was already running stay loaded until you stop them (Ollama's own behavior). If Axis is killed without a chance to clean up, Windows ends those programs with it; on macOS and Linux the next Axis takes them back and shows them as on, so you can stop them.

### Commands

| Command | Description |
|---------|-------------|
| `axis` | Start Axis and open it in its own window |
| `axis setup` | Install llama.cpp and stable-diffusion.cpp now. `--build cpu\|vulkan\|cuda\|rocm\|metal`, `--only llama\|sd`, `--force` to replace a copy found elsewhere. |
| `axis shortcut` | Add Axis to the Start menu, Applications, or app menu. `--remove` to remove it, `--no-desktop` to skip the Windows desktop shortcut. |
| `axis update` | Install the latest version from GitHub. Agents, skills, and files are kept. |
| `axis uninstall` | Remove the program. Asks whether to delete your data as well. |
| `axis --version` | Print the installed version |

When the page opens, Axis checks GitHub quietly for a newer version. The badge at the top of the sidebar shows **Up to date** (green) or **Out of date**; click **Out of date**, then **Update now**, to install the new version from the page. Restart Axis afterwards (Ctrl+C, then `axis`) and reload the page. The check needs no account, sends nothing about you, and shows nothing when you are offline. Set `AXIS_NO_UPDATE_CHECK=1` to turn it off.

The version number is kept in one place, `axis/VERSION`. To release a new version, change that file and push to `main`.

If `axis update` fails, reinstall manually:

```bash
pipx install --force https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip
```

`axis uninstall --yes` removes the program and keeps your data; add `--delete-data` to remove the data folder too. On Windows the removal completes a few seconds after the command returns; details are written to `axis-uninstall.log` in your temp folder. You can also remove Axis with `pipx uninstall axis-agent-manager`.

### Options

| Option | Effect |
|--------|--------|
| `--port 9000` | Use a different port (default 8765; the next free port is used if it is taken) |
| `--no-browser` | Do not open Axis; open the printed address yourself |
| `--tab` | Open Axis in a normal browser tab instead of its own window. Closing the tab does not stop Axis. |
| `--url http://127.0.0.1:9000` | Use a model server at a specific address |
| `--no-engine-install` | Do not install llama.cpp and stable-diffusion.cpp on the first start |
| `--version` | Print the version |

Any server with an OpenAI-compatible `/v1/models` and `/v1/chat/completions` API (for example LM Studio on port 1234) also works through `--url`.

### Environment variables

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
| `GITHUB_TOKEN` | A GitHub token, used only if GitHub limits release lookups (Axis falls back to the release pages anyway) | not set |

Example: `AXIS_NUM_CTX=16384 axis` (macOS and Linux) or `$env:AXIS_NUM_CTX = "16384"; axis` (PowerShell).

The page only answers requests from this computer and only from its own page, so other websites open in your browser cannot use it.

---

## Using Axis

### Chat

- **Direct chat** talks to the model with no agent instructions. Click an agent in the sidebar to chat with that agent.
- **Enter** sends; **Shift+Enter** adds a new line. **Send** becomes **Stop** while a reply is being written (or press **Esc**).
- You can switch pages or tabs while a reply is generating. The sidebar marks chats that are still working, and several chats can work at once.
- Every conversation is saved as you go. **Export** downloads the open conversation as Markdown.
- Under each reply: **Copy**, **Retry**, and a line showing the model, token count, speed, and duration. A meter under the message box shows context usage when the server reports it.

### Agents

Click **New agent** in the sidebar.

| Field | Purpose |
|-------|---------|
| Name | Letters, numbers, `-` and `_`. Renaming moves the agent's history and folder with it. |
| Preferred model | Model used when it is on. Leave empty to use whichever model is on. |
| System prompt | Who the agent is and how it behaves. Sent at the start of every conversation. |
| Config | Instructions appended to **every message** you send, for rules the agent must not forget. |
| Skills | Which skills the agent may use. |
| Folder | Where the agent's files are kept. **Open folder** shows it in Explorer or Finder. |

Use **Duplicate** to copy an agent's settings. **Delete agent** removes the agent, its folder, every file in it, and all of its chats.

<details>
<summary>What the model receives</summary>

For an agent with the system prompt "You are a copy editor.", one custom skill, and two config instructions, sending "their going home" produces:

```
system:  You are a copy editor.

         You have the following skills. Use them when relevant.

         ## Grammar
         Fix grammar mistakes.

user:    their going home

         ---
         Follow these instructions:
         - Answer in one sentence.
         - Use US spelling.
```

The config block is attached to your newest message each time and is not shown in the chat. Agents with action skills also receive a note that their files live in their own folder, plus the action definitions.

</details>

### Skills

Open **Skills** in the sidebar. Every agent chooses from the same library.

| Built-in skill | Capability |
|----------------|------------|
| Run Python | Write and run Python (60 seconds per run) |
| List files | List files and folders in the agent's folder |
| Read files | Read text files |
| Make files | Create or overwrite text files and make folders |
| Delete files | Delete files and empty folders |
| Web search | `web_search` and `fetch_page` |
| Wikipedia | `wikipedia_search` and `wikipedia_article` |
| Image generation | `generate_image` with a local Stable Diffusion model |
| Shared folder | Lets file actions reach `shared/`, a folder common to all agents with this skill |

Notes:

- Actions require a model that can call tools. See [Recommended models](#recommended-models).
- File actions are confined to the agent's own folder (plus `shared/` with Shared folder). Paths using `..`, absolute paths, and links pointing outside are refused.
- Run Python starts in the agent's folder and blocks writing, renaming, or deleting elsewhere, and launching other programs. It uses the Python Axis was installed with. To make a package such as pandas available to agents, install it into that environment: `pipx inject axis-agent-manager pandas`.
- This guard is not a security sandbox against code deliberately written to escape it. Review what an agent does when you give it Run Python.
- Web search uses DuckDuckGo by default. If it rate-limits you, set `AXIS_SEARCH_URL` to a [SearXNG](https://docs.searxng.org/) server. `fetch_page` refuses links to your own machine and local network.
- Agents choose how much text to retrieve (`max_results`, `max_chars`). Long pages end with a `start=` marker so the agent can continue reading. Wikipedia actions accept `lang` (for example `la` for Latin).
- Agents can leave each other files in `shared/` (for example `shared/inbox/to-writer.md`). In Run Python the path is in `os.environ["AXIS_SHARED_DIR"]`.

**Custom skills:** click **New skill**, then enter a name, a one-line description, and instructions (a house style, a checklist, a way of answering). Built-in skills cannot be edited or deleted.

### Recommended models

| Model (Ollama name) | Download | Notes |
|---------------------|----------|-------|
| `llama3.1:8b` | about 4.9 GB | **Recommended.** Reliable for multi-step actions. Needs about 8 GB free memory. |
| `llama3.2:3b` | about 2 GB | Good chat; simple actions only (save a file, short calculation). |
| `llama3.2:1b` | about 1.3 GB | Basic chat. Not recommended for actions. |
| `llama3`, `llama2` | | No built-in tool support. Axis falls back to a text-based tool format, which is less reliable. |

Axis is tuned for Llama 3 behavior:

- **Larger context.** Ollama defaults to 4,096 tokens and silently truncates beyond that. Axis requests 8,192 (change with `AXIS_NUM_CTX`). For llama.cpp, start the server with `-c 8192`. Long chats are trimmed automatically so the newest messages and agent instructions always fit.
- **Steadier actions.** Agent turns that can use actions run at temperature 0.2.
- **Text tool-call recovery.** When a model writes a tool call as plain text instead of making a real call, Axis detects it, runs the action, and replaces the raw text.
- **Forgiving names.** Common variants (`python`, `ls`, `mkdir`, `create_file`, `filename`, `text`, `script`) are mapped to the correct action and argument. Python sent with literal `\n` is repaired before running.
- **Loop protection.** Repeated identical actions are not re-run; after a few repeats or 10 actions in one reply, the agent is asked for its final answer.
- **Action memory.** Earlier replies are sent back with a short note of the actions taken, so follow-ups like "now add a line to that file" work.

If `llama-server` refuses tools (older builds started without `--jinja`), Axis uses the text-based format automatically.

### Past chats

Every conversation is saved automatically. Open **Past chats** in the sidebar to filter by agent, sort (newest, oldest, by agent, by title), search by title, rename, resume, or delete. Axis never deletes chats on its own, except that deleting an agent deletes its chats. Chats are stored as `.json` files in `Axis/chats` in your home folder.

### Images

Axis generates pictures with [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp), which it installs itself (see [Model engines](#model-engines)).

**Install by name.** On the **Models** page, choose **Image model**, type a name, and click **Install**. Axis downloads every file the model needs from Hugging Face and sets it up with settings that suit it.

| Name | Model | Download | Notes |
|------|-------|----------|-------|
| `sd1.5` | Stable Diffusion 1.5 | about 2.1 GB | 512 x 512. Small and quick; runs on almost any computer. |
| `sdxl` | Stable Diffusion XL 1.0 (with the fp16-fixed VAE) | about 7.3 GB | 1024 x 1024. Needs about 8 GB of memory. |
| `sdxl-turbo` | SDXL Turbo | about 7.3 GB | 512 x 512 in 1 to 4 steps. |
| `z-image-turbo` | Z-Image Turbo (diffusion model, Qwen3-4B text encoder, VAE) | about 6.5 GB | High quality in 8 steps; can draw text. `:q3` is smaller, `:q8` larger and sharper. |
| `flux1-schnell` | FLUX.1 schnell (diffusion model, CLIP-L, T5-XXL, VAE) | about 12 GB | 1024 x 1024 in 4 steps. `:q8` is larger. |

Names are forgiving (`sd15`, `zimage`, `flux`). Installing a name again finds the model already installed; `z-image-turbo:q8` adds a second model that shares the text encoder and VAE with the first.

**Install one file from Hugging Face.** For any other model, click **Install one file from Hugging Face instead**, choose what the file is, type its Hugging Face name, and click **Install**. When a repository has several model files, Axis lists them and you choose one.

| What you install | What it is | Example |
|------------------|------------|---------|
| A complete model | Single-file SD 1.5, SD 2, or SDXL checkpoint | `stable-diffusion-v1-5/stable-diffusion-v1-5`, then choose `v1-5-pruned-emaonly.safetensors` |
| A diffusion model | The main file of Flux, Z-Image, SD3, or Qwen-Image | `leejet/Z-Image-Turbo-GGUF`, then choose a quantization |
| A VAE / text encoder | A part added to a model you installed | `Comfy-Org/z_image_turbo/split_files/vae/ae.safetensors`, added to the Z-Image model |

Models with separate parts are installed one file at a time: first the diffusion model (which creates the image model), then each part with **Add to** that model. For Z-Image Turbo: the diffusion model from `leejet/Z-Image-Turbo-GGUF`, the text encoder (LLM) from `unsloth/Qwen3-4B-Instruct-2507-GGUF`, and the VAE above. Axis guesses default size, steps, and CFG scale from the file name; check them with **Edit**.

Gated models (some Stability AI and Black Forest Labs repositories) need you to accept their terms on the model's page, create a token at <https://huggingface.co/settings/tokens>, set `HF_TOKEN` to it, and restart Axis.

**Start and Stop.** **Start** loads an image model into `sd-server` and keeps it in memory, so each picture begins immediately. **Stop** frees the memory. Only one image model is on at a time; starting another stops the first. When no image model is on, each picture runs `sd-cli`, which loads the model, makes the picture, and exits. If only `sd-server` is available, generating a picture starts the model automatically.

**Delete** removes the image model and the files Axis downloaded for it. A file another image model also uses is kept. Files you added yourself are kept unless you tick **Also delete the files you added yourself**.

**Files you already have.** Click **Add files I already have** and enter the files and settings yourself. First confirm the files work from a terminal:
   ```powershell
   cd C:\sd
   .\bin\sd-cli.exe --diffusion-model models\z_image_turbo-Q4_0.gguf --llm models\Qwen3-4B-Instruct-2507-Q8_0.gguf `
     --vae models\ae.safetensors --cfg-scale 1.0 --steps 4 -W 384 -H 384 --flash-attn off,diffusion=on `
     -p "a lighthouse on a cliff at dusk" -o out_fast.png
   ```
Then click **Add files I already have** and enter the same files and settings:

   | Field | Value |
   |-------|-------|
   | Name | Z-Image Turbo |
   | Diffusion model | `models\z_image_turbo-Q4_0.gguf` |
   | Text encoder (LLM) | `models\Qwen3-4B-Instruct-2507-Q8_0.gguf` |
   | VAE | `models\ae.safetensors` |
   | Default width / height | 384 / 384 |
   | Default steps / CFG scale | 4 / 1.0 |
   | Extra arguments | `--flash-attn off,diffusion=on` |

   Relative paths start at the **working folder**: the folder above `bin` when `sd-cli` is in a `bin` folder, otherwise `sd-cli`'s own folder. You can set it explicitly in the sd-cli settings. A single-file SD 1.5 or SDXL model goes in **Checkpoint**; Flux and SD3 use **CLIP-L** and **T5-XXL**. Anything else `sd-cli` accepts goes in **Extra arguments**.

**Images tab.** Choose a model (its defaults fill in), write a prompt and optional negative prompt, set resolution, steps, CFG scale, sampler, and seed (-1 picks a random seed; the used seed is always shown), then **Generate** (or Ctrl+Enter). Pictures are saved in `Axis/shared/images`. The gallery supports **Reuse settings**, **Reuse with seed**, **Download**, **Open in Files**, and **Delete**. One picture is made at a time.

**Agents.** Give an agent the **Image generation** skill. It calls `generate_image` with a prompt (and optionally size, steps, seed, or model). The picture is saved in `images/` in the agent's folder and shown in the chat.

### Files

Open **Files** in the sidebar to browse what agents have made.

- Select an agent, or **Shared folder**, then navigate folders on the left and open files on the right.
- Text and code display as-is; Markdown renders formatted (with **Show source**); CSV displays as a table; images and PDFs display inline; `.html` files display as source with **Preview page** for a rendered view. Previews run in a sealed frame that cannot reach Axis or your other data. Axis never executes files you click.
- **Upload** (or drag and drop) copies files into the current folder, up to 512 MB each. Existing files are never replaced; duplicates are saved as `data (2).csv`.
- **Download**, **Show in folder**, **Copy text**, **Delete**, **New folder**, and **Delete folder** are available. The list refreshes automatically.

Data layout:

```
Axis/                       in your home folder (override with AXIS_HOME)
├── agents/
│   └── <name>/
│       ├── agent.json      the agent's settings
│       └── workspace/      the agent's folder: every file it makes goes here
├── chats/                  saved conversations, one .json file each
├── engine-settings.json    llama-server path, context size, extra arguments
├── engines/                llama.cpp and stable-diffusion.cpp, as Axis installed them
├── image-models/           image model definitions, one .json file each
├── image-settings.json     sd-cli and sd-server paths, working folder, default image model
├── models/
│   ├── llm/                GGUF chat models for llama.cpp, one folder per Hugging Face repository
│   └── image/              Stable Diffusion files Axis downloaded
├── run/                    programs Axis started (to recover them after a crash)
├── shared/                 folder shared by agents with the Shared folder skill
│   └── images/             pictures made in the Images tab
└── skills/                 your custom skills, one .json file each
```

### Models tab

Open **Models** to install, start, stop, and delete models.

**Install a model.** Choose what to install, type a name, and click **Install** (or press Enter). Progress, speed, and time left appear under **Activity**; you can switch pages, and a download can be cancelled.

| Choice | Type | Examples |
|--------|------|----------|
| Chat model, Ollama | A name from [ollama.com/library](https://ollama.com/library), with the size after the colon. Hugging Face GGUF models work as `hf.co/owner/name:quant`. Pasted `ollama run` commands and ollama.com links work too. | `llama3.1:8b`, `qwen3:4b`, `hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M` |
| Chat model, llama.cpp | A short name (`llama3.2:1b`, `llama3.2:3b`, `llama3.1:8b`, `qwen3:4b`, `qwen3:8b`), or a GGUF model on [Hugging Face](https://huggingface.co/models?library=gguf), optionally with a quantization after a colon (the same form as `llama-server -hf`), a file path, or a link copied from the site. Without a quantization Axis picks Q4_K_M, or lists the files so you can choose. Split GGUF files are downloaded in full. | `bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M` |
| Image model | A name such as `z-image-turbo`, or one file from Hugging Face. See [Images](#images). | `z-image-turbo`, `sd1.5` |

Hugging Face downloads are checked against the size and SHA-256 checksum Hugging Face publishes before they are used. An interrupted download resumes where it stopped the next time you install the same model; a cancelled one is discarded. Axis checks free disk space first.

**Each installed model** shows **Start** or **Stop**, **Delete** (which asks first), and **Chat with this model** while it is on. **Start** loads the model and keeps it loaded until **Stop**. For Ollama, Axis loads it at the context size it uses (`AXIS_NUM_CTX`), so the first chat does not reload it. For llama.cpp, Axis starts `llama-server` with the context size and extra arguments under **Engine settings** (for example `-ngl 99` to put every layer on the GPU). If a model fails to start, the card shows the program's last messages, including out-of-memory errors.

**Details.** Loaded models show memory in use (and GPU share), context window, parameters, quantization, size on disk, architecture, keep-alive state, and capabilities (Chat, Tools, Vision, Thinking, Embeddings). Ollama models that are not loaded show an estimated memory figure. llama.cpp does not report the context cache, so its real memory use is somewhat higher.

**GGUF files you already have** can be copied into `Axis/models/llm` (any subfolder); they appear as llama.cpp models.

A `llama-server` or other OpenAI-compatible server you started yourself is listed with **Chat with this model**, but Axis cannot stop it.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `axis` or `pipx` not found | See [Fixing PATH problems](#fixing-path-problems). Open a new terminal first. |
| `py` not recognized (Windows) | Reinstall Python with **Add python.exe to PATH** checked, or use `python` instead of `py`. |
| `ollama` not recognized | Open a new terminal after installing. On Windows, confirm Ollama appears in the system tray. |
| Status: **No model server** | Open **Models**: click **Start Ollama**, or **Start** a llama.cpp model. For a server on a non-default port, start Axis with `--url`. |
| Status: **No model is on** | Open **Models** and click **Start** on a model. |
| Install: "No model named ... was found" (Ollama) | Copy the exact name from ollama.com/library, including the part after the colon. |
| Install: "not found, or it is private or gated" (Hugging Face) | Check the spelling (`owner/name`). For a gated model, accept its terms on its page, set `HF_TOKEN`, and restart Axis. |
| Install: "no GGUF files" | llama.cpp needs GGUF. Search Hugging Face for the model's GGUF version (often `...-GGUF`), or install it with Ollama. |
| Install: "Not enough disk space" | Free space on the drive holding `Axis/models`, or set `AXIS_HOME` to another drive. |
| Start (llama.cpp): "did not fit in memory" | Choose a smaller model or quantization, lower **Context size** under Engine settings, or close other programs. |
| Start (llama.cpp): "llama-server was not found" | Click **Install llama.cpp** on the Models page, or set its path under **Engine settings**. |
| Installing llama.cpp or stable-diffusion.cpp fails: "does not run on this computer" | Choose another build in the menu next to **Install** (CPU always works), or update your GPU driver. |
| Installing an engine fails on Windows: "Visual C++ runtime" | Install <https://aka.ms/vs/17/release/vc_redist.x64.exe> and click **Install** again. |
| Install by name: "no longer has ..." | The file was renamed on Hugging Face. Install the parts with **Install one file from Hugging Face instead**, or update Axis. |
| Start (image): "sd-server was not found" | Update stable-diffusion.cpp (recent releases include `sd-server`), or set the sd-server path under Image models. |
| Start (image): extra arguments not accepted | Edit the model and remove options only `sd-cli` understands. |
| Model shows "Started by an earlier Axis session" | Axis was closed without stopping it. Click **Stop** if you do not need it. |
| Page says **Axis has stopped** | The terminal running `axis` was closed. Run `axis` again and reload the page. |
| Browser did not open | Open the address printed in the terminal (normally `http://127.0.0.1:8765`). |
| Axis opens in a tab, not its own window | No Chrome, Edge, Brave or Chromium was found. Install one, or set `AXIS_BROWSER` to a Chromium-based browser. |
| Axis stopped when I closed its window | That is intended in 4.0. To keep Axis running without a window, start it with `axis --tab` and close the tab instead. |
| "This agent prefers X, which is not on" | Turn that model on, or clear the agent's preferred model in **Edit agent**. |
| Reply stops with an error | Your message is restored in the box. Check the status panel and send again. |
| Replies are very slow | Use a smaller model or close other programs to free memory. |
| First reply after loading a model is slow | Normal. Ollama reloads the model with the larger context Axis requests. |
| Agent forgets instructions mid-task | The conversation outgrew the model's context. Start a **New chat**, or raise `AXIS_NUM_CTX` (Ollama) or `-c` (llama.cpp). |
| Agent describes an action but nothing happens | Confirm the skill is ticked in the agent's settings and use a tool-capable model such as `llama3.1:8b`. Direct requests help: "Save this as notes.md". |
| `Unknown tool '...'` in a step | The agent tried an action whose skill is not enabled. Tick it in **Edit agent**, or ignore it. |
| Run Python reports a missing package | `pipx inject axis-agent-manager <package>` |
| `axis update` fails | Check your connection, or reinstall with the `pipx install --force` command in [Commands](#commands). |
| Images: **Set up Stable Diffusion** | Install an image model by name on the Models page; Axis installs stable-diffusion.cpp with it. |
| Image model shows **Files missing** | A path is wrong or relative paths start in an unexpected folder. Use full paths, or set the **Working folder**. |
| Picture fails with `sd-cli failed` | Run the same command in a terminal. Out-of-memory errors usually need a smaller size, a smaller quantization, or `--offload-to-cpu` in Extra arguments. |
| Chats disappeared | Use the same `AXIS_HOME`. Chats written by very old versions were stored per browser and port; use the same browser and default port once to migrate them. |

---

## Development

Install from a local clone in editable mode:

```bash
git clone https://github.com/HyrumHendrickson/Axis.git
cd Axis
pipx install --editable .
```

Run the tests. They use fake Ollama, llama.cpp, stable-diffusion.cpp, Hugging Face, and GitHub servers, so no real model or network is needed:

```bash
python3 -m unittest discover -s tests -v      # Windows: py -m unittest discover -s tests -v
```

Project layout:

```
axis/
  cli.py          the `axis`, `axis update`, `axis shortcut`, and `axis uninstall` commands
  shortcut.py     the Start menu / Applications / app menu launcher
  icons/          the app icon for Windows (.ico), macOS (.icns), and Linux (.png)
  server.py       local web server and JSON API
  updates.py      checks GitHub for a newer version and installs it from the page
  VERSION         the version number, and nothing else
  agents.py       agent storage, folders, and message assembly
  chats.py        saved conversations
  images.py       image models, sd-cli and sd-server runners, and gallery
  manager.py      installs, starts, stops, and deletes models (Ollama, llama.cpp, sd-server)
  engines.py      installs and updates llama.cpp and stable-diffusion.cpp from their GitHub releases
  catalog.py      model names (z-image-turbo, sd1.5, llama3.1:8b, ...) and the files each one needs
  hub.py          Hugging Face names, file choice, and verified, resumable downloads
  procs.py        the programs Axis starts: logs, readiness, stopping, crash recovery
  jobs.py         background installs, starts, stops, and deletes, with progress
  skills.py       skill library (built-in actions and custom skills)
  tools.py        actions, confined to each agent's folder
  runner.py       lets an agent use actions while replying
  backend.py      finds Ollama and llama.cpp servers, reads model details, streams replies
  paths.py        where Axis keeps its data
  web/index.html  the page (HTML, CSS, and JavaScript in one file)
tests/
  test_axis.py    tests using fake model servers
  test_manage.py  tests for installing, starting, stopping, and deleting models
  test_engines.py tests for installing engines (fake GitHub) and image models by name
```

## License

MIT. See [LICENSE](LICENSE).
