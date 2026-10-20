# Axis

Axis is a small command-line tool for talking to a local AI model and running simple "agents" that work through task files.

Axis does **not** download, start, or stop models. You start a model yourself with **Ollama** or **llama.cpp**, and Axis uses whatever model is loaded. If no model server is running, or no model is loaded, every Axis command that needs a model stops with an error that tells you what to start.

```
you start the model  ──►  Axis detects it  ──►  axis ask / chat / run
(Ollama or llama.cpp)     (ports 11434, 8080)
```

---

## Contents

1. [Dependencies](#dependencies)
2. [Windows setup](#windows-setup)
3. [Mac setup](#mac-setup)
4. [Every-day use: turning the model on and off](#every-day-use-turning-the-model-on-and-off)
5. [Command reference](#command-reference)
6. [Agents and task files](#agents-and-task-files)
7. [Settings](#settings)
8. [Troubleshooting](#troubleshooting)
9. [Updating and uninstalling](#updating-and-uninstalling)
10. [Running the tests](#running-the-tests)

---

## Dependencies

| Dependency | Required? | Purpose |
|---|---|---|
| Python 3.9 or newer | Required | Runs Axis |
| pipx | Recommended | Installs the `axis` command so it works in any terminal |
| Ollama **or** llama.cpp | Required (pick one) | Serves the model that Axis talks to |
| Git | Optional | Only for installing straight from GitHub |

Axis itself uses **only the Python standard library**. There are no Python packages to install besides Axis.

Hardware: a 3-billion-parameter model (the examples below) needs about 3 to 4 GB of free RAM. Larger models need more.

---

## Windows setup

Use **PowerShell** for every command below (Start menu, type "PowerShell"). Windows 10 or 11 is required.

### Step 1. Install Python

1. Go to <https://www.python.org/downloads/> and download the latest Python 3 installer.
2. Run it. On the first screen, **check "Add python.exe to PATH"**, then click **Install Now**.
3. Close PowerShell, open a new one, and confirm:

   ```powershell
   py --version
   ```

   You should see `Python 3.x.x` (3.9 or higher).

### Step 2. Install pipx

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

**Close PowerShell and open a new window** so the PATH change takes effect.

### Step 3. Install Axis

Choose one option.

**Option A: from a downloaded ZIP (no Git needed).** On the GitHub page, click **Code → Download ZIP**, then:

```powershell
cd $HOME\Downloads
pipx install .\Axis-main.zip
```

**Option B: from GitHub with Git** (install Git from <https://git-scm.com/download/win> first):

```powershell
pipx install git+https://github.com/HyrumHendrickson/Axis.git
```

**Option C: from an extracted project folder:**

```powershell
cd path\to\Axis-main
pipx install .
```

Confirm:

```powershell
axis --version
```

### Step 4. Install a model server (choose Ollama or llama.cpp)

Ollama is the easier choice. You only need one.

#### Option 1: Ollama (recommended)

1. Download the Windows installer from <https://ollama.com/download> and run it, or in PowerShell:

   ```powershell
   winget install Ollama.Ollama
   ```

2. Ollama starts automatically in the background and shows an icon in the system tray. Open a **new** PowerShell and confirm:

   ```powershell
   ollama --version
   ```

3. Download and turn on a model (the first run downloads about 2 GB):

   ```powershell
   ollama run llama3.2:3b --keepalive -1m
   ```

   This opens Ollama's own chat prompt. Type `/bye` to leave it; **the model stays loaded** because of `--keepalive -1m`. Any model from <https://ollama.com/library> works the same way.

#### Option 2: llama.cpp

1. Install it:

   ```powershell
   winget install llama.cpp
   ```

   Open a new PowerShell afterwards. (Alternatively, download a Windows ZIP from <https://github.com/ggml-org/llama.cpp/releases>, extract it, and run the commands below from that folder.)

2. Start the server with a model (the first run downloads the model from Hugging Face):

   ```powershell
   llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M
   ```

   Leave this window open. The model is on while this window runs. To use a model file you already have: `llama-server -m C:\path\to\model.gguf`

### Step 5. Use Axis

In a **separate** PowerShell window:

```powershell
axis status
axis ask "Explain recursion in two sentences."
axis chat
```

If `axis status` prints `Axis is ready.`, setup is complete.

---

## Mac setup

Use the **Terminal** app (Applications → Utilities → Terminal). macOS 12 or newer is recommended.

### Step 1. Install Python

Check whether you already have Python 3.9 or newer:

```bash
python3 --version
```

If the command is missing, or older than 3.9, download the macOS installer from <https://www.python.org/downloads/> and run it. (If macOS offers to install "command line developer tools" when you type `python3`, accepting that also works; it provides Python 3.9.)

### Step 2. Install pipx

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

**Close Terminal and open a new window.**

If you use Homebrew, `brew install pipx && pipx ensurepath` also works. (Homebrew's own Python refuses `pip install --user`; use the Homebrew command in that case.)

### Step 3. Install Axis

Choose one option.

**Option A: from a downloaded ZIP.** On the GitHub page, click **Code → Download ZIP**, then:

```bash
cd ~/Downloads
pipx install ./Axis-main.zip
```

If Safari unzipped it automatically, use `pipx install ./Axis-main` instead.

**Option B: from GitHub with Git** (Git comes with the command line developer tools):

```bash
pipx install git+https://github.com/HyrumHendrickson/Axis.git
```

Confirm:

```bash
axis --version
```

### Step 4. Install a model server (choose Ollama or llama.cpp)

#### Option 1: Ollama (recommended)

1. Download the Mac app from <https://ollama.com/download>, open the downloaded file, and drag **Ollama** into **Applications**. Open it once from Applications; it runs in the menu bar and starts the server automatically.

   Homebrew alternative: `brew install ollama`, then run `ollama serve` in its own Terminal window and leave it open (the Homebrew version has no menu bar app).

2. Confirm in a new Terminal window:

   ```bash
   ollama --version
   ```

3. Download and turn on a model:

   ```bash
   ollama run llama3.2:3b --keepalive -1m
   ```

   Type `/bye` to leave Ollama's chat prompt. The model stays loaded.

#### Option 2: llama.cpp

1. Install Homebrew if you do not have it (instructions at <https://brew.sh>), then:

   ```bash
   brew install llama.cpp
   ```

2. Start the server with a model:

   ```bash
   llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M
   ```

   Leave this window open. To use a model file you already have: `llama-server -m ~/models/model.gguf`

Apple Silicon Macs (M1 and later) use the GPU automatically with both options.

### Step 5. Use Axis

In a **separate** Terminal window:

```bash
axis status
axis ask "Explain recursion in two sentences."
axis chat
```

---

## Every-day use: turning the model on and off

Axis only works while a model is on.

| | Ollama | llama.cpp |
|---|---|---|
| Server running? | Ollama app open (tray/menu bar icon), or `ollama serve` | `llama-server` window open |
| Turn a model on | `ollama run <model> --keepalive -1m`, then `/bye` | `llama-server -hf <repo>:<quant>` or `-m <file.gguf>` |
| See what is on | `ollama ps` or `axis status` | `axis status` |
| Turn a model off | `ollama stop <model>` | Press Ctrl+C in the `llama-server` window |
| Default address | `http://127.0.0.1:11434` | `http://127.0.0.1:8080` |

Notes on Ollama:

* Without `--keepalive -1m`, Ollama unloads a model after 5 idle minutes, and Axis will then report that no model is loaded.
* While Axis is talking to a model, it asks Ollama to keep that model loaded until you run `ollama stop <model>`.
* You can load several models at once (memory permitting). Axis uses the first one listed by `axis status` unless you choose one with `-m`.

---

## Command reference

Every command accepts `--help`, e.g. `axis chat --help`.

| Command | What it does | Needs a model on? |
|---|---|---|
| `axis status` | Shows the detected server and loaded models | Yes |
| `axis ask "question"` | Asks one question and prints the answer as it is written | Yes |
| `axis chat` | Interactive conversation | Yes |
| `axis run <agent>` | Processes the agent's task files until Ctrl+C | Yes |
| `axis agent create <name>` | Creates an agent | No |
| `axis agent list` | Lists agents and their pending task counts | No |
| `axis agent show <name>` | Shows an agent's settings and folder | No |
| `axis agent edit <name>` | Changes an agent's prompt or model | No |
| `axis agent delete <name>` | Deletes an agent and its files (asks first) | No |
| `axis --version` | Prints the Axis version | No |

### Options

* `ask` and `chat`: `-a/--agent NAME` to use an agent's system prompt and model; `-m/--model NAME` to pick a specific loaded model (overrides the agent's model).
* `ask` also reads from a pipe: `Get-Content notes.txt | axis ask` (Windows) or `cat notes.txt | axis ask` (Mac).
* `run`: `--once` processes what is waiting and exits; `--interval SECONDS` sets how often to check (default 2).
* `agent create` / `agent edit`: `-p/--prompt "..."` sets the system prompt; `-m/--model NAME` sets a preferred model; `edit --clear-model` goes back to "whichever model is loaded".
* `agent delete -y` skips the confirmation.
* Global: `axis --url http://host:port <command>` uses a specific server (see [Settings](#settings)).

### Inside `axis chat`

| Type | Effect |
|---|---|
| `/help` | List chat commands |
| `/clear` | Forget the conversation so far |
| `/model` | Show the model in use |
| `/exit` or `/quit` | Leave (Ctrl+D on Mac, Ctrl+Z then Enter on Windows also work) |
| Ctrl+C while a reply prints | Stop that reply and keep chatting |

The chat keeps the last 40 messages as context.

---

## Agents and task files

An agent is a named system prompt (its personality and instructions), an optional preferred model, and a folder of task files.

```bash
axis agent create editor -p "You are a careful copy editor. Return the corrected text only."
axis ask -a editor "Their going to the libary tomorow."
axis chat -a editor
```

### Folder layout

Agents live in `Axis/agents/` in your home folder (`C:\Users\<you>\Axis\agents\` on Windows, `/Users/<you>/Axis/agents/` on Mac):

```
Axis/agents/editor/
├── agent.json        settings (you may edit this file directly)
├── tasks/            put .txt or .md task files here
│   ├── done/         finished tasks are moved here
│   └── failed/       tasks that could not be processed
└── results/          one answer per task, as <task-name>.md
```

### Processing tasks

1. Save a task as a text file, e.g. `Axis/agents/editor/tasks/letter.txt`. The whole file is sent as the request.
2. Run:

   ```bash
   axis run editor
   ```

3. The answer appears in `results/letter.md` and the task moves to `tasks/done/`. Axis keeps watching for new files until you press Ctrl+C. Use `axis run editor --once` to process what is waiting and exit.

If a task fails (for example, the model returns an error), it moves to `tasks/failed/` and the reason is written to `results/<task-name>.error.txt`. If the model server goes away while `axis run` is watching, Axis waits and resumes when the model is back; nothing is marked failed. Re-using a task name never overwrites an earlier result; a timestamp is added instead.

`axis run` uses whichever terminal you start it in. To keep chatting at the same time, open another terminal window.

---

## Settings

| Setting | How | Default |
|---|---|---|
| Server address | `--url` option, or `AXIS_URL` environment variable | Tries `127.0.0.1:11434` (Ollama), then `127.0.0.1:8080` (llama.cpp) |
| Data folder | `AXIS_HOME` environment variable | `Axis` in your home folder |

Examples for a llama.cpp server on a non-default port:

```powershell
# Windows, this window only
$env:AXIS_URL = "http://127.0.0.1:9000"
```

```bash
# Mac, this window only
export AXIS_URL=http://127.0.0.1:9000
```

Any server with an OpenAI-compatible `/v1/models` and `/v1/chat/completions` API (for example LM Studio on port 1234) also works when given with `--url`.

---

## Troubleshooting

| Message or symptom | Fix |
|---|---|
| `No model server is running at ...` | Start Ollama (open the app, or `ollama serve`) or `llama-server`. Check the port with `--url` if you changed it. |
| `... is running ... but no model is loaded` | Turn a model on: `ollama run llama3.2:3b --keepalive -1m` |
| `Model 'X' is not loaded` | Load that model, or run without `-m`, or change the agent with `axis agent edit <name> --clear-model` |
| `still loading its model` (llama.cpp) | Wait until `llama-server` prints that the server is listening, then retry |
| `axis` is not recognized / command not found | Run `py -m pipx ensurepath` (Windows) or `python3 -m pipx ensurepath` (Mac) and open a new terminal. As a fallback, run it by full path: `$HOME\.local\bin\axis.exe` (Windows) or `~/.local/bin/axis` (Mac). |
| `py` is not recognized (Windows) | Reinstall Python with "Add python.exe to PATH" checked, or use `python` instead of `py` |
| `ollama` is not recognized | Open a new terminal after installing; on Windows confirm Ollama appears in the tray |
| Replies are very slow | Use a smaller model (e.g. `llama3.2:1b`) or close other programs to free memory |
| Strange characters in Windows output | Use Windows Terminal (default on Windows 11) rather than the old console |

---

## Updating and uninstalling

To update, get the new version and install over the old one with `--force`:

```bash
pipx install --force ./Axis-main.zip                                          # from a new ZIP
pipx install --force git+https://github.com/HyrumHendrickson/Axis.git         # from GitHub
```

To uninstall:

```bash
pipx uninstall axis-agent-manager
```

Uninstalling Axis does not delete your agents. To remove them, delete the `Axis` folder in your home folder. Ollama and llama.cpp are uninstalled separately (Windows: Settings → Apps; Mac: drag Ollama out of Applications, or `brew uninstall llama.cpp`).

---

## Running the tests

The tests start fake Ollama and llama.cpp servers, so no real model is needed. From the project folder:

```bash
python -m unittest discover -s tests -v      # Windows: py -m unittest discover -s tests -v
```

## Project layout

```
axis/
  backend.py   detects the server, lists loaded models, streams replies
  agents.py    agent storage
  worker.py    task-file processing for `axis run`
  cli.py       commands
tests/
  test_axis.py end-to-end tests against mock servers
```

## License

MIT. See `LICENSE`.
