# User guide

A tour of every page in Axis. Installation is covered in [installation.md](installation.md) and settings in [configuration.md](configuration.md).

## Chat

- **Direct chat** talks to the model with no agent instructions. Open **Agents** in the sidebar and click an agent to chat with that agent.
- **Enter** sends; **Shift+Enter** adds a new line. **Send** becomes **Stop** while a reply is being written (or press **Esc**).
- You can switch pages or tabs while a reply is generating. The sidebar marks chats that are still working, and several chats can work at once.
- Every conversation is saved as you go. **Export** downloads the open conversation as Markdown.
- **Edit** under any of your messages turns it into a text box. Change it and press **Send** (or **Enter**) to send the conversation again from that point; **Cancel** (or **Esc**) leaves it as it was. Every message after the edited one is replaced by the new reply, and pictures attached to the message are kept. **Copy** copies your message.
- **Pictures in chats.** When the model can read pictures (**Vision** on the Models page, for example `gemma3:4b`), use **Attach picture** under the message box, or paste or drop pictures into it, or choose one already in Axis with **From Workspace**. Up to four pictures go with one message, and large ones are shrunk first. **Attach picture** is dimmed when the model cannot read pictures.
- Under each reply: **Copy**, **Retry**, and a line showing the model, token count, speed, and duration. A meter under the message box shows context usage when the server reports it.

## Agents

Open **Agents** in the sidebar and click **New agent**. The Agents page lists every agent with **Chat**, **Edit**, **Workspace**, and **Past chats**.

| Field | Purpose |
|-------|---------|
| Name | Letters, numbers, `-` and `_`. Renaming moves the agent's history and folder with it. |
| Preferred model | Model used when it is on. Leave empty to use whichever model is on. |
| System prompt | Who the agent is and how it behaves. Sent at the start of every conversation. |
| Config | Instructions appended to **every message** you send, for rules the agent must not forget. |
| Skills | Which skills the agent may use. |
| Folder | Where the agent's files are kept. **Open folder** shows it in Explorer or Finder. |

**The agent's folder.** Agents work in the workspace folder (see [Workspace](#workspace)). In an agent's chat, the **Folder** menu can keep that chat's agent in another folder instead: choose **Choose a folder…**, open any folder on the computer, and click **Use this folder**. The choice is saved with the chat; a new chat starts in the workspace folder again. Either way the agent cannot leave its folder: its Python code cannot read, write, list, or change to anything outside it (it may still read Python's own files and the system's program folders, which code needs to run), and terminal or Rust code that names a place outside the folder (a full path, a `..` that climbs out, the home folder, or a bare `cd`) is refused before it runs. These guards catch mistakes; they are not a sandbox against code written to get around them. Pictures an agent makes always go to `Axis/images`.

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

         Follow these instructions:
         - Answer in one sentence.
         - Use US spelling.
```

The config block is attached to your newest message each time and is not shown in the chat. Agents with action skills also receive a note that their files live in their own folder, plus the action definitions.

</details>

## Skills

Choose an agent's skills in its settings (**Edit agent**). Every agent chooses from the same library.

| Built-in skill | Capability |
|----------------|------------|
| Run Python | Write and run Python (`run_python`), or run a `.py` file already in the agent's folder (`run_python_file`); 60 seconds per run |
| Run Rust | Compile and run Rust (`run_rust`), or compile a `.rs` file already in the agent's folder (`run_rust_file`); 60 seconds per run |
| Run terminal code | Run shell code (`run_terminal`), or a shell program already in the agent's folder (`run_terminal_file`). The default is bash or sh on macOS and Linux, and cmd on Windows; PowerShell and cmd are available on Windows. |
| List files | List files and folders in the agent's folder |
| Read files | Read text files |
| Make files | Create or overwrite text files and make folders |
| Delete files | Delete files and empty folders |
| Web search | `web_search` and `fetch_page` |
| Wikipedia | `wikipedia_search` and `wikipedia_article` |
| CAS | `run_cas`: exact math with Maxima or SymPy from the **CAS** page. Listed under **Tools**. |
| Images | `generate_image` with a local Stable Diffusion model, as on the **Images** page. Listed under **Tools**. |
| OCR | `read_text`: reads the text in a picture in the agent's folder with an OCR model, as on the **OCR** page. Listed under **Tools**. |
| Shared folder | Lets file actions reach `shared/`, a folder common to all agents with this skill |

| Art Craft apps | One skill for each Craft app (PhotoCraft, LightCraft, FilmCraft, DesignCraft, VectorCraft, PdfCraft, EffectCraft, WordCraft, DeckCraft, GridCraft, CADCraft, SoundCraft, FigCraft). See below. |

Notes:

- Actions require a model that can call tools. See [Recommended models](#recommended-models).
- File actions are confined to the agent's own folder (plus `shared/` with Shared folder). Paths using `..`, absolute paths, and links pointing outside are refused.
- Run Python starts in the agent's folder (a Python file starts in its own folder) and blocks writing, renaming, or deleting elsewhere, and launching other programs. It uses the Python Axis was installed with. To make a package such as pandas available to agents, install it into that environment: `pipx inject axis-agent-manager pandas`.
- This guard is not a security sandbox against code deliberately written to escape it. Review what an agent does when you give it Run Python.
- Run Rust and Run terminal code use the user's permissions and are not security sandboxes. Rust uses the portable compiler from **Art Craft** when it is installed, or `rustc` on the PATH. On Windows, installing Rust also downloads Microsoft's C++ Build Tools when the MSVC linker needs them. Terminal code accepts `shell: powershell` or `shell: cmd` on Windows.
- Web search uses DuckDuckGo by default. If it rate-limits you, set `AXIS_SEARCH_URL` to a [SearXNG](https://docs.searxng.org/) server. `fetch_page` refuses links to your own machine and local network (including private, link-local, and shared addresses such as Tailscale's `100.x.x.x`), checks the address it actually connects to, and gives up on a site that sends its answer too slowly.
- Agents choose how much text to retrieve (`max_results`, `max_chars`). Long pages end with a `start=` marker so the agent can continue reading. Wikipedia actions accept `lang` (for example `la` for Latin).
- Agents can leave each other files in `shared/` (for example `shared/inbox/to-writer.md`). In Run Python the path is in `os.environ["AXIS_SHARED_DIR"]`.

**Tools skills.** Each page under **Tools** has a skill, listed under **Tools** in an agent's settings: **CAS**, **Images** and **OCR** (the Craft apps have their own, below). Each gives the agent one action that uses what you set up on that page, so set it up there first. The CAS skill uses the system chosen under **CAS agents use** on the CAS page unless the agent asks for the other one. Maxima code from an agent is read before it runs and refused when it starts other programs (`system`), uses Lisp, or names a file outside the agent's folder; SymPy code runs under the same guard as Run Python. Neither is a sandbox. The OCR skill reads pictures in the agent's folder only, with a running PyTorch OCR model if one is on, otherwise Tesseract English, unless the agent names another installed model.

**Art Craft skills.** Each Craft app has its own skill, listed under **Art Craft apps** in an agent's settings. A skill teaches an agent that app's command-line program (for example `photocraft-cli`): how to look up commands, how to write their parameters, and how to check the result. Each skill also gives the agent the Run terminal code actions, so choosing it is enough. The app must be installed on the **Art Craft** page. Axis adds the folders of installed apps' command-line programs to the PATH of programs an agent starts, so the agent runs them by name; your own PATH is not changed. Files an agent makes stay in its folder, and the skills tell the agent not to overwrite originals. Like Run terminal code, they use your permissions and are not a sandbox. The macOS download of an app holds only the app, not its command-line program, so on a Mac these skills work only if the program (the separate `<app>-cli-<version>-macos-universal.zip` from the app's releases page) is unpacked into the app's folder under `Axis/engines/artcraft`. The Craft apps also have an MCP server; Axis does not use it. If a newer app release changes a command, edit the skill's file in `Axis/skills/built-in`.

**Custom skills:** click **New skill**, then enter a name, a one-line description, and instructions (a house style, a checklist, a way of answering). **Edit** and **Delete** are on each card. Deleting a skill removes it from every agent that used it.

**Editing a built-in skill.** **Edit** on a built-in skill changes its name, description, and instructions, which is the text it adds to the model's prompt. Its actions (what the agent can do) are fixed and cannot be changed, and built-in skills cannot be deleted. An edited skill shows an **Edited** tag. **Reset to default** puts the text back the way Axis ships it; it is dimmed while the skill is unchanged. Edits are kept in `skills/built-in/` in your data folder, so they survive `axis update`.

**What this sends to the model.** Every card has this dropdown. It shows, exactly as sent: the text the skill adds to the system message, the action definitions given to the model, and the same actions written out as text (used with models that cannot call tools). It reads from the same code that builds a real chat, so it always matches what an agent sends, including your edits. The agent's own system prompt and config instructions are separate; see [What the model receives](#agents).

## Recommended models

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

## Past chats

Every conversation is saved automatically. Open **Past chats** in the sidebar to filter by agent, sort (newest, oldest, by agent, by title), search by title, rename, resume, or delete. Axis never deletes chats on its own, except that deleting an agent deletes its chats. Chats are stored as `.json` files in `Axis/chats` in your home folder.

## Images

Open **Tools** in the sidebar, then **Images**. Axis generates pictures with [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp), which it installs itself (see [Model engines](installation.md#model-engines)).

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

**Show each step.** With **Show each step** on (under **Generate** on the Images tab, and under the message box when chatting with an agent that has the Image generation skill), the picture appears after every sampling step and sharpens as it goes. The previews use stable-diffusion.cpp's `proj` method, a cheap projection of the latent to colour at one eighth of the final size, so they add very little time. Turn it off to skip them; with `sd-cli` that also stops it writing a preview file after each step. The setting is remembered and applies to the next picture.

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

**Images tab.** Choose a model (its defaults fill in), write a prompt and optional negative prompt, set resolution, steps, CFG scale, sampler, and seed (-1 picks a random seed; the used seed is always shown), then **Generate** (or Ctrl+Enter). Pictures are saved in `Axis/images`, never in the workspace. The gallery shows that folder (**Open folder** opens it) and supports **Reuse settings**, **Reuse with seed**, **Download**, **Show in folder**, and **Delete**. One picture is made at a time.

**Edit a picture.** On the Images tab, switch from **Create** to **Edit a picture**. Drop a picture on the box, click **Upload**, or choose one with **Choose a saved picture** (from the workspace folder or the images folder), describe how it should look, and set **Strength**: low keeps most of the original, high changes more (image to image, using the image models you have). **Keep the picture’s shape** makes the result the same shape as the original. The result goes to the gallery like any other picture.

**Agents.** Give an agent the **Image generation** skill. It calls `generate_image` with a prompt (and optionally size, steps, seed, or model). The picture is saved in `Axis/images`, not in the agent's folder, and shown in the chat.

## OCR

Open **Tools** in the sidebar, then **OCR**. Axis reads the text in pictures with one of two kinds of engine:

- **Tesseract**, a fast, light program that reads more than 100 languages, including Latin (`lat`) and ancient Greek (`grc`). Each language is a model.
- **PyTorch OCR models**: [EasyOCR](https://github.com/JaidedAI/EasyOCR) (pick the languages, for example `en,fr`) and [docTR](https://github.com/mindee/doctr) (a text detector and recognizer, for example `fast_base+crnn_vgg16_bn`). They are slower without a graphics card, but often better on photos and screenshots.

**Installing.** Open **Models**, then **OCR models**, choose **Tesseract language**, **EasyOCR**, or **docTR**, type a name, and click **Install**.

| Choice | Type | Examples |
|--------|------|----------|
| Tesseract language | A language code from [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast); several at once with `+`; `:best` for the larger, more accurate model from tessdata_best. Full names such as `latin` work too. | `eng`, `eng+lat`, `grc`, `eng:best` |
| EasyOCR | Language codes separated by commas. | `en`, `en,fr,de`, `ch_sim,en` |
| docTR | A detector and a recognizer joined with `+`; leave one out for docTR's default. | `fast_base+crnn_vgg16_bn`, `db_resnet50+parseq` |

Tesseract languages are downloaded from GitHub (checked against GitHub's file hashes when its API answers) into `Axis/models/ocr/tessdata`. Languages that came with Tesseract are listed too; Axis does not delete those.

**Tesseract itself.** Axis uses a Tesseract it finds (the path under **Engine settings**, `AXIS_TESSERACT`, your PATH, or where its installer puts it). **Install Tesseract** on the OCR models page installs it:

| System | How |
|--------|-----|
| Windows | The installer published on [Tesseract's GitHub releases](https://github.com/tesseract-ocr/tesseract/releases), checked against its SHA-256 and run silently. Windows may ask for permission. |
| macOS | `brew install tesseract` (needs [Homebrew](https://brew.sh)) |
| Linux | Your package manager (`apt-get`, `dnf`, `pacman`, `zypper`, or `apk`); your desktop asks for your password. Without a desktop, Axis shows the command to run. |

**The PyTorch environment.** EasyOCR and docTR need PyTorch, so Axis makes a separate Python environment for them in `Axis/engines/ocr-python` (Axis itself still uses only the standard library). It is installed with your first EasyOCR or docTR model, or with **Install PyTorch** on the OCR models page. By default it installs the CPU-only build of PyTorch (about 1 GB); choose **CUDA (NVIDIA GPU)** in the menu next to **Install** for the GPU build (several GB). EasyOCR and docTR are then installed into it as models need them. Under **Engine settings** you can choose the Python the environment is made from, the device (graphics card when there is one, or CPU only), and the PyTorch package index pip uses.

**Start and Stop.** A PyTorch OCR model that is on stays in memory, so each picture is read at once; only one is on at a time. A model that is off is loaded for each picture, which takes longer but uses no memory in between. Tesseract runs for each picture and needs no starting.

**OCR tab.** Drop, paste, upload, or choose a picture from Workspace, choose the model, and click **Read text**. For Tesseract, combine installed languages with `+` (`eng+lat`) and choose a page layout (automatic, one column, one block, one line, or scattered text). The result shows the word count, the mean confidence, and the time taken. Pictures you upload or paste are saved in `shared/uploads`; **Save to Workspace** saves the text in `shared/ocr`.

## Art Craft

Open **Tools** in the sidebar, then **Art Craft**, to install and open the Craft App Suite: free, open-source creative apps from the ArtCraft team (PhotoCraft, LightCraft, FilmCraft, DesignCraft, VectorCraft, PdfCraft, EffectCraft, WordCraft, DeckCraft, GridCraft, CADCraft, SoundCraft and FigCraft).

- **Install** on an app downloads its portable build from the app's latest GitHub release, checks it against the checksum the release publishes, and unpacks it into `Axis/engines/artcraft`. Nothing is installed on your computer and no administrator rights are needed. The button shows progress and has **Cancel**. When it finishes, the button becomes **Open**.
- **Open** starts the app. It keeps running if you quit Axis.
- **Install Rust** installs a portable Rust (with rustup) into `Axis/engines/rust`. Axis never changes your PATH. An app opened from this page finds this Rust on its PATH. The ready-made apps do not need Rust to run. On Windows, Axis downloads Microsoft's C++ Build Tools when a Rust build needs the MSVC linker.
- The portable build is the portable zip on Windows, the release tarball on Linux, and the app copied out of the release disk image on macOS (never into Applications). Where each app keeps its settings is up to the app.
- When the tab opens, Axis checks GitHub (and rustup, for Rust) once for newer versions. A card then says **Update available** and shows **Update to vX.Y.Z**; otherwise it says up to date. **Check for updates** checks again. An update installs the new version beside the old one, copies across any settings folder kept beside the old program, and removes the old version only after the new one is ready. Rust is updated with `rustup update stable`.
- Agents can drive an installed app through its command line with the matching skill under **Art Craft apps** in its settings (see [Skills](#skills)).
- **Delete** removes the app (every version, and any settings kept in its folder) or Rust, after asking. Installing over an installed app is never done, so settings are safe.

## Workspace

Open **Workspace** in the sidebar to browse, change, and run what agents have made. It is a light editor: a file list, an editor, a terminal underneath, and a chat with an agent beside it. It has no debugger, language support, or extensions.

**Layout.** The top bar has a **Folder** menu and two switches, **Terminal** and **Agent**, that show or hide the bottom panel and the agent chat. Under the bar, the folder path sits on the left and **New file**, **New folder**, **Upload**, and **More** on the right. Which panels are open, their size, and the chosen agent are remembered in this browser.

**The workspace folder.** Agents and the Workspace work in one folder, `Axis/shared` by default; its path is shown under **Workspace**. **Folder** > **Choose another folder…** opens a chooser where you can open any folder on the computer (or type its path) and click **Use this folder**. **Use the default folder** goes back to `Axis/shared`. The top of a drive and Axis's own data folder cannot be chosen. If the folder you chose is later moved or deleted, Axis uses the default and shows a note.

**Files**
- Navigate folders on the left and open files on the right.
- Text and code display as-is; Markdown renders formatted (with **Show source**); CSV displays as a table; images and PDFs display inline; `.html` files display as source with **Preview page** for a rendered view. Previews run in a sealed frame that cannot reach Axis or your other data. Clicking a file never runs it.
- **New file** adds a name box at the top of the list. Type a name and press **Enter** (Esc cancels). The empty file opens in the editor, ready to type in. **New folder** works the same way and opens the new folder. A name that is already taken is refused; nothing is ever replaced.
- **Rename** a file or folder with the pencil that appears on its row, with **F2** while the row has focus, or with **Rename** above an open file. Type the new name and press **Enter**. The name is a plain name, not a path; it stays in the same folder, and a taken name is refused. An open file follows its new name. Axis asks first if the renamed file has unsaved changes.
- **Edit** opens any text file (`.txt`, `.py`, `.html`, `.md`, `.csv`, `.json`, and so on) in an editor. **Save** (or Ctrl+S) replaces the file; Tab indents (press Esc first to move on with Tab). Axis asks before discarding unsaved changes. Files larger than 512 KB can be downloaded but not edited here.
- **Upload** (or drag and drop) copies files into the current folder, up to 512 MB each. Existing files are never replaced, even when two uploads of the same name arrive at once; duplicates are saved as `data (2).csv`. On Windows, names that Windows reserves (such as `nul` or `con`) are refused. Files dropped from your computer onto a folder in the list are uploaded into that folder.
- **Move by dragging.** Press and hold a file or folder in the list, drag it onto a folder, and let go to move it there. Drop it on **Up one folder**, or on a part of the folder path above the list, to move it upward. Moves stay inside the same agent's folder, never replace a file of the same name, and cannot put a folder inside itself.
- **Download**, **Show in folder**, **Copy text**, and **Delete** are on each open file. **More** holds **Refresh**, **Show in folder**, **Open a terminal here**, and, inside a folder, **Delete this folder**. The list refreshes automatically.

**The bottom panel (terminal and output).** Click **Terminal** (or the arrow at the right end of the panel) to show or hide it. Drag its top edge, or focus it and press the up and down arrow keys, to change its height. Showing it for the first time opens a terminal in the folder you are looking at. Each terminal is a tab (**×** closes it), **New terminal** opens another in the current folder, and the terminal button on a folder row opens one in that folder. See [Terminal](#terminal) for typing. **Run** on a `.py`, `.rs`, `.sh`, `.bash`, `.ps1`, `.bat`, or `.cmd` file runs it and shows its output (printed text, errors, and exit code) in an **Output** tab of the same panel; **Run again** repeats it. Rust files are compiled first. These runs stop after one minute and use your permissions; they are not a security sandbox. While editing, **Save and run** does both. Closing the panel does not stop terminals.

**Agent chat.** **Agent** opens a chat on the right. Choose the agent at the top; it is the agent's ordinary chat, so it also appears in **Past chats** and on the agent's own page (**Full chat**). With each message the agent is shown the folder you are in (its name, and the names, sizes, and item counts of what it contains), which file is open, and, only while the terminal is showing in the bottom panel, your newest commands in the open terminal with their output (see **Terminal commands** under [Terminal](#terminal)). It never sees what is inside your files unless it reads them with its own skills, and it can never type into the terminal. If the chat keeps the agent in a folder of its own, the agent is told it cannot reach the Workspace folder itself.

## Terminal

Open **Terminal** in the sidebar for a full-page terminal, or use the **Terminal** switch in Workspace for one under your files. Both are the same terminals.

- Each terminal is a shell (bash, or `sh` where bash is missing; `cmd` on Windows) started in the chosen folder. Agent terminal actions can also select PowerShell (`powershell` or `pwsh`) and cmd on Windows. Several terminals can be open at once; they appear as tabs, and **×** closes one. Terminals keep running while you use other pages and close when Axis stops.
- **Type directly in the terminal**, as in any terminal: your text appears after the prompt with a cursor, and **Enter** runs the line. Left and Right move the cursor (with Ctrl, a word at a time), Home and End (or Ctrl+A and Ctrl+E) go to the ends, Backspace and Delete edit, Ctrl+U and Ctrl+K cut to the start or end, Ctrl+W deletes the word before the cursor, Up and Down recall earlier commands, and Page Up and Page Down scroll the output. What you type to a running program, such as an answer to a question, stays visible. Tab completion is not available.
- **Ctrl+C** copies when text is selected; otherwise it discards the line you are typing and stops the running command (macOS and Linux). **Stop command** does the same. **Clear** (or Ctrl+L) clears the output shown.
- **Paste** puts text on the line. A pasted line is not run until you press Enter. Pasting several lines asks first, since each would run at once.
- A terminal you type into runs with your own permissions and is not confined to any folder. It is connected through pipes rather than a full terminal, so commands, scripts, and line-based programs work, but full-screen programs (editors such as `vim`, pagers, `top`) do not.
- In the Terminal tab, **Show agent** opens a panel on the right. Choose an agent and ask it about the terminal. **Terminal commands** sets how many of your newest commands it is shown with each message, each with what it printed: Off, 1, 3, 5 (the default), 10, 25 or 100. A command is any line you type and run, including answers you type to a running program. At most about 16,000 characters are shown; when there are more, the oldest part is left out. The choice is remembered in this browser and also applies to the Workspace's agent panel. The agent can read the terminal but never type into it. The conversation is the agent's ordinary chat, so it also appears in **Past chats** and on the agent's own page (**Full chat**).

## Models tab

Open **Models** to install, start, stop, and delete models. While the page is open, its four sections are listed under **Models** in the sidebar: **Chat models** (Ollama and llama.cpp), **Image models**, **OCR models** (see [OCR](#ocr)), and **Engine settings** (every engine with **Install** or **Update**, plus the settings for llama.cpp, stable-diffusion.cpp, and OCR). Each section shows its own installs under **Activity**.

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

## Settings

**Settings**, at the top right of every page next to **Quit Axis**, chooses the appearance: **System default** follows your computer's light or dark mode and changes with it, while **Light** and **Dark** keep that look regardless. The choice is remembered in this browser.

## CAS

Open **Tools** in the sidebar, then **CAS**, to set up a computer algebra system: a program that does exact math with symbols (solving equations, simplifying, derivatives, integrals, series, matrices) instead of rounded numbers. Agents use it with the **CAS** skill.

- **Maxima** is a complete computer algebra system. **Install Maxima** installs it on your computer: with winget on Windows (which checks the installer before it runs; Windows may ask for permission), with Homebrew on macOS, and with your package manager on Linux (your system asks for your password). Where Axis cannot install it, the card says how to do it yourself. Axis finds an installed Maxima on your PATH or where its installer puts it, or at the **Maxima path** you give. Because Maxima is installed like any other program, Axis does not delete it; remove it the way you remove other programs.
- **SymPy** is a computer algebra library for Python. **Install SymPy** makes a Python environment of its own in `Axis/engines/cas-python` and installs SymPy into it with pip. No administrator rights are needed. **Delete** removes that environment.
- **CAS agents use** chooses which one agents use when both are installed. **Automatic** uses Maxima if it is installed, otherwise SymPy.
- **Try it** runs code in the workspace folder and shows the result. Maxima code is a list of commands, each ending in `;` (show the result) or `$` (hide it). SymPy code is a Python program that prints its results.
- Maxima sometimes stops to ask a question, such as whether a number is positive. Nobody can answer it here, so Axis stops Maxima and shows the question; tell Maxima the answer first, for example `assume(n > 0)`, and run the code again.
- A plot saved with a file name, such as `plot2d(sin(x), [x, 0, 6], [png_file, "plot.png"])`, is put in the folder the code ran in. If a file of that name is already there, the new one is called `plot-2.png`; nothing is replaced.
- Each run may take up to 60 seconds (`AXIS_CAS_TIMEOUT`).

## Where Axis keeps your data

Everything Axis stores is in one folder on your computer. Nothing is sent anywhere.

```
Axis/                       in your home folder (override with AXIS_HOME)
├── agents/
│   └── <name>/
│       ├── agent.json      the agent's settings
├── chats/                  saved conversations, one .json file each
├── engine-settings.json    llama-server path, context size, extra arguments
├── cas-settings.json       the Maxima path and the CAS agents use
├── engines/                llama.cpp and stable-diffusion.cpp, as Axis installed them, ocr-python/ (PyTorch for OCR),
│                           artcraft/ (the Craft apps), rust/ (portable Rust) and cas-python/ (SymPy)
├── image-models/           image model definitions, one .json file each
├── image-settings.json     sd-cli and sd-server paths, working folder, default image model
├── images/                 every picture Axis makes (Images page and agents)
├── ocr-settings.json       Tesseract path, Python and device for PyTorch OCR models, PyTorch package index
├── models/
│   ├── llm/                GGUF chat models for llama.cpp, one folder per Hugging Face repository
│   ├── image/              Stable Diffusion files Axis downloaded
│   └── ocr/                OCR models: tessdata/ (Tesseract languages), easyocr/ and doctr/ (PyTorch weights)
├── run/                    programs Axis started (to recover them after a crash)
├── shared/                 the default workspace folder, where agents work
├── skills/                 your custom skills, one .json file each
│   └── built-in/           your edits to built-in skills, one .json file each
└── workspace-settings.json the workspace folder you chose, if not the default
```
