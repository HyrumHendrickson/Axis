# Axis

Axis is a lightweight CLI for managing local Ollama-based agents.

## Requirements

- Python 3.9+
- Ollama running locally at `http://127.0.0.1:11434`

## Install

### Option 1: Install from this repository

```bash
python -m pip install .
```

### Option 2: Install directly from GitHub

```bash
python -m pip install git+https://github.com/HyrumHendrickson/Axis.git
```

Verify install:

```bash
axis --help
```

If `axis` is not found, use:

```bash
python -m axis --help
```

On Windows with `--user` installs, add your user Scripts directory to PATH and open a new terminal:

```powershell
$scripts = python -c "import site; print(site.USER_BASE + r'\\Scripts')"
[Environment]::SetEnvironmentVariable('Path', $env:Path + ';' + $scripts, 'User')
```

## Quick start (MVP flow)

```bash
# create an agent
axis create demo

# list online and downloaded models
axis list online
axis list downloaded

# download a model (either command works)
axis get llama3.2:3b
axis download llama3.2:3b

# start the agent worker
axis start demo

# drop a task file in:
# ~/Axis/demo/workshop/tasks/my-task.txt

# check running processes
axis ps

# stop and cleanup
axis stop demo
axis delete demo
```

## Command summary

- `axis update`
- `axis uninstall`
- `axis list online`
- `axis list downloaded`
- `axis get <model>`
- `axis download <model>` (alias of `get`)
- `axis remove <model>`
- `axis ps`
- `axis list agents`
- `axis create <agent>`
- `axis edit <agent>`
- `axis start <agent>`
- `axis stop <agent>`
- `axis delete <agent>`

## Storage layout

Axis stores files under your home directory:

- `~/Axis/.Axis` - state files, process tracking, logs
- `~/Axis/<agent-name>/config.json` - agent name, model, personality
- `~/Axis/<agent-name>/tools.json` - tool config placeholder
- `~/Axis/<agent-name>/workshop/tasks` - incoming `.txt` tasks
- `~/Axis/<agent-name>/workshop/results` - task result `.json` files

## How task processing works

When an agent is running, the worker polls:

- `~/Axis/<agent>/workshop/tasks/*.txt`

For each task file without an existing result, Axis calls Ollama chat and writes:

- `~/Axis/<agent>/workshop/results/<task-name>.json`

## Troubleshooting

- If model commands fail, verify Ollama is running.
- If `axis create <name>` fails, use a simple folder-safe name (no `\\ / : * ? \" < > |`).
- Use `axis ps` to identify stale or running agents.
