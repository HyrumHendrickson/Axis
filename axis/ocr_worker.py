"""Runs PyTorch OCR models (EasyOCR and docTR) for Axis.

This file runs in Axis's OCR Python environment (~/Axis/engines/ocr-python), not in Axis's
own, because it needs PyTorch and the OCR packages. It uses nothing from Axis.

  python ocr_worker.py check
  python ocr_worker.py download --engine easyocr --model en,fr --dir DIR
  python ocr_worker.py run      --engine doctr --model fast_base+crnn_vgg16_bn --dir DIR --image FILE
  python ocr_worker.py serve    --engine ... --model ... --dir DIR --port N

check, download and run end with one line: "@@AXIS@@" followed by a JSON object. serve loads
the model, then answers on 127.0.0.1: GET /health, and POST /ocr {"image": path} with the
X-Axis-Token header set to the AXIS_OCR_TOKEN it was started with.
"""

import os
import sys

# Axis's own modules sit next to this file; keep them from shadowing what the OCR packages import.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402

MARK = "@@AXIS@@"


def say(data):
    sys.stdout.write(MARK + json.dumps(data) + "\n")
    sys.stdout.flush()


def device_for(choice):
    if choice == "cpu":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def lines_from_boxes(items):
    """Text in reading order from [(box, text, confidence)]: boxes on the same line, left to right."""
    words = []
    for box, text, conf in items:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        words.append((min(ys), max(ys), min(xs), str(text), float(conf)))
    words.sort(key=lambda w: ((w[0] + w[1]) / 2, w[2]))
    lines = []
    for w in words:
        cy, height = (w[0] + w[1]) / 2, w[1] - w[0]
        if lines and abs(cy - lines[-1]["cy"]) <= max(height, lines[-1]["h"]) * 0.5:
            lines[-1]["words"].append(w)
        else:
            lines.append({"cy": cy, "h": height, "words": [w]})
    return "\n".join(" ".join(x[3] for x in sorted(line["words"], key=lambda x: x[2])) for line in lines)


class EasyOCR:
    def __init__(self, model, folder, device):
        import easyocr

        self.device = device_for(device)
        langs = [x for x in model.split(",") if x]
        self.version = getattr(easyocr, "__version__", "")
        self.reader = easyocr.Reader(langs, gpu=self.device if self.device != "cpu" else False,
                                     model_storage_directory=folder,
                                     user_network_directory=os.path.join(folder, "user_network"),
                                     download_enabled=True, verbose=True)

    def read(self, data):
        items = self.reader.readtext(data, detail=1, paragraph=False)
        confs = [float(c) for _, _, c in items]
        return {"text": lines_from_boxes(items), "words": len(items),
                "confidence": round(100 * sum(confs) / len(confs), 1) if confs else None}


class DocTR:
    def __init__(self, model, folder, device):
        os.environ["DOCTR_CACHE_DIR"] = folder
        os.environ.setdefault("USE_TORCH", "1")
        import doctr
        from doctr.models import ocr_predictor

        det, _, reco = model.partition("+")
        self.version = getattr(doctr, "__version__", "")
        self.predictor = ocr_predictor(det_arch=det, reco_arch=reco, pretrained=True)
        self.device = device_for(device)
        if self.device != "cpu":
            try:
                import torch

                self.predictor = self.predictor.to(torch.device(self.device))
            except Exception:
                self.device = "cpu"

    def read(self, data):
        from doctr.io import DocumentFile

        doc = self.predictor(DocumentFile.from_images(data))
        pages, confs, count = [], [], 0
        for page in doc.export().get("pages", []):
            blocks = []
            for block in page.get("blocks", []):
                lines = []
                for line in block.get("lines", []):
                    words = line.get("words", [])
                    count += len(words)
                    confs += [float(w.get("confidence") or 0) for w in words]
                    text = " ".join(str(w.get("value", "")) for w in words).strip()
                    if text:
                        lines.append(text)
                if lines:
                    blocks.append("\n".join(lines))
            pages.append("\n\n".join(blocks))
        return {"text": "\n\n".join(p for p in pages if p), "words": count,
                "confidence": round(100 * sum(confs) / len(confs), 1) if confs else None}


ENGINES = {"easyocr": EasyOCR, "doctr": DocTR}


def load(args):
    os.makedirs(args.dir, exist_ok=True)
    return ENGINES[args.engine](args.model, args.dir, args.device)


def read_file(engine, path):
    with open(path, "rb") as f:
        data = f.read()
    started = time.time()
    out = engine.read(data)
    out.update({"ok": True, "device": engine.device, "read_seconds": round(time.time() - started, 2)})
    return out


def cmd_check(args):
    out = {"ok": True, "python": "%d.%d.%d" % sys.version_info[:3]}
    try:
        import torch

        out["torch"] = torch.__version__
        out["cuda"] = bool(torch.cuda.is_available())
        mps = getattr(torch.backends, "mps", None)
        out["mps"] = bool(mps is not None and mps.is_available())
    except Exception as exc:
        out.update({"ok": False, "error": "PyTorch cannot be loaded: %s" % exc})
    for name, module in (("easyocr", "easyocr"), ("doctr", "doctr")):
        try:
            mod = __import__(module)
            out[name] = getattr(mod, "__version__", "") or "installed"
        except ImportError:
            out[name] = None
        except Exception as exc:
            out[name] = None
            out[name + "_error"] = str(exc)
    say(out)
    return 0 if out["ok"] else 1


def cmd_download(args):
    engine = load(args)
    say({"ok": True, "version": engine.version, "device": engine.device})
    return 0


def cmd_run(args):
    engine = load(args)
    print("Reading the text", flush=True)
    say(read_file(engine, args.image))
    return 0


def cmd_serve(args):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    engine = load(args)
    token = os.environ.get("AXIS_OCR_TOKEN", "")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._json(200, {"ready": True, "engine": args.engine, "model": args.model, "device": engine.device})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/ocr":
                return self._json(404, {"error": "not found"})
            if token and self.headers.get("X-Axis-Token") != token:
                return self._json(403, {"error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                return self._json(200, read_file(engine, body["image"]))
            except Exception as exc:
                traceback.print_exc()
                return self._json(500, {"error": "The model could not read this picture: %s" % exc})

    print("Ready on port %d" % args.port, flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="ocr_worker")
    p.add_argument("mode", choices=["check", "download", "run", "serve"])
    p.add_argument("--engine", choices=sorted(ENGINES))
    p.add_argument("--model", default="")
    p.add_argument("--dir", default="")
    p.add_argument("--device", default="auto", choices=["auto", "cpu"])
    p.add_argument("--image", default="")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args(argv)
    if args.mode != "check" and not (args.engine and args.model and args.dir):
        p.error("--engine, --model and --dir are needed")
    try:
        return {"check": cmd_check, "download": cmd_download, "run": cmd_run, "serve": cmd_serve}[args.mode](args)
    except Exception as exc:
        traceback.print_exc()
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, ImportError):
            message = "A package is missing from the OCR Python environment: %s" % exc
        say({"ok": False, "error": message})
        return 1


if __name__ == "__main__":
    sys.exit(main())
