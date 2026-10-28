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

Axis runs a small web app on your computer and opens it in your browser. It talks to a model served locally by **Ollama** or **llama.cpp**.

- **Chat** directly with a model, or with agents you design.
- **Agents** have their own system prompt, standing instructions, skills, and private folder.
- **Skills** let agents run Python, read and write files, search the web, read Wikipedia, and generate images.
- **Past chats** are saved automatically and can be resumed at any time.
- **Images** are generated locally with Stable Diffusion (stable-diffusion.cpp).
- **Files** tab lets you browse, preview, upload to, and download from every agent's folder.
- **Models** tab shows memory use, context size, and capabilities for each model.

Axis does not download, start, or stop models. You start a model with Ollama or llama.cpp; Axis connects to it.

```
start a model  ->  run `axis`  ->  browser opens at http://127.0.0.1:8765
(Ollama or llama.cpp)
```

## Quick start

Requirements: Python 3.9+ (3.10+ recommended), a terminal, and a browser. Full per-platform instructions follow.

```bash
# 1. Install pipx, then restart your terminal
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# 2. Install Axis
pipx install git+https://github.com/HyrumHendrickson/Axis.git

# 3. Start a model (requires Ollama)
ollama run llama3.1:8b --keepalive -1m     # type /bye after it loads

# 4. Run Axis
axis
```

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

## Install a model server

Axis needs one of the following running. Ollama is simpler.

### Option 1: Ollama (recommended)

| Platform | Install |
|----------|---------|
| Windows | `winget install Ollama.Ollama`, or download from <https://ollama.com/download> |
| macOS | Download the app from <https://ollama.com/download> and open it once. It runs in the menu bar. |
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |

Open a **new** terminal, then download and load a model:

```bash
ollama run llama3.1:8b --keepalive -1m
```

The first run downloads the model (about 4.9 GB). Type `/bye` to leave Ollama's prompt; the model stays loaded because of `--keepalive -1m`. Any model from <https://ollama.com/library> works the same way. For machines with limited memory, use `llama3.2:3b` (about 2 GB).

### Option 2: llama.cpp

| Platform | Install |
|----------|---------|
| Windows | `winget install llama.cpp` |
| macOS | `brew install llama.cpp` |
| Linux | `brew install llama.cpp`, or a release from <https://github.com/ggml-org/llama.cpp/releases> |

Start the server (the first run downloads the model from Hugging Face):

```bash
llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M -c 8192
```

Leave that terminal open; the model is on while it runs. `-c 8192` gives agents enough context. To use a model file you already have: `llama-server -m /path/to/model.gguf -c 8192`.

### Daily use

| | Ollama | llama.cpp |
|---|---|---|
| Server running | Ollama app open, or `ollama serve` | `llama-server` terminal open |
| Turn a model on | `ollama run <model> --keepalive -1m`, then `/bye` | `llama-server -hf <repo>:<quant>` or `-m <file.gguf>` |
| See what is on | `ollama ps`, or the Axis status panel | The Axis status panel |
| Turn a model off | `ollama stop <model>` | Ctrl+C in the `llama-server` terminal |
| Default address | `http://127.0.0.1:11434` | `http://127.0.0.1:8080` |

Without `--keepalive -1m`, Ollama unloads a model after five idle minutes. You can load several models at once, memory permitting, and switch between them with the **Model** menu in any chat.

---

## Running Axis

In a separate terminal:

```bash
axis
```

Your browser opens at `http://127.0.0.1:8765`. The status panel shows **Ollama is ready** (or **llama.cpp / OpenAI-compatible is ready**) once a model is on. Closing the browser tab does not stop Axis. Press Ctrl+C in its terminal to stop it. Running `axis` while it is already running reopens the page.

### Commands

| Command | Description |
|---------|-------------|
| `axis` | Start Axis and open it in your browser |
| `axis update` | Install the latest version from GitHub. Agents, skills, and files are kept. |
| `axis uninstall` | Remove the program. Asks whether to delete your data as well. |
| `axis --version` | Print the installed version |

If `axis update` fails, reinstall manually:

```bash
pipx install --force https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip
```

`axis uninstall --yes` removes the program and keeps your data; add `--delete-data` to remove the data folder too. On Windows the removal completes a few seconds after the command returns; details are written to `axis-uninstall.log` in your temp folder. You can also remove Axis with `pipx uninstall axis-agent-manager`.

### Options

| Option | Effect |
|--------|--------|
| `--port 9000` | Use a different port (default 8765; the next free port is used if it is taken) |
| `--no-browser` | Do not open a browser; open the printed address yourself |
| `--url http://127.0.0.1:9000` | Use a model server at a specific address |
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

Axis generates pictures with [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)'s `sd-cli`. As with chat models, you download the program and model files yourself.

1. Download a release from <https://github.com/leejet/stable-diffusion.cpp/releases> and extract it (for example to `C:\sd`). Put model files in a `models` folder next to its `bin` folder.
2. Confirm it works from a terminal:
   ```powershell
   cd C:\sd
   .\bin\sd-cli.exe --diffusion-model models\z_image_turbo-Q4_0.gguf --llm models\Qwen3-4B-Instruct-2507-Q8_0.gguf `
     --vae models\ae.safetensors --cfg-scale 1.0 --steps 4 -W 384 -H 384 --flash-attn off,diffusion=on `
     -p "a lighthouse on a cliff at dusk" -o out_fast.png
   ```
3. In Axis, open **Models**. Under **Image models**, set the **sd-cli path** (leave empty if `sd-cli` is on PATH) and click **Save**.
4. Click **Add image model** and enter the same files and settings:

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

**Images tab.** Choose a model (its defaults fill in), write a prompt and optional negative prompt, set resolution, steps, CFG scale, sampler, and seed (-1 picks a random seed; the used seed is always shown), then **Generate** (or Ctrl+Enter). Pictures are saved in `Axis/shared/images`. The gallery supports **Reuse settings**, **Reuse with seed**, **Download**, **Open in Files**, and **Delete**. `sd-cli` starts fresh for each picture and exits afterward, so it uses no memory between pictures. One picture is made at a time.

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
├── image-models/           image model definitions, one .json file each
├── image-settings.json     sd-cli path, working folder, default image model
├── shared/                 folder shared by agents with the Shared folder skill
│   └── images/             pictures made in the Images tab
└── skills/                 your custom skills, one .json file each
```

### Models tab

Open **Models** to see what your server reports. Nothing here changes a model.

- **Ollama:** every downloaded model is listed. Loaded models show memory in use (and GPU share), context window, parameters, quantization, size on disk, architecture, keep-alive state, and capabilities (Chat, Tools, Vision, Thinking, Embeddings). Models that are not loaded show an estimated memory figure. Each card shows the command to turn the model on or off.
- **llama.cpp:** the loaded model shows its context window per slot, trained context, parameter count, weight memory, parallel slots, and model file. llama.cpp does not report the context cache, so real memory use is somewhat higher.
- **Image models** are listed below chat models, with the sd-cli settings.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `axis` or `pipx` not found | See [Fixing PATH problems](#fixing-path-problems). Open a new terminal first. |
| `py` not recognized (Windows) | Reinstall Python with **Add python.exe to PATH** checked, or use `python` instead of `py`. |
| `ollama` not recognized | Open a new terminal after installing. On Windows, confirm Ollama appears in the system tray. |
| Status: **No model server** | Start Ollama (open the app, or `ollama serve`) or `llama-server`. For a non-default port, start Axis with `--url`. |
| Status: **No model is on** | Turn one on: `ollama run llama3.1:8b --keepalive -1m` |
| Page says **Axis has stopped** | The terminal running `axis` was closed. Run `axis` again and reload the page. |
| Browser did not open | Open the address printed in the terminal (normally `http://127.0.0.1:8765`). |
| "This agent prefers X, which is not on" | Turn that model on, or clear the agent's preferred model in **Edit agent**. |
| Reply stops with an error | Your message is restored in the box. Check the status panel and send again. |
| Replies are very slow | Use a smaller model or close other programs to free memory. |
| First reply after loading a model is slow | Normal. Ollama reloads the model with the larger context Axis requests. |
| Agent forgets instructions mid-task | The conversation outgrew the model's context. Start a **New chat**, or raise `AXIS_NUM_CTX` (Ollama) or `-c` (llama.cpp). |
| Agent describes an action but nothing happens | Confirm the skill is ticked in the agent's settings and use a tool-capable model such as `llama3.1:8b`. Direct requests help: "Save this as notes.md". |
| `Unknown tool '...'` in a step | The agent tried an action whose skill is not enabled. Tick it in **Edit agent**, or ignore it. |
| Run Python reports a missing package | `pipx inject axis-agent-manager <package>` |
| `axis update` fails | Check your connection, or reinstall with the `pipx install --force` command in [Commands](#commands). |
| Images: **Set up Stable Diffusion** | Set the sd-cli path and add an image model on the Models page. |
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

Run the tests. They use fake Ollama and llama.cpp servers, so no real model is needed:

```bash
python3 -m unittest discover -s tests -v      # Windows: py -m unittest discover -s tests -v
```

Project layout:

```
axis/
  cli.py          the `axis`, `axis update`, and `axis uninstall` commands
  server.py       local web server and JSON API
  agents.py       agent storage, folders, and message assembly
  chats.py        saved conversations
  images.py       image models, sd-cli runner, and gallery
  skills.py       skill library (built-in actions and custom skills)
  tools.py        actions, confined to each agent's folder
  runner.py       lets an agent use actions while replying
  backend.py      detects Ollama or llama.cpp, reads model details, streams replies
  paths.py        where Axis keeps its data
  web/index.html  the page (HTML, CSS, and JavaScript in one file)
tests/
  test_axis.py    tests using fake model servers
```

## License

MIT. See [LICENSE](LICENSE).
