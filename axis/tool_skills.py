"""One built-in skill for each page under Tools that is not a Craft app: CAS, Images and OCR.

Each skill gives an agent the action that uses what the user set up on that page:
run_cas (Maxima or SymPy, from the CAS page), generate_image (an image model from the
Models page, as on the Images page) and read_text (an OCR model, as on the OCR page).
The Craft apps have their own skills (see craft_skills.py).
"""

from __future__ import annotations

from typing import Any, Dict, List

GROUP = "Tools"

_SKILLS: List[Dict[str, Any]] = [
    {
        "id": "cas",
        "name": "CAS",
        "description": "Do exact symbolic math (algebra, calculus, equations, matrices) with Maxima or SymPy, "
        "installed on the CAS page.",
        "instructions": "Use run_cas for exact symbolic math: simplifying, factoring, solving equations, derivatives, "
        "integrals, limits, series, sums, matrices and number theory, and to check algebra you did yourself. Leave "
        "system out to use the CAS chosen on the CAS page; if the action says none is installed, tell the user to "
        "install Maxima or SymPy there. Maxima: end each command with ; to show its result or $ to hide it, assign "
        "with :, define functions with :=, and use % for the last result, for example "
        "`f(x) := x^3 - 2*x$ solve(f(x) = 0, x); integrate(f(x), x, 0, 2); float(%);`. Nobody can answer Maxima's "
        "questions here, so when a result depends on a sign or type, state it first with assume(n > 0) or "
        "declare(n, integer). Save a plot as a file in your folder with "
        "plot2d(sin(x), [x, 0, 6], [png_file, \"plot.png\"]). SymPy: send a complete Python program and print the "
        "results, for example `from sympy import *` then `x = symbols('x')` then `print(integrate(sin(x)**2, x))`. "
        "Report exact results, and add a decimal value (float() in Maxima, N() in SymPy) when it helps.",
        "tools": ["run_cas"],
    },
    {
        "id": "images",
        "name": "Images",
        "description": "Make pictures from a text description with a local Stable Diffusion model, as on the Images page.",
        "instructions": "Use generate_image when the user asks for a picture. Write a detailed prompt: the subject, "
        "the setting, the style, the lighting, the colours and the composition; put what to keep out in "
        "negative_prompt. Leave width, height, steps and model out unless the user asks for them, so the model's "
        "own defaults are used. The picture is saved in Axis's images folder, not in your folder, and the user sees "
        "it in the chat; say in a sentence what you made. If the action says no image model is set up, tell the "
        "user to add one on the Models page (Image models).",
        "tools": ["generate_image"],
    },
    {
        "id": "ocr",
        "name": "OCR",
        "description": "Read the text in pictures in the agent's folder with a local OCR model, as on the OCR page.",
        "instructions": "Use read_text to read the text in a picture (PNG, JPEG, BMP, TIFF or WebP) in your folder. "
        "Leave model out to use a sensible installed model; name one (for example tesseract:eng) only when the "
        "user asks, or when the text is in another language and that model is installed. For Tesseract, "
        "languages takes installed languages joined with +, for example eng+lat. OCR makes mistakes, especially "
        "with handwriting, small print and unusual fonts: say so when the confidence is low, and do not invent "
        "words that were not read. Other files must be turned into a picture first. If the action says no OCR "
        "model is installed, tell the user to install one on the Models page (OCR models).",
        "tools": ["read_text"],
    },
]


def build() -> List[Dict[str, Any]]:
    """The skill definitions, in the form skills.BUILTIN holds."""
    return [{**s, "group": GROUP, "tools": list(s["tools"])} for s in _SKILLS]
