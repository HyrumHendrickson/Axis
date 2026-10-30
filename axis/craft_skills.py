"""One built-in skill for each Craft app (PhotoCraft, LightCraft, and the other eleven).

Each skill teaches an agent to drive one app through its headless program, <app>-cli, with the
Run terminal action. The programs come with the apps that Axis installs on the Art Craft page,
and Axis puts their folders on the PATH of programs an agent starts (see tools._run_env), so an
agent runs them by name. The Craft apps also offer an MCP server and a JSON control channel; Axis
has no MCP client, so the skills use the command line only.

The commands here were written from each app's own documentation and checked by running the
released Linux programs. Each app has hundreds of commands, so every skill teaches how to look a
command up (commands, describe) instead of listing them, and how a command's parameters are
written. Edit the text of a skill on the Skills page if a newer release changes it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

GROUP = "Art Craft apps"
TOOLS = ["run_terminal", "run_terminal_file"]

# What every skill says first. {cli} is the name of the program. Quoting advice is added by adapt() for the
# shell this computer uses; the examples below are written for bash.
_START = (
    "Run {cli} with run_terminal; it is already on the PATH when the app is installed on the Art Craft page. "
    "If the shell cannot find it, say so and tell the user to install the app there. Use relative paths in your "
    "working folder. Never overwrite an original file: write results to new names, check them, and tell the user "
    "which files you made. "
)
_QUOTE_POSIX = "Quote JSON for the shell with single quotes, as in the examples. "
_QUOTE_CMD = ("The examples below are written for bash. In the default Windows shell (cmd) use double quotes "
              "around JSON and write each inner quote as \\\", or set shell to powershell or pwsh and keep the single quotes. ")

# (id, app name, what it is for, instructions after the common start)
_APPS: List[Tuple[str, str, str, str]] = [
    ("photocraft", "PhotoCraft",
     "Edit images, layers and PSD files with the PhotoCraft command line.",
     "PhotoCraft edits images. It has about 945 commands, so never guess an id or a parameter: "
     "`photocraft-cli commands --filter blur` lists matching ids, and `commands --json --filter ID` shows the "
     "parameters. Edit with `photocraft-cli run in.psd --cmd ID --params '{...}' --cmd ID2 --out out.png`. "
     "--params belongs to the --cmd before it, and commands run in order. Use --new instead of a file to start "
     "blank: --new '{\"width\":800,\"height\":600,\"background\":\"#336699\"}'. Example: "
     "`photocraft-cli run --new '{\"width\":400,\"height\":300}' --cmd edit.fill --params "
     "'{\"contents\":\"color\",\"color\":\"#ffcc00\"}' --cmd filter.blur.gaussianBlur --params '{\"radius\":4}' --out a.png`. "
     "The output extension picks the format (png, jpg, webp, tif, psd, pcraft); keep layers by saving .psd or "
     ".pcraft. Other subcommands: `info FILE --compact` (size, mode, layer tree as JSON), "
     "`convert IN OUT [--quality 1-100]`, and `batch --actions actions.json --in DIR --out DIR` to apply "
     "one action list to every image in a folder. An --out that is the input is refused; do not work around it."),
    ("lightcraft", "LightCraft",
     "Develop raw and photo files and manage a photo library with the LightCraft command line.",
     "LightCraft develops photos non-destructively. For one file use "
     "`lightcraft-cli render IN -o OUT --set light.exposure=0.5 --size 1600` (the extension picks jpg, png, tif, "
     "webp, avif, jxl, psd or dng; --set is repeatable; --preset ID applies a preset; --quality 1-100). "
     "For several steps use `lightcraft-cli run --import photo.jpg develop.set control=light.exposure value=0.7 "
     "develop.auto app.export path=out.jpg longEdge=1600`: a word without = starts the next command, key=value "
     "pairs are its parameters (values are JSON when they parse), and one JSON line is printed per command. "
     "`lightcraft-cli commands` lists the ids with their parameters (about 750; search the output, for example "
     "with grep or findstr); run `develop.controls` to list every slider id with its range. Nothing is kept "
     "between runs unless you give --library DIR (open or create a library). `merge hdr|panorama|hdr-panorama FILES` "
     "makes a merged DNG next to the first file. Warnings about the GPU are normal; it renders on the CPU."),
    ("filmcraft", "FilmCraft",
     "Edit video projects, colour and sound, and export movies, with the FilmCraft command line.",
     "FilmCraft edits video in projects (.fcproj). Each call is a separate session, so keep state in a project "
     "file: put the options first, `filmcraft-cli --project p.fcproj --save SUBCOMMAND`; make a new project with "
     "`filmcraft-cli --save-as p.fcproj import clip.mp4 music.wav`. Subcommands: `inspect [project|sequence]` "
     "(JSON with ids), `import FILE...`, `exec ID key=value ...` or `exec ID '{json}'` (one command; values are "
     "JSON when they parse), `run script.jsonl` (one {\"id\",\"params\"} per line), `commands [filter]` and "
     "`describe ID` (about 710 commands; look up ids and parameters, never guess), `probe MEDIA`, "
     "`render --seconds 5 --out frame.png`, and `export OUT [--preset NAME | --format h264|hevc|prores|gif|wav|...] "
     "[--scale 0.5] [--range entire|inOut]` (`export --list-presets` lists presets). A usual flow: import, "
     "`exec file.newSequence name=Main`, then `exec clip.automateToSequence '{\"items\":[1,2],\"method\":\"insert\"}'` "
     "with the item ids from inspect, check with `inspect sequence`, render a frame to look at, then export. "
     "Times are in ticks (254016000000 per second); most commands also accept seconds=, frame= or timecode=. "
     "Exit status 1 means a command failed or the export drew offline (missing) media; read the message."),
    ("designcraft", "DesignCraft",
     "Lay out pages, then export PDF, PNG, IDML, EPUB or HTML, with the DesignCraft command line.",
     "DesignCraft is a page layout app. Every action is a command. Look commands up: "
     "`designcraft-cli commands footnote` lists ids matching a word, and `designcraft-cli describe frame.create` "
     "shows the parameters. Run commands with `designcraft-cli run [--in FILE | --sample] --cmd 'ID={json}' ... "
     "--export OUT`. --export writes the type its extension names (.png or .jpg for a page, .pdf, .designcraft, "
     ".idml, .epub, .html, .rtf, .txt); --page N, --scale S and --pdf-options JSON apply to the --export after "
     "them; `--all-pages DIR` writes every page. Example: `designcraft-cli run --cmd 'frame.create="
     "{\"rect\":[72,72,540,300],\"content\":\"text\",\"text\":\"Results\"}' --export page.pdf`. For many steps "
     "where a later step uses an earlier result, write a script file with one `command.id {json}` per line and "
     "run `designcraft-cli script page.dcs --save out.designcraft --export out.pdf`; inside it \"$0.story\" is "
     "the `story` value in step 0's result. Rects are [x0,y0,x1,y1] in points. `validate-pdf FILE.pdf` checks "
     "PDF/X-4. Most commands ignore parameters they do not know, so check names with describe."),
    ("vectorcraft", "VectorCraft",
     "Draw vector art and export SVG, PDF, AI, EPS or PNG with the VectorCraft command line.",
     "VectorCraft is a vector illustration app. Run commands with `vectorcraft-cli run [--in FILE] --cmd ID "
     "--params '{json}' ... --export OUT`; --params belongs to the --cmd before it, and each --export writes the "
     "format its extension picks (svg, svgz, pdf, ai, eps, png, jpg, gif, webp, tif, psd, dxf, emf, wmf; --scale N "
     "sizes raster output). Example: `vectorcraft-cli run --cmd shape.rectangle --params "
     "'{\"x\":10,\"y\":10,\"width\":200,\"height\":100}' --export a.svg --export a.png`. For many steps give "
     "`--commands steps.json` (a JSON array of {\"command\":ID,\"params\":{...}}). `vectorcraft-cli commands` "
     "prints about 820 commands as long JSON: save it with `vectorcraft-cli commands > commands.json` and search "
     "the file for an id or word (grep -A8 or findstr) instead of printing it; never guess an id or a parameter. "
     "Other subcommands: `convert IN OUT [--artboard I | --range 1-3] [--outline-text]` between any readable and "
     "writable format, and `info FILE` (artboards, object counts, fonts, import warnings). Read the warnings in "
     "each result; they say what an export left out."),
    ("pdfcraft", "PdfCraft",
     "Read, edit, combine, split, OCR and check PDF files with the PdfCraft command line.",
     "PdfCraft works on PDF files. Each subcommand is one job: `pdfcraft-cli info FILE.pdf` (JSON summary), "
     "`text FILE.pdf [--page N]` (extracted text), `render FILE.pdf --page 1 --dpi 96 --out page.png` (look at a "
     "page), `edit IN.pdf --out OUT.pdf --rotate 1,3:90 --delete 2,4 --move 5:1 --insert-blank 1 --title T` "
     "(options run in order and later page numbers follow the earlier changes), `combine A.pdf B.pdf --out ALL.pdf`, "
     "`extract IN.pdf --pages 1,3,5-8 --out OUT.pdf`, `split IN.pdf --every 10 --out-dir parts` or `--before 3,7`, "
     "`audit FILE.pdf` (finds faux redactions: text still under black boxes), `check FILES_OR_DIRS` (opens each "
     "file and reports which ones fail), and `batch DIR --steps convert,ocr,optimize --out-dir OUT` (steps run "
     "in that order; add --recursive, --language en, --dpi 300). Use --password PW only when the user gives one. "
     "`pdfcraft-cli tools` lists every automation tool with its JSON schema, and "
     "`pdfcraft-cli run TOOL key=value` runs one (add --root DIR to confine it to a folder). Page numbers start at 1. "
     "Scanned PDFs have no text until OCR has run."),
    ("effectcraft", "EffectCraft",
     "Build motion graphics and visual effects projects with the EffectCraft command line.",
     "EffectCraft makes motion graphics in projects (.ecproj) with compositions, layers and effects. Choose the "
     "project first: `effectcraft-cli --project p.ecproj [--save] ...`, `--empty` or `--demo` for a throwaway one, and "
     "`--save-as p.ecproj` to write a new file. Subcommands: `info` (items, active composition), `commands --filter "
     "TEXT` (about 735 commands; never guess an id), `exec ID --params '{json}'`, `run ID '{json}' ID2 '{json}'` for "
     "several in order, `props COMP LAYER --flat`, `get COMP LAYER PATH`, `set COMP LAYER PATH VALUE [--expression E]` "
     "(COMP is an id or name, - is the active one; LAYER is an id, #n or a name), `render-frame --comp C --time 1 "
     "--max-side 640 --out f.png` (look at a frame), and `render --comp C --out out.mp4 [--format h264|hevc|av1|prores|"
     "webm|png|gif|wav] [--start S --end S] [--resolution half]`. Example: `effectcraft-cli --empty --save-as p.ecproj "
     "exec comp.new --params '{\"name\":\"Main\",\"width\":1280,\"height\":720,\"duration\":5}'`. "
     "`script FILE.jsx` runs JavaScript with an After Effects-style object model (app.project, layers, properties). "
     "Time is in seconds. Render a frame and look at it before a long render."),
    ("wordcraft", "WordCraft",
     "Write, edit and convert documents (docx, odt, pdf, md) with the WordCraft command line.",
     "WordCraft is a word processor. Quick jobs: `wordcraft-cli text FILE` (plain text), `info FILE` (pages, words, "
     "properties), `inspect FILE` (structure as JSON), `render FILE out.png --page 1` (look at a page), and "
     "`convert IN OUT` between docx, doc (read only), pdf, odt, rtf, html, md, tex, txt and more; --markup shows "
     "comments and tracked changes in pdf, png and html. To edit, run commands on a document: `wordcraft-cli run "
     "--file in.docx --cmd 'ID={json}' --cmd ID2 --save out.docx`, or start new with no --file (or --template T). "
     "Only one --save per run; use convert for other formats. Examples: `--cmd 'text.insert={\"text\":\"Hello\"}'`, "
     "`--cmd para.heading1`, `--cmd caret.docEnd`. Text is typed at the caret; `caret.docEnd` moves it to the end. "
     "`wordcraft-cli commands` lists about 650 ids (search the output; never guess an id or a parameter). "
     "--author NAME names who made tracked changes and comments. A password-protected file needs --password-file "
     "PATH or the WORDCRAFT_PASSWORD variable, which is safer than --password. "
     "Set WORDCRAFT_REGION=en-US for Letter paper."),
    ("deckcraft", "DeckCraft",
     "Build presentations (deckcraft, pptx) and export slides with the DeckCraft command line.",
     "DeckCraft makes slide decks. Build with `deckcraft-cli run [--in FILE] --cmd 'ID={json}' ... --save OUT`: "
     "without --in it starts a new deck; --save writes .deckcraft (keeps everything), .pptx, .pdf or .md (outline). "
     "Example: `deckcraft-cli run --cmd 'slide.new={\"layout\":\"titleOnly\",\"title\":\"Q3 results\"}' --cmd "
     "'shape.insert={\"preset\":\"roundRect\",\"rect\":[80,160,320,120],\"text\":\"Revenue\"}' --save q3.deckcraft`. "
     "Slides are 960x540 points, x to the right and y down, rect is [x,y,width,height]; slide indices start at 0 "
     "and slide.new makes the new slide current (`slide.go={\"index\":N}` changes it). Adding a shape selects it, "
     "and format.* commands act on the selection. `deckcraft-cli commands` lists ids and `describe ID` explains one "
     "(never guess); `help run` has more rules. To see the result: `render FILE --slide 2 out.png` or `render FILE "
     "--all outdir`. Also `info FILE` (slides, layouts, theme as JSON) and `convert IN OUT` (the extension picks the "
     "format). Look at rendered slides for text that overflows before you finish."),
    ("gridcraft", "GridCraft",
     "Work with spreadsheets (xlsx, csv) and formulas with the GridCraft command line.",
     "GridCraft is a spreadsheet. Read: `gridcraft-cli info FILE` (sheets, used ranges, tables, charts), "
     "`cat FILE --range A1:D20 [--sheet S] [--formulas] [--csv]`, `eval '=SUM(1,2,3)' [--in FILE]` (calculate a "
     "formula), `functions --search NAME`. Convert: `convert IN OUT` (xlsx, csv, tsv, json, html, pdf by extension; "
     "--sheet NAME). Edit: `gridcraft-cli run --in FILE --cmd 'ID={json}' ... --out NEW.xlsx --print A1:C5`, for "
     "example `--cmd 'cell.set={\"cell\":\"C1\",\"value\":\"=A2+B2\"}'`. Without --in it starts a blank sheet. "
     "`gridcraft-cli commands --search cell` lists matching ids with parameters (about 355; never guess one). "
     "Values starting with = are formulas. After editing, print the range you changed and confirm the numbers "
     "before you report them. `check FILE` compares cached values with a recalculation and reports differences."),
    ("cadcraft", "CADCraft",
     "Draw and convert CAD drawings (dxf, dwg, svg, pdf) with the CADCraft command line.",
     "CADCraft is a CAD drawing app. Run commands with `cadcraft-cli run [FILE...] [--metric] --cmd 'ID {json}' ... "
     "--save OUT.dxf --export OUT.svg`; note the id and its JSON are separated by a space here. FILE may be a dxf, "
     "dwg or svg (the last one given is current); with none it starts a new drawing (--metric starts a metric drawing, "
     "--sample opens a sample). --save picks the format by extension (dxf, dwg, dwt, svg, png, pdf), and --export "
     "writes png, svg or pdf of the current view. Example: `cadcraft-cli run --metric --cmd 'line "
     "{\"points\":[[0,0],[100,0],[100,50]],\"closed\":true}' --cmd 'circle {\"center\":[50,25],\"radius\":10}' "
     "--save part.dxf --export part.svg`. `--script-file F.scr` and `--script TEXT` run AutoCAD-style command "
     "scripts. `cadcraft-cli commands` prints the 444-command catalogue as JSON (ids, aliases, parameters): save it "
     "to a file and search it; never guess an id. `info FILE` prints entity counts, layers and extents as JSON, "
     "and `convert IN OUT` converts between formats. Coordinates are in drawing units."),
    ("soundcraft", "SoundCraft",
     "Record, edit, mix and convert audio (wav, flac, aiff) with the SoundCraft command line.",
     "SoundCraft is an audio editor. For one file: `soundcraft-cli info FILE` (format, length, rate as JSON) and "
     "`soundcraft-cli convert IN OUT [--rate 44100] [--bit-depth 16|24|32f] [--normalize]` (reads wav, aiff, flac, "
     "mp3, ogg, m4a and more; writes wav, bwf, aiff or flac by the extension). For editing and mixing, work in a "
     "session: `soundcraft-cli run [--in S.scraft | --demo] --cmd 'ID={json}' ... [--bounce mix.wav] [--save "
     "S.scraft] [--inspect --compact]`. Example: `run --cmd 'file.import_audio={\"path\":\"tone.wav\"}' --bounce "
     "mix.wav --save s.scraft`. --bounce writes the mix and reports its loudness (LUFS and peak), which is how you "
     "check the result; --start and --end limit it. `soundcraft-cli commands` lists ids and `describe ID` shows the "
     "parameters (never guess). `script FILE` runs a file with one `ID {json}` per line. Saving over the session "
     "you opened needs --in-place; do not use it unless the user asks."),
    ("figcraft", "FigCraft",
     "Design interfaces and prototypes and export PNG, SVG or PDF with the FigCraft command line.",
     "FigCraft is an interface design app. Build by running a script of commands: write a .jsonl file with one "
     "{\"command\":ID,\"params\":{...}} per line (or a JSON array), then `figcraft-cli run script.jsonl [--in "
     "file.figcraft] --out design.figcraft --no-system-fonts`. One result line is printed per step. Example lines: "
     "{\"command\":\"frame.create\",\"params\":{\"x\":0,\"y\":0,\"width\":320,\"height\":200,\"name\":\"Card\"}} "
     "and {\"command\":\"text.create\",\"params\":{\"text\":\"Hello\",\"x\":20,\"y\":20,\"props\":{\"fontSize\":24}}}. "
     "`figcraft-cli commands --filter text` lists matching ids with parameters (about 615; never guess an id or "
     "a parameter); node.create makes rectangle, ellipse, text, frame and more. Look at the work: `render FILE -o "
     "out.png [--scale 2] [--page NAME]`, and `export FILE -o OUT [--format png|jpg|svg|webp|pdf] [--node ID|NAME]` "
     "(one file per top-level frame into a folder, or one file when OUT has an image extension). `info FILE --json` "
     "lists pages, frames and missing fonts, and `convert IN OUT` reads .fig, .sketch and .svg (best effort; "
     "info says what was not imported). --no-system-fonts makes renders match on every computer. Use "
     "--automation-read-root DIR and --automation-write-root DIR to confine a run to one folder."),
]


IDS = {a[0] for a in _APPS}


def adapt(app_id: str, instructions: str) -> str:
    """The skill's text plus the facts of this computer: quoting for its shell, and whether the app is installed."""
    from . import artcraft, engines, tools
    cli = app_id + "-cli"
    try:
        shell = tools._shells_here()[0]
        installed = artcraft.cli_program(app_id) is not None
        app_in = artcraft._app_info(app_id) is not None
        mac = engines.this_platform()[0] == "macos"
    except Exception:
        return instructions
    quote = _QUOTE_CMD if shell == "cmd" else _QUOTE_POSIX
    text = instructions
    if installed:
        state = f"{cli} is installed and on the PATH."
    elif app_in and mac:
        state = (f"The app is installed but macOS downloads have no command-line program, so {cli} is not "
                 "available: tell the user, and do not try other ways to run the app.")
    elif app_in:
        state = f"The app is installed but {cli} was not found in it: tell the user to update the app on the Art Craft page."
    else:
        state = (f"{cli} is not installed on this computer: do not run it. Tell the user to install the app on the "
                 "Art Craft page (under Tools).")
    return f"{text.rstrip()} {quote}{state}"


def build() -> List[Dict[str, Any]]:
    """The skill definitions, in the form skills.BUILTIN holds."""
    skills: List[Dict[str, Any]] = []
    for app_id, name, description, body in _APPS:
        skills.append({
            "id": app_id,
            "name": name,
            "group": GROUP,
            "description": description,
            "instructions": _START.format(cli=app_id + "-cli") + body,
            "tools": list(TOOLS),
        })
    return skills
