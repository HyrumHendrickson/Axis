# Axis

Axis is a lightweight AI agent manager that wraps Ollama models behind a simple CLI.

## Install (Linux + Windows)

```bash
pip install axis-agent-manager
```

This installs the `axis` command.

## Storage layout

Axis uses your home directory:

- `~/Axis/.Axis` → Axis state, process tracking, logs
- `~/Axis/<agent-name>/workshop` → agent workspace (tasks/results)
- `~/Axis/<agent-name>/config.json` → personality + model config
- `~/Axis/<agent-name>/tools.json` → agent tool config

## Commands

- `axis update`
- `axis uninstall`
- `axis list online`
- `axis list downloaded`
- `axis get <name>`
- `axis remove <name>`
- `axis ps`
- `axis list agents`
- `axis delete <agent>`
- `axis create <agent>`
- `axis start <agent>`
- `axis stop <agent>`
- `axis edit <agent>`

## Agent runtime

When started, an agent watches:

- `~/Axis/<agent>/workshop/tasks/*.txt`

For each task file, Axis calls Ollama with the agent's configured personality/model and writes:

- `~/Axis/<agent>/workshop/results/<task-name>.json`

## Notes

- Ensure Ollama is running at `http://127.0.0.1:11434`
- Default model in new agents: `llama3.2:3b`
