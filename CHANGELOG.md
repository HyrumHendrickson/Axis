# Changelog

All notable changes to Axis are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Axis uses [semantic versioning](https://semver.org/): a major version for changes that break existing data or habits, a minor version for new features, and a patch version for fixes and small improvements.

## [Unreleased]

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
