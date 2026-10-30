"""One built-in skill for each page under Tools that is not a Craft app: CAS, Images and OCR.

Each skill gives an agent the action that uses what the user set up on that page:
run_cas (Maxima or SymPy, from the CAS page), generate_image (an image model from the
Models page, as on the Images page) and read_text (an OCR model, as on the OCR page).
The Craft apps have their own skills (see craft_skills.py).

The text a user can edit on the Skills page is the general part. What depends on this computer is added
by adapt() each time a prompt is built: the CAS skill names the one system in use (an agent is shown
run_maxima or run_sympy, never both, and never run_cas), and the Images and OCR skills say when nothing is
set up. The image models, OCR models and Tesseract languages themselves are listed in the action's definition
(tools.tool_spec), so a model is told each of them once.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

GROUP = "Tools"

_SKILLS: List[Dict[str, Any]] = [
    {
        "id": "cas",
        "name": "CAS",
        "description": "Do exact symbolic math (algebra, calculus, equations, matrices) with Maxima or SymPy, "
        "installed on the CAS page.",
        "instructions": "Use run_cas for exact symbolic math (simplifying, factoring, equations, derivatives, integrals, "
        "limits, series, sums, matrices, number theory) and to check algebra you did yourself. Report exact results, "
        "and add a decimal value when it helps. If it says no computer algebra system is installed, tell the user to "
        "install Maxima or SymPy on the CAS page (under Tools).",
        "tools": ["run_cas"],
    },
    {
        "id": "images",
        "name": "Images",
        "description": "Make pictures from a text description with a local Stable Diffusion model, as on the Images page.",
        "instructions": "Use generate_image when the user asks for a picture. Write a detailed prompt (subject, setting, "
        "style, lighting, colours, composition) and put what to keep out in negative_prompt. Leave width, height, steps "
        "and model out unless the user asks, so the model's defaults are used. The picture is saved in Axis's images "
        "folder, not in your folder, and the user sees it in the chat; say in a sentence what you made. If it says no "
        "image model is set up, tell the user to add one on the Models page (Image models).",
        "tools": ["generate_image"],
    },
    {
        "id": "ocr",
        "name": "OCR",
        "description": "Read the text in pictures in the agent's folder with a local OCR model, as on the OCR page.",
        "instructions": "Use read_text to read the text in a picture (PNG, JPEG, BMP, TIFF or WebP) in your folder. "
        "Leave model out unless the user asks for one, or the text is in another language that an installed model "
        "reads (Tesseract languages are joined with +, like eng+lat). OCR makes mistakes, especially with handwriting, "
        "small print and unusual fonts: say so when the confidence is low, and do not invent words that were not read. "
        "Other files must be turned into a picture first. If it says no OCR model is installed, tell the user to "
        "install one on the Models page (OCR models).",
        "tools": ["read_text"],
    },
]


CAS_MAXIMA = (
    "The computer algebra system in use is Maxima, so your action is run_maxima (the user chose it on the CAS "
    "page). Send Maxima commands and end each with ; to show its result or $ to hide it; assign with :, define "
    "functions with :=, and use % for the last result, for example "
    "`f(x) := x^3 - 2*x$ solve(f(x) = 0, x); integrate(f(x), x, 0, 2); float(%);`. Nobody can answer Maxima's "
    "questions here, so when a result depends on a sign or type, state it first with assume(n > 0) or "
    "declare(n, integer). Save a plot as a file in your folder with "
    "plot2d(sin(x), [x, 0, 6], [png_file, \"plot.png\"]). Add a decimal value with float() when it helps."
)
CAS_SYMPY = (
    "The computer algebra system in use is SymPy, so your action is run_sympy (the user chose it on the CAS "
    "page). Send a complete Python program and print the results, for example `from sympy import *` then "
    "`x = symbols('x')` then `print(integrate(sin(x)**2, x))`. Add a decimal value with N() when it helps."
)
CAS_NONE = (
    "No computer algebra system is installed yet. If you try the action it will say so; then tell the user "
    "to install Maxima or SymPy on the CAS page (under Tools), and do not guess exact results you could not check."
)


def _adapt_cas(text: str) -> str:
    from . import tools
    name = tools.cas_tool_name()
    text = re.sub(r"\brun_cas\b", name, text)   # the action the model is shown, whatever the text says
    notes = {"run_maxima": CAS_MAXIMA, "run_sympy": CAS_SYMPY}.get(name, CAS_NONE)
    return f"{text.rstrip()} {notes}"


def _adapt_images(text: str) -> str:
    from . import images, manager
    try:
        usable = [m for m in images.list_models() if not m.get("missing")]
        engine = bool(images.find_cli() or manager.sd_server_binary())
    except Exception:
        return text
    if not usable:
        return (f"{text.rstrip()} No image model is set up right now, so generate_image cannot make pictures: "
                "tell the user to add one on the Models page (Image models).")
    if not engine:   # the models themselves are listed in the action's definition
        return (f"{text.rstrip()} stable-diffusion.cpp is not installed yet, so a picture will fail until the user "
                "installs it on the Models page (Image models).")
    return text


def _adapt_ocr(text: str) -> str:
    from . import ocr
    try:
        usable = [m for m in ocr.list_models() if m.get("usable")]
    except Exception:
        return text
    if not usable:
        return (f"{text.rstrip()} No OCR model is installed right now, so read_text cannot read pictures: tell "
                "the user to install one on the Models page (OCR models).")
    if any(m.get("engine") != "tesseract" for m in usable):   # the models and languages are in the action's definition
        return f"{text.rstrip()} The PyTorch models read harder pictures but are slower."
    return text


ADAPTERS = {"cas": _adapt_cas, "images": _adapt_images, "ocr": _adapt_ocr}


def adapt(skill_id: str, instructions: str) -> str:
    """The instructions as an agent reads them now: the skill's own text, plus what is set up on this computer."""
    fn = ADAPTERS.get(skill_id)
    return fn(instructions) if fn else instructions


def build() -> List[Dict[str, Any]]:
    """The skill definitions, in the form skills.BUILTIN holds."""
    return [{**s, "group": GROUP, "tools": list(s["tools"])} for s in _SKILLS]
