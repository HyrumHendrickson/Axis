# Contributing to Axis

Thank you for helping. Axis is a small, local, dependency-free app, and every contribution is judged by whether it keeps it that way. Please read the [design principles](docs/principles.md) first; they explain what will and will not be accepted.

## Ways to help

- **Report a bug** with the bug report form under [Issues](https://github.com/HyrumHendrickson/Axis/issues/new/choose). Include your system, your Python version, `axis --version`, what you did, what you expected, and what happened.
- **Suggest a feature** with the feature request form. Explain the problem before the solution, and say how it fits the design principles.
- **Improve the documentation.** Unclear instructions are bugs too.
- **Send a pull request** for an open issue. For anything larger than a small fix, open an issue first so the approach can be agreed before you spend time on it.

## Setting up

You need Python 3.9 or newer and Git. Nothing else.

```bash
git clone https://github.com/HyrumHendrickson/Axis.git
cd Axis
pipx install --editable .      # the `axis` command now runs your clone
python3 -m unittest discover -s tests -v
```

On Windows use `py` instead of `python3`. The tests start fake model servers in-process, so they need no network, model, or GPU. More detail is in [docs/development.md](docs/development.md).

## Making a change

1. Create a branch from `main`.
2. Make the change. Keep it focused: one fix or feature per pull request.
3. Add or update tests in `tests/`. Bug fixes should include a test that fails without the fix.
4. Update the documentation the change affects: the user guide for anything a user sees, `docs/api.md` and the route list in `axis/server.py` for API changes, `docs/configuration.md` for new options or environment variables.
5. Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
6. Run the full test suite and make sure it passes.
7. Open a pull request and fill in the template.

## Rules that are not negotiable

- **No new runtime dependencies.** The `axis` package uses only the Python standard library.
- **No build step for the page.** `axis/web/index.html` stays one hand-written file with no framework and nothing loaded from the internet.
- **No accounts, telemetry, analytics, or cloud services.**
- **No weakening of the safety checks**: the request origin check, the path confinement in `tools.resolve`, and checksum verification of downloads.

## Style

- Python: follow the style of the surrounding code. Type hints on new functions, a docstring on each module and on any function whose purpose is not obvious, and `pathlib` for paths.
- Errors shown to users are full sentences in plain language that say what to do next.
- JavaScript: plain browser JavaScript, built with the existing `h()` and `fill()` helpers. Colours come only from the CSS variables on `:root`, so both themes keep working.
- Indentation is 4 spaces in Python and 2 in HTML, CSS, and JavaScript (see `.editorconfig`).
- Commit messages: a short imperative summary line ("Add drag and drop moves to Workspace"), then a blank line and any detail.

## Reviews

A maintainer will review your pull request, may ask for changes, and will merge it when it is ready. Reviews look at correctness, tests, documentation, and fit with the design principles. Please be patient; this is a volunteer project.

## Licence

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE), like the rest of Axis.

## Conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
