# Changelog

All notable changes to Axis are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Axis uses [semantic versioning](https://semver.org/): a major version for changes that break existing data or habits, a minor version for new features, and a patch version for fixes and small improvements.

## [Unreleased]

### Fixed
- The test workflow uses `actions/checkout@v6` and `actions/setup-python@v6` (Node 24), and cancels a run that a newer push replaces.
- Tests: deleting an agent whose folder, or a folder inside it, was read-only failed on Linux and macOS, and the test suite failed there. Deleting a folder in the **Files** tab now also removes read-only files and folders, and never follows a link out of the folder.
- Run Python: several chats using the same agent at the same time no longer delete each other's scratch files.
- Agents no longer receive an out-of-date answer to "list files" or "read file" after they wrote, changed, or deleted a file (the "you already did this" check now forgets earlier results when something changes). Repeating the same action over and over is still stopped.
- Picture making closes the output pipe of `sd-cli`, so its handles are released (Windows could not clean up the files).
- `axis uninstall` on Windows no longer fails if the PowerShell shortcut removal prints nothing.
- Closing a browser tab during a reply no longer prints error traces in the terminal.

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
