# Axis

<p align="center"><img src="axislogo.png" width="160" alt="Axis logo"></p>

Axis is a small app for chatting with a local AI model and with agents you design. Run `axis`, and it opens a page in your browser where you can:

* see whether your model server is running and which models are on,
* chat with a model directly,
* make skills, including built-in actions that let agents run Python, create, read and list files, search the web, and read Wikipedia,
* create agents with their own system prompt, config (instructions added to every message), and chosen skills, and
* chat with those agents. Each agent has its own folder, and every file it makes stays there,
* return to any earlier conversation in Past chats, and
* browse every agent's folder in the Files tab: open folders, read documents, view images and tables, upload files for agents to work on, and download anything they made, and
* see each model's memory use, context size and details in the Models tab.

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
5. [Commands](#commands)
6. [Using Axis](#using-axis)
7. [Skills](#skills)
8. [Llama models](#llama-models)
9. [Past chats](#past-chats)
10. [Files and agent folders](#files-and-agent-folders)
11. [The Models tab](#the-models-tab)
12. [Settings](#settings)
13. [Troubleshooting](#troubleshooting)
14. [Running the tests](#running-the-tests)

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
   llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M -c 8192
   ```

   Leave this window open. The model is on while this window runs. `-c 8192` gives the model enough memory of the conversation for agents (see [Llama models](#llama-models)). To use a model file you already have: `llama-server -m C:\path\to\model.gguf -c 8192`

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
   llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M -c 8192
   ```

   Leave this window open. `-c 8192` gives the model enough memory of the conversation for agents (see [Llama models](#llama-models)). To use a model file you already have: `llama-server -m ~/models/model.gguf -c 8192`

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

## Commands

| Command | What it does |
|---|---|
| `axis` | Starts the Axis web app and opens it in your browser |
| `axis update` | Downloads the latest Axis from GitHub and installs it over the current version |
| `axis uninstall` | Removes the Axis program. It asks whether to delete your data too. |
| `axis --version` | Prints the installed version |

`axis update` replaces the program only; your agents, skills and agent files are kept. If Axis is open in another window during an update, stop it with Ctrl+C and run `axis` again to use the new version. Advanced: `axis update --source <ZIP file, folder or URL>` installs from somewhere other than GitHub.

`axis uninstall` asks two questions: whether to remove Axis, and whether to delete your data folder (`Axis` in your home folder) as well. `axis uninstall --yes` removes the program without asking and keeps your data; add `--delete-data` to delete the data too. On Windows the removal finishes a few seconds after the command returns, because Windows cannot delete a program while it is running; details are written to `axis-uninstall.log` in your temp folder.

---

## Using Axis

Run `axis` in a terminal. Axis starts a small website that only this computer can reach and opens it in your browser. Closing the browser tab does not stop Axis; closing the terminal window (or pressing Ctrl+C in it) does. Running `axis` again while it is already running just opens the page again.

### Status

The panel under the Axis name shows whether a model server was found and which models are on. The line under the name is green when Axis is ready, amber when a server is running but no model is on, and red when no server is found. The page checks every 4 seconds; while no model is on, chatting is locked and the page shows the exact commands to start one. It unlocks by itself once a model is on.

### Chat

* **Direct chat** talks to the model with no agent instructions.
* Click an agent in the sidebar to chat with that agent.
* **Model** (top of the chat) switches between the models that are on.
* **Enter** sends; **Shift+Enter** adds a new line. While a reply is being written, **Send** becomes **Stop** (or press **Esc**).
* While the model works, the chat shows **Thinking** with a timer, and each action as it runs ("Running Python…"). You can switch to another page, agent or browser tab and come back: the reply keeps going and is shown where it left off. The sidebar marks any chat that is still working, and the browser tab title says **Working…** (then **Reply ready** if you were away).
* Several chats can work at the same time, for example two agents on separate tasks.
* Each chat keeps its own unsent text, so switching chats never mixes up what you were typing.
* **New chat** starts a fresh conversation; the previous one is kept in [Past chats](#past-chats). **Past chats** in the chat's top bar lists earlier conversations with that agent. **Export** downloads the open conversation as a Markdown file.
* Every conversation is saved by Axis as you go, so it survives reloading the page, closing the browser, and restarting Axis.
* Replies show formatting (bold, lists, code blocks; each code block has its own **Copy** button). Under a reply: **Copy** copies its text, **Retry** asks for a new answer to your last message, and a line shows the model, how many tokens it wrote, its speed, and how long it took.
* Under the message box, a meter shows how much of the model's context the conversation is using (when the server reports token counts).

### Agents

Click **New agent** in the sidebar. An agent has:

| Part | What it does |
|---|---|
| Name | Letters, numbers, `-` and `_`. You can rename an agent later; its chat history and folder move with it. |
| Preferred model | The model the agent uses when it is on. Leave empty to use whichever model is on. You can still switch models in the chat. |
| System prompt | Who the agent is and how it behaves. Sent at the start of every conversation. |
| Config | A list of instructions added to the end of **every message** you send the agent, for rules it must not forget (for example, "Answer in one sentence."). |
| Skills | Tick the skills this agent may use (see [Skills](#skills)). **Manage skills** opens the Skills page. |
| Folder | Shown once the agent is created: where its files are kept, a list of them, and **Open folder** to see them in Explorer or Finder. |

Click **Create agent** (or **Save agent**) to save, **Chat with agent** to start talking, **Duplicate** to start a new agent with the same settings, and **Delete agent** to remove it. **Deleting an agent deletes its folder and every file in it.**

When an agent uses an action, the chat shows a line such as "Wrote notes/plan.md" or "Ran Python". Click it to see the code, the file contents, or the result.

#### What the model receives

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

The config block is attached to your newest message each time you send one; it is not shown in the chat. Agents with built-in action skills also receive a short note that their files live in their own folder, plus the definitions of their actions.

---

## Skills

Click **Skills** in the sidebar to see and manage the skill library. Every agent picks from the same library in its settings.

### Built-in actions

These let an agent do things, not just write text. File actions work only inside that agent's own folder (and the shared folder, if the agent has **Shared folder**).

| Skill | The agent can |
|---|---|
| Run Python | Write a Python program and run it (calculations, data work, processing files). Up to 60 seconds per run. |
| List files | See the files and folders in its folder |
| Read files | Read text files in its folder |
| Make files | Create or overwrite text files, and make folders |
| Delete files | Delete files and empty folders |
| Web search | Search the web (`web_search`) and read the text of a web page (`fetch_page`) |
| Wikipedia | Find articles (`wikipedia_search`) and read them as plain text (`wikipedia_article`) through the Wikipedia API |
| Shared folder | Use its file actions in a folder shared by every agent with this skill, as well as its own |

Built-in skills cannot be edited or deleted.

Actions need a model that can call tools. See [Llama models](#llama-models) for which ones work best.

**The agent chooses how much text comes back.** Web search and Wikipedia take `max_results` (1 to 10) and `max_chars` (200 to 20,000), so a large model can ask for a lot of detail while a small model with a short context asks for a little. Long pages and articles end with a note such as `start=3000`; the agent calls again with that value to read the next part. `wikipedia_article` also takes `intro_only` for just the opening summary, and both Wikipedia actions take `lang` (for example `la` for the Latin Wikipedia).

Web search uses DuckDuckGo by default and needs no account. If DuckDuckGo starts limiting requests, point Axis at a [SearXNG](https://docs.searxng.org/) server with `AXIS_SEARCH_URL` (see [Settings](#settings)). `fetch_page` refuses links to your own computer and local network, so an agent cannot use it to reach Axis, your model server, or devices at home.

**Shared folder** adds no actions of its own. It changes what the file actions (and Run Python) an agent already has can reach: paths that start with `shared/` go to `Axis/shared`, and everything else stays in the agent's own folder. Give it to two or more agents and they can leave files and messages for each other, for example `shared/inbox/to-writer.md`. An agent with Shared folder but no file actions cannot do anything with it. In Run Python, the shared folder's path is in `os.environ["AXIS_SHARED_DIR"]`. While the skill is on, a folder named `shared` inside the agent's own folder is hidden from its file actions (the files are not touched, and reappear if the skill is removed).

**Run Python uses your own Python**, the one Axis was installed with, so packages you have installed there (for example `pip install pandas`) are available to agent code.

### Your skills

Click **New skill** and give it a name, a one-line description (shown when choosing skills), and instructions, for example a house style, a checklist, or a way of answering. Use **Edit** and **Delete** on each card. Each card shows which agents use it. Deleting a skill removes it from every agent that used it.

Skills made in Axis 2.0 (inside an individual agent) move into the library automatically the first time that agent is opened.

---

## Llama models

Axis works with any Llama model, and is tuned for how Llama 3 models behave in Ollama and llama.cpp.

| Model (Ollama name) | Download | Chat | Actions (files, Python) |
|---|---|---|---|
| `llama3.1:8b` | about 4.9 GB | Good | **Recommended.** Handles multi-step tasks well. Needs about 8 GB of free memory. |
| `llama3.2:3b` | about 2 GB | Good | Works for simple tasks (save a file, run a short calculation). Less reliable over many steps. |
| `llama3.2:1b` | about 1.3 GB | Basic | Not recommended for actions. |
| `llama3` (3.0), `llama2` | | Good / basic | No built-in tool support; Axis switches to its text-based tool format automatically, which works but is less reliable. |

Turn one on the usual way, for example `ollama run llama3.1:8b --keepalive -1m`.

What Axis does for Llama models:

* **Larger context.** Ollama gives models 4,096 tokens of memory by default and silently forgets anything beyond that, which can cut off an agent's instructions mid-task. Axis asks Ollama for 8,192 tokens. Because of this, the first message after you turn a model on can take a few extra seconds while Ollama reloads it. Change the amount with `AXIS_NUM_CTX` (see [Settings](#settings)); for llama.cpp, set it with `-c 8192` when starting `llama-server`. Long chats are also shortened automatically so the newest messages and the agent's instructions always fit.
* **Steadier actions.** Agent turns that can use actions run at a low temperature (0.2), which makes Llama's tool calls more consistent.
* **Recovering text tool calls.** Llama models sometimes write a tool call as text (for example `{"name": "write_file", "parameters": {...}}`) instead of making a real call. Axis recognises these, runs the action, and replaces the raw text in the chat with the action itself.
* **Forgiving names.** Common variations such as `python`, `ls`, `mkdir`, `create_file`, `filename`, `text` or `script` are understood as the right action and argument, and Python code sent with `\n` instead of real line breaks is repaired before it runs.
* **No loops.** If an agent repeats the same action, the repeat is not run again; after a few repeats, or after 10 actions in one reply, the agent is asked to give its final answer.
* **Memory of earlier actions.** Each earlier reply is sent back to the model with a short note of the actions it took, so follow-ups such as "now add a line to that file" work.

If `llama-server` refuses tools (older builds started without `--jinja`), Axis uses the text-based tool format automatically.

---

## Past chats

Every conversation is saved automatically, both with agents and in Direct chat. Click **Past chats** in the sidebar to see them all.

* **Show** limits the list to one agent or to Direct chat. **Sort** orders it newest first, oldest first, by agent (grouped under each agent's name), or by title. The search box filters by title.
* Each chat is titled with its first message until you **Rename** it. The line under the title shows the agent, the number of messages, and when it was last used. **Open** marks the chat currently open for that agent, and **Working** marks one that is still replying.
* Click a chat to continue it. Each agent remembers which chat you had open, and **New chat** in the list starts a fresh one.
* **Delete** removes a chat for good (after asking). Axis never deletes chats on its own, with one exception: **deleting an agent deletes all of its chats**. Renaming an agent keeps its chats.
* Chats saved in the browser by earlier versions of Axis are moved into Past chats the first time the page opens.

Chats are stored as `.json` files in `Axis/chats` in your home folder (see [Where the files are](#where-the-files-are)).

---

## Files and agent folders

### The Files tab

Click **Files** in the sidebar to look through what your agents have made.

* Pick an agent at the top, or **Shared folder** to see the folder agents share. Its folder is listed on the left: click a folder to go into it, **Up one folder** or the path above the list to go back.
* Click a file to open it on the right. Text and code are shown as-is; Markdown is shown formatted (with **Show source**); CSV files are shown as a table; images are shown as pictures; PDFs open in a viewer; web pages (`.html`) are shown as source with **Preview page** to see them rendered.
* **Download** saves a copy; **Show in folder** opens Explorer or Finder with the file selected; **Copy text** copies a text file; **Delete** removes it. Axis never runs files you click.
* **Upload** (or dragging files onto the list) copies files from your computer into the folder you are viewing, so an agent can read or process them. An upload never replaces an existing file: a second `data.csv` is saved as `data (2).csv`. Files up to 512 MB can be uploaded.
* **New folder** makes a folder; **Delete folder** removes the folder you are in and everything inside it.
* The list refreshes by itself every few seconds, so new files appear while an agent works.
* In a chat, each action has a link (**Open in Files**) that jumps straight to the file it made.

Web pages an agent makes are previewed in a sealed frame: their scripts can run, but they cannot reach Axis or your other data.

### Where the files are

Each agent's files live in its own folder:

```
Axis/                       in your home folder
├── agents/
│   └── maker/
│       ├── agent.json      the agent's settings
│       └── workspace/      the agent's folder: every file it makes goes here
├── chats/                  saved conversations, one .json file each
├── shared/                 the folder shared by agents with the Shared folder skill
└── skills/                 your custom skills, one .json file each
```

How Axis keeps files in the agent's folder:

* The file actions refuse any path outside the folder (other than `shared/...` for agents with Shared folder), including `..`, absolute paths such as `C:\` or `/Users`, and links that point outside.
* Run Python starts in the folder, and blocks writing, renaming or deleting files anywhere else and starting other programs. Code can still *read* files elsewhere (for example to import installed packages).
* Renaming an agent moves its folder; deleting an agent deletes its folder and everything in it, along with its saved chats.

This guard is designed to keep the agent's ordinary work in its folder. It is not a security sandbox against code written deliberately to escape it, so review what an agent is doing when you give it Run Python.

---

## The Models tab

Click **Models** in the sidebar to see what your model server reports about each model. Nothing here changes a model; it only reads.

**With Ollama**, every model you have downloaded is listed. Models that are on show:

| Detail | Meaning |
|---|---|
| Memory in use | RAM and video memory the loaded model takes, including its context cache, and how much of it is on the GPU |
| Context window | How many tokens the model can hold right now, and the most the model supports |
| Parameters, Quantization, Size on disk | The model's size and compression (for example 3.2B, Q4_K_M) |
| Architecture, Layers, Embedding size, Vocabulary | Technical details from the model file |
| Stays loaded | When Ollama will unload it ("until you stop it" with `--keepalive -1m`) |
| Capabilities | Chat, Tools, Vision, Thinking or Embeddings, as Ollama reports them |

Models that are installed but not on show an **estimated memory** figure: the download size plus the context cache needed for the context Axis asks for (8,192 tokens unless you change `AXIS_NUM_CTX`). Treat it as approximate; the real figure appears once the model is on. Each card has the command to turn the model on (or off) and, for models that are on, **Chat with this model**.

**With llama.cpp**, the loaded model shows its context window (per slot), the context it was trained for, its parameter count, the memory its weights take, the number of parallel slots, and the model file. llama.cpp does not report the context cache, so actual memory use is somewhat higher than the weights alone.

If a model has no built-in tool support, its card says so; agents with actions still work through Axis's text format, but less reliably.

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
| `AXIS_HOME` | Where agents, skills and agent files are saved | `Axis` in your home folder |
| `AXIS_PYTHON` | Python used by the Run Python action | The Python Axis was installed with |
| `AXIS_NUM_CTX` | Context size Axis asks Ollama for, in tokens (0 = Ollama's own default) | 8192 |
| `AXIS_HISTORY_CHARS` | How much earlier conversation is sent with each message, in characters | 20000 |
| `AXIS_SEARCH_URL` | A SearXNG server for the Web search skill (its JSON format must be enabled) | DuckDuckGo |

To set one, start Axis like this: `$env:AXIS_NUM_CTX = "16384"; axis` (Windows PowerShell) or `AXIS_NUM_CTX=16384 axis` (Mac).

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
| An agent forgets its instructions or what it was doing | The conversation outgrew the model's memory. Start a **New chat**, or raise `AXIS_NUM_CTX` if your computer has memory to spare. For llama.cpp, start `llama-server` with `-c 8192` or more. |
| The first reply after turning a model on is slow | Normal: Ollama reloads the model with the larger context Axis asks for. Later replies are fast. |
| An agent describes an action but nothing happens | Make sure the skill is ticked in the agent's settings, and use a model that handles actions well, such as `llama3.1:8b` (see [Llama models](#llama-models)). Asking directly also helps: "Save this as notes.md". |
| `Unknown tool '...'` in a step | The agent tried an action whose skill is not ticked. Tick it in **Edit agent**, or ignore it. |
| Run Python says a package is missing | Install it for the Python that runs Axis, e.g. `py -m pip install pandas` (Windows) or `python3 -m pip install pandas` (Mac) |
| `axis update` fails | Check your internet connection. You can also reinstall by hand: `pipx install --force https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip` |
| Chats disappeared | Conversations are stored per browser and per port. Use the same browser and the default port. |

---

## Running the tests

The tests start fake Ollama and llama.cpp servers, so no real model is needed. From the project folder:

```bash
python -m unittest discover -s tests -v      # Windows: py -m unittest discover -s tests -v
```

## Project layout

```
axis/
  cli.py          the `axis`, `axis update` and `axis uninstall` commands
  server.py       local web server and JSON API
  agents.py       agent storage, agent folders, and how messages are assembled
  chats.py        saved conversations (Past chats)
  skills.py       the skill library (built-in actions and your skills)
  tools.py        the actions, kept inside each agent's folder
  runner.py       lets an agent use its actions while replying
  backend.py      detects Ollama or llama.cpp, reads model details, streams replies, tool calls and token counts
  paths.py        where Axis keeps its data
  web/index.html  the page (HTML, CSS and JavaScript in one file, with the logo built in)
tests/
  test_axis.py    tests for all of the above, using fake model servers
axislogo.png      the Axis logo
```

## License

MIT. See `LICENSE`.
