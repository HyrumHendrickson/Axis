<p align="center">
  <img src="axislogo.png" alt="Axis logo" width="160">
</p>

<h1 align="center">Axis</h1>

<p align="center">
  A lightweight local app for chatting with AI models and building your own agents.<br>
  No cloud, no accounts, no telemetry, no Python dependencies beyond the standard library.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-blue.svg">
  <img alt="Dependencies: none" src="https://img.shields.io/badge/dependencies-none-brightgreen.svg">
  <a href="https://github.com/HyrumHendrickson/Axis/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/HyrumHendrickson/Axis/actions/workflows/tests.yml/badge.svg"></a>
</p>

---

## Why Axis

- **Everything stays on your computer.** Models run locally through llama.cpp, stable-diffusion.cpp, Ollama, Tesseract, or PyTorch. Your chats, agents, and files are plain files in one folder you own.
- **No sign-up, no login, no telemetry.** Axis sends nothing about you anywhere. It goes online only to download what it needs (engines on first start, models you install), to check this repository for a newer version (turn it off with `AXIS_NO_UPDATE_CHECK=1`), and when an agent you gave the web skills searches or reads a page. The page itself answers only requests from your own computer.
- **Small and fast.** One Python package that uses only the standard library, and one HTML page with no build step, no bundler, and no JavaScript framework.
- **Open source.** MIT licensed. Read it, change it, ship it.

## Overview

Axis runs a small web app on your computer and opens it in a window of its own. It talks to a model served locally by **Ollama** or **llama.cpp**.

- **Chat** directly with a model, or with agents you design.
- **Agents** have their own system prompt, standing instructions, skills, and private folder.
- **Agents** tab lists your agents; click one to chat with it, or edit it, open its folder, or see its past chats.
- **Skills** let agents run Python, compile and run Rust, run terminal code (including PowerShell or cmd on Windows), read and write files, search the web, read Wikipedia, and generate images. Every skill, built in or your own, can be edited, shows exactly what it sends to the model, and built-in ones can be reset to default.
- **Past chats** are saved automatically and can be resumed at any time. Any message you sent can be edited and sent again.
- **Images** are generated locally with Stable Diffusion (stable-diffusion.cpp).
- **Edit a picture** on the Images tab: upload one or choose one from Workspace, describe the result, and set how much may change. It uses the image models you already have (image to image).
- **Pictures in chats**: attach, paste, or drop pictures into a chat when the model can read them (Vision on the Models page), such as `gemma3:4b`. For llama.cpp models Axis also downloads the model's vision file (mmproj).
- **OCR** tab reads the text in a picture (uploaded, pasted, or chosen from Workspace) with Tesseract or with PyTorch OCR models (EasyOCR, docTR). The text can be corrected, copied, saved to Workspace, or downloaded.
- **Workspace** tab is a light editor for every agent's folder: browse, preview, create, rename, edit, upload, download, and move files, and run Python, Rust, and terminal-language files. A collapsible terminal sits underneath, and a collapsible chat on the side lets an agent see the folder you are in (and the terminal, while it is open).
- **Terminal** is a shell on your computer, started in any agent's folder, in its own tab or in the Workspace. You type directly in it, like an ordinary terminal. An agent of your choice can see its output and help.
- **Art Craft** tab installs the portable Craft apps (PhotoCraft, WordCraft and eleven more) and a portable Rust in one click each, then opens them.
- **Models** tab installs, starts, stops, and deletes chat, image, and OCR models, and shows memory use, context size, and capabilities for each. Its sections (**Chat models**, **Image models**, **OCR models**, **Engine settings**) appear under **Models** in the sidebar while the page is open.
- **Settings** (top right) switches between light, dark, and your system's appearance.

Axis manages models itself. Type a model name such as `llama3.1:8b` or `z-image-turbo` on the **Models** page and click **Install**; every installed model then has **Start**, **Stop**, and **Delete** buttons. Axis installs llama.cpp and stable-diffusion.cpp for you the first time it starts, so nothing else is needed. Ollama is optional; Axis uses it when it is installed.

```
pipx install Axis  ->  run `axis`  ->  Models page: type a name, Install, then Start
```

## Quick start

Requirements: Python 3.9+ (3.10+ recommended), a terminal, and a browser. Full per-platform instructions are in [docs/installation.md](docs/installation.md).

```bash
# 1. Install pipx, then restart your terminal
python3 -m pip install --user pipx
python3 -m pipx ensurepath

# 2. Install Axis
pipx install git+https://github.com/HyrumHendrickson/Axis.git

#    If that fails (for example, Git is not installed), use the ZIP instead:
pipx install https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip

# 3. Run Axis
axis
```

On its first start Axis downloads llama.cpp and stable-diffusion.cpp in the background (progress is on the **Models** page). Go to **Models**, choose **Chat model, llama.cpp**, type `llama3.1:8b`, click **Install**, then **Start**. For pictures, choose **Image model**, type `z-image-turbo` (or `sd1.5` for a small one), click **Install**, then **Start**.

On Windows, replace `python3` with `py`. If `axis` is not found after step 2, see [Fixing PATH problems](docs/installation.md#fixing-path-problems).

## Documentation

| Document | What it covers |
|----------|----------------|
| [Installation](docs/installation.md) | Installing Axis on Windows, macOS, and Linux; PATH problems; the model engines |
| [User guide](docs/user-guide.md) | Every page: chat, agents, skills, images, OCR, workspace, terminal, models, settings |
| [Configuration](docs/configuration.md) | The Axis window and app, commands, options, and environment variables |
| [Troubleshooting](docs/troubleshooting.md) | Symptoms and fixes |
| [Architecture](docs/architecture.md) | How the pieces fit together, for anyone reading or changing the code |
| [HTTP API](docs/api.md) | The local JSON API the page uses |
| [Development](docs/development.md) | Setting up a clone, running the tests, and the conventions the code follows |
| [Design principles](docs/principles.md) | What Axis is, what it is not, and how changes are judged |

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md); it explains how to set up a clone, run the tests, and send a pull request. Please read the [design principles](docs/principles.md) before proposing a feature, and the [code of conduct](CODE_OF_CONDUCT.md) before taking part. Report security problems privately as described in [SECURITY.md](SECURITY.md). AI coding agents working on this repository should follow [AGENTS.md](AGENTS.md).

Changes in each version are listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
