# Installing Axis

This page covers installing Axis itself and the engines it uses to run models. For a two-minute version, see the [Quick start](../README.md#quick-start).

## Installing the axis command

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


## Model engines

Axis runs chat models with **llama.cpp** or **Ollama**, and image models with **stable-diffusion.cpp**. Axis starts and stops them as needed.

### llama.cpp and stable-diffusion.cpp: installed by Axis

pip and pipx cannot run programs while a package is installed, so Axis installs these two itself:

- **On its first start**, in the background, when they are not already on your computer. Turn this off with `axis --no-engine-install` or `AXIS_NO_ENGINE_INSTALL=1`.
- **From the Models page**, with **Install** (or **Update**) next to each engine.
- **Automatically**, as the first step of installing or starting a model that needs one.
- **From a terminal**, with `axis setup`, for example right after `pipx install`.

Axis downloads the latest release from each project's GitHub page, checks it against the published SHA-256 checksum, and keeps it in `Axis/engines` in your home folder. No administrator rights are needed and nothing is installed system-wide. By default it installs the CPU-only build, which runs everywhere (on Apple Silicon, where no CPU build is published, it uses Metal). Choosing **Best for this computer** on the Models page, or `axis setup --build auto`, picks the build for your computer instead:

| Computer | llama.cpp | stable-diffusion.cpp |
|----------|-----------|----------------------|
| Windows, NVIDIA GPU | CUDA (with its runtime) | CUDA (with its runtime) |
| Windows, other GPU | Vulkan | Vulkan |
| Windows on ARM | CPU / Vulkan | not available |
| macOS, Apple Silicon | Metal | Metal |
| macOS, Intel | CPU | not available |
| Linux x86-64 | Vulkan when `vulkaninfo` works, otherwise CPU | Vulkan when `vulkaninfo` works, otherwise CPU |
| Linux ARM64 | CPU / Vulkan | not available |

If a build does not run (for example, a missing GPU driver), Axis tries the next one. To choose a build yourself, use the menu next to **Install** on the Models page, or `axis setup --build vulkan` (`auto`, `cpu`, `vulkan`, `cuda`, `rocm`, `metal`). Where no ready-made build exists, build the program from source and set its path on the Models page.

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
