# Axis

Axis is a small app for chatting with a local AI model and with agents you design. Run `axis`, and it opens a page in your browser where you can:

* see whether your model server is running and which models are on,
* chat with a model directly,
* create agents with their own system prompt, config (instructions added to every message), and skills, and
* chat with those agents.

Axis does **not** download, start, or stop models. You start a model yourself with **Ollama** or **llama.cpp**; until a model is on, the page shows what to start and keeps chat locked.

```
you start the model  ──►  run axis  ──►  browser opens at http://127.0.0.1:8765
(Ollama or llama.cpp)
```

---

## Contents

1. [Dependencies](#dependencies)
2. [Windows setup](#windows-setup)
3. [Mac setup](#mac-setup)
4. [Every-day use: turning the model on and off](#every-day-use-turning-the-model-on-and-off)
5. [Using Axis](#using-axis)
6. [Settings](#settings)
7. [Troubleshooting](#troubleshooting)
8. [Updating and uninstalling](#updating-and-uninstalling)
9. [Running the tests](#running-the-tests)

---

## Dependencies

| Dependency | Required? | Purpose |
|---|---|---|
| Python 3.9 or newer | Required | Runs Axis |
| pipx | Recommended | Installs the `axis` command so it works in any terminal |
| Ollama **or** llama.cpp | Required (pick one) | Serves the model that Axis talks to |
| Git | Optional | Only for installing straight from GitHub |
| A web browser | Required | Any current Chrome, Edge, Firefox, or Safari |

Axis itself uses **only the Python standard library**, and the page uses no outside scripts or fonts, so it works offline. There are no Python packages to install besides Axis.

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

### Step 5. Start Axis

In a **separate** PowerShell window:

```powershell
axis
```

Your browser opens at `http://127.0.0.1:8765`. The status panel at the top left should say **Ollama is ready** (or **llama.cpp / OpenAI-compatible is ready**). Leave the PowerShell window open while you use Axis; press Ctrl+C in it to stop Axis.

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

### Step 5. Start Axis

In a **separate** Terminal window:

```bash
axis
```

Your browser opens at `http://127.0.0.1:8765`. The status panel at the top left should say **Ollama is ready** (or **llama.cpp / OpenAI-compatible is ready**). Leave the Terminal window open while you use Axis; press Ctrl+C in it to stop Axis.

---

## Every-day use: turning the model on and off

Axis only works while a model is on.

| | Ollama | llama.cpp |
|---|---|---|
| Server running? | Ollama app open (tray/menu bar icon), or `ollama serve` | `llama-server` window open |
| Turn a model on | `ollama run <model> --keepalive -1m`, then `/bye` | `llama-server -hf <repo>:<quant>` or `-m <file.gguf>` |
| See what is on | `ollama ps`, or the Axis status panel | The Axis status panel |
| Turn a model off | `ollama stop <model>` | Press Ctrl+C in the `llama-server` window |
| Default address | `http://127.0.0.1:11434` | `http://127.0.0.1:8080` |

Notes on Ollama:

* Without `--keepalive -1m`, Ollama unloads a model after 5 idle minutes, and Axis will then report that no model is loaded.
* While Axis is talking to a model, it asks Ollama to keep that model loaded until you run `ollama stop <model>`.
* You can load several models at once (memory permitting). Pick between them with the **Model** menu at the top of any chat.

---

## Using Axis

Run `axis` in a terminal. Axis starts a small website that only this computer can reach and opens it in your browser. Closing the browser tab does not stop Axis; closing the terminal window (or pressing Ctrl+C in it) does. Running `axis` again while it is already running just opens the page again.

### Status

The panel under the Axis name shows whether a model server was found and which models are on. The line under the name is green when Axis is ready, amber when a server is running but no model is on, and red when no server is found. The page checks every 4 seconds; while no model is on, chatting is locked and the page shows the exact commands to start one. It unlocks by itself once a model is on.

### Chat

* **Direct chat** talks to the model with no agent instructions.
* Click an agent in the sidebar to chat with that agent.
* **Model** (top of the chat) switches between the models that are on.
* **Enter** sends; **Shift+Enter** adds a new line. While a reply is being written, **Send** becomes **Stop**.
* **New chat** clears the conversation. Conversations are saved in your browser, so they survive reloading the page.
* Replies show formatting (bold, lists, code blocks). **Copy** under a reply copies its text.

### Agents

Click **New agent** in the sidebar. An agent has:

| Part | What it does |
|---|---|
| Name | Letters, numbers, `-` and `_`. You can rename an agent later; its chat history moves with it. |
| Preferred model | The model the agent uses when it is on. Leave empty to use whichever model is on. You can still switch models in the chat. |
| System prompt | Who the agent is and how it behaves. Sent at the start of every conversation. |
| Config | A list of instructions added to the end of **every message** you send the agent, for rules it must not forget (for example, "Answer in one sentence."). |
| Skills | Named blocks of instructions, each with an **On** switch. Skills that are on are added to the system prompt; skills that are off are kept but not sent. |

Click **Create agent** (or **Save agent**) to save, **Chat with agent** to start talking, and **Delete agent** to remove it. The text under the message box in an agent chat shows how many config instructions and skills are active.

Agents are saved as `Axis/agents/<name>/agent.json` in your home folder (`C:\Users\<you>\Axis` on Windows, `/Users/<you>/Axis` on Mac). Agents made with Axis 1.0 open without changes.

#### What the model receives

For an agent with the system prompt "You are a copy editor.", one skill switched on, and two config instructions, sending "their going home" produces:

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

The config block is attached to your newest message each time you send one; it is not shown in the chat.

---

## Settings

Options for the `axis` command:

| Option | Effect |
|---|---|
| `--port 9000` | Use a different port for the page (default 8765; if it is taken, Axis uses the next free one) |
| `--no-browser` | Start without opening a browser; open the printed address yourself |
| `--url http://127.0.0.1:9000` | Use a model server at a specific address |
| `--version` | Print the version |

Environment variables:

| Variable | Effect | Default |
|---|---|---|
| `AXIS_URL` | Model server address | Tries `127.0.0.1:11434` (Ollama), then `127.0.0.1:8080` (llama.cpp) |
| `AXIS_HOME` | Where agents are saved | `Axis` in your home folder |

Example for a llama.cpp server on a non-default port:

```powershell
axis --url http://127.0.0.1:9000          # Windows and Mac alike
```

Any server with an OpenAI-compatible `/v1/models` and `/v1/chat/completions` API (for example LM Studio on port 1234) also works when given with `--url`.

The page only answers requests from this computer, and only from its own page, so other websites open in your browser cannot use it.

---

## Troubleshooting

| Message or symptom | Fix |
|---|---|
| Status says **No model server** | Start Ollama (open the app, or `ollama serve`) or `llama-server`. If it uses a non-default port, start Axis with `--url`. |
| Status says **No model is on** | Turn a model on: `ollama run llama3.2:3b --keepalive -1m` |
| The page says **Axis has stopped** | The terminal running `axis` was closed. Run `axis` again and reload the page. |
| A chat shows "This agent prefers X, which is not on" | Turn that model on, or clear the agent's preferred model in **Edit agent** |
| The browser did not open | Open the address printed in the terminal (normally `http://127.0.0.1:8765`) |
| A reply stops with an error | The message is put back in the box; check the status panel, then send again |
| `axis` is not recognized / command not found | Run `py -m pipx ensurepath` (Windows) or `python3 -m pipx ensurepath` (Mac) and open a new terminal. As a fallback, run it by full path: `$HOME\.local\bin\axis.exe` (Windows) or `~/.local/bin/axis` (Mac). |
| `py` is not recognized (Windows) | Reinstall Python with "Add python.exe to PATH" checked, or use `python` instead of `py` |
| `ollama` is not recognized | Open a new terminal after installing; on Windows confirm Ollama appears in the tray |
| Replies are very slow | Use a smaller model (for example `llama3.2:1b`) or close other programs to free memory |
| Chats disappeared | Conversations are stored per browser and per port. Use the same browser and the default port. |

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
  cli.py          the `axis` command: starts the server and opens the browser
  server.py       local web server and JSON API
  agents.py       agent storage and how messages are assembled
  backend.py      detects Ollama or llama.cpp, lists models, streams replies
  web/index.html  the page (HTML, CSS and JavaScript in one file)
tests/
  test_axis.py    tests for the backend, agents, API and launcher
```

## License

MIT. See `LICENSE`.
