"""Downloads model files from Hugging Face (huggingface.co).

Accepted names, as typed or pasted from the website:

    bartowski/Llama-3.2-3B-Instruct-GGUF                 a repository
    bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M          a repository and a quantization (like llama-server -hf)
    Comfy-Org/z_image_turbo/split_files/vae/ae.safetensors   one file (or a folder) in a repository
    hf.co/owner/repo, huggingface.co/owner/repo           the same, with the site name
    https://huggingface.co/owner/repo[/tree|blob|resolve/<revision>/<path>]   a link copied from the site

Downloads are written to "<file>.part" first and renamed only after the size and
SHA-256 checksum match what Hugging Face reports, so a half-finished file is never
mistaken for a model. An interrupted download resumes where it stopped.

Gated and private repositories need a Hugging Face access token, taken from
HF_TOKEN (or HUGGING_FACE_HUB_TOKEN), or from the token file that
`hf auth login` writes.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__

CHUNK = 1024 * 1024
RETRIES = 6
READ_TIMEOUT = 60.0
DISK_MARGIN = 512 * 1024 * 1024   # keep this much free space after a download
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
IMAGE_EXTS = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".sft")


class HubError(Exception):
    """A problem with a Hugging Face name or download that the user can fix."""

    user_facing = True


class Cancelled(HubError):
    pass


class NeedChoice(HubError):
    """Several files match; the user must pick one."""

    def __init__(self, message: str, options: List[Dict[str, Any]]):
        super().__init__(message)
        self.options = options


@dataclass
class Ref:
    repo: str
    revision: str = "main"
    path: str = ""     # a file or folder inside the repository ("" for the whole repository)
    tag: str = ""      # a quantization such as Q4_K_M

    @property
    def text(self) -> str:
        out = self.repo + (f"/{self.path}" if self.path else "")
        return out + (f":{self.tag}" if self.tag else "")


def endpoint() -> str:
    return (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")


def token() -> str:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(key, "").strip():
            return os.environ[key].strip()
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    for path in (os.environ.get("HF_TOKEN_PATH"), str(Path(home) / "token")):
        if path:
            try:
                value = Path(path).expanduser().read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                return value
    return ""


# ----------------------------------------------------------------- names


def _clean_path(path: str) -> str:
    path = path.strip().strip("/")
    if not path:
        return ""
    if "\\" in path or "\0" in path:
        raise HubError("A file path inside a repository uses forward slashes, like split_files/vae/ae.safetensors.")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise HubError(f"'{path}' is not a valid file path inside a repository.")
    return "/".join(parts)


def parse(text: str) -> Ref:
    """Turn what the user typed into a repository, revision, path and quantization tag."""
    raw = (text or "").strip().strip('"').strip("'").strip()
    if not raw:
        raise HubError("Type the name of a Hugging Face model, like bartowski/Llama-3.2-3B-Instruct-GGUF.")
    if len(raw) > 500:
        raise HubError("That name is too long.")
    for prefix in ("llama-server -hf ", "llama-cli -hf ", "-hf "):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):].strip()
    revision = "main"
    if re.match(r"^https?://", raw, re.IGNORECASE):
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower()
        if host not in ("huggingface.co", "www.huggingface.co", "hf.co", urllib.parse.urlsplit(endpoint()).hostname):
            raise HubError("Only links to huggingface.co can be installed this way.")
        segs = [urllib.parse.unquote(s) for s in parsed.path.split("/") if s]
        if segs and segs[0] in ("models",):
            segs = segs[1:]
        if segs and segs[0] in ("datasets", "spaces"):
            raise HubError("That link is a dataset or Space, not a model.")
        if len(segs) < 2:
            raise HubError("That link does not point to a model repository.")
        repo = f"{segs[0]}/{segs[1]}"
        rest = segs[2:]
        path = ""
        if rest and rest[0] in ("tree", "blob", "resolve") and len(rest) >= 2:
            revision = rest[1]
            path = "/".join(rest[2:])
        elif rest:
            raise HubError("That link does not point to a model file or folder.")
        tag = ""
    else:
        for prefix in ("https://", "http://"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):]
        low = raw.lower()
        for prefix in ("hf.co/", "huggingface.co/", "www.huggingface.co/"):
            if low.startswith(prefix):
                raw = raw[len(prefix):]
                break
        tag = ""
        head, sep, maybe_tag = raw.rpartition(":")
        if sep and "/" not in maybe_tag:
            raw, tag = head, maybe_tag.strip()
        segs = [s for s in raw.split("/") if s != ""]
        if len(segs) < 2:
            raise HubError(f"'{text.strip()}' is not a Hugging Face model name. Names look like owner/model, "
                           "for example bartowski/Llama-3.2-3B-Instruct-GGUF.")
        repo = f"{segs[0]}/{segs[1]}"
        path = "/".join(segs[2:])
    owner, name = repo.split("/", 1)
    if not (NAME_RE.fullmatch(owner) and NAME_RE.fullmatch(name)):
        raise HubError(f"'{repo}' is not a valid Hugging Face model name.")
    if tag and not TAG_RE.fullmatch(tag):
        raise HubError(f"'{tag}' is not a valid quantization name (for example Q4_K_M).")
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", revision) or ".." in revision:
        raise HubError(f"'{revision}' is not a valid revision.")
    return Ref(repo=repo, revision=revision, path=_clean_path(path), tag=tag)


# ------------------------------------------------------------------- HTTP


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never send the access token to another host (the download CDN)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            for name in list(new.headers):
                if name.lower() == "authorization":
                    del new.headers[name]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


_OPENER = urllib.request.build_opener(_Redirect())


def _request(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0):
    hdrs = {"User-Agent": f"axis/{__version__}", **(headers or {})}
    tok = token()
    if tok:
        hdrs["Authorization"] = f"Bearer {tok}"
    return _OPENER.open(urllib.request.Request(url, headers=hdrs), timeout=timeout)


def _explain(exc: urllib.error.HTTPError, repo: str) -> str:
    if exc.code in (401, 403, 404):
        why = ("No Hugging Face model named '{repo}' was found, or it is private or gated. Check the spelling on "
               "huggingface.co. If the model is gated, accept its terms on its page, create an access token at "
               "huggingface.co/settings/tokens, set HF_TOKEN to it, and restart Axis.").format(repo=repo)
        if exc.code == 403:
            why = (f"Hugging Face refused access to '{repo}'. It is probably gated: accept its terms on its page "
                   "while signed in, and make sure HF_TOKEN is set to a token from that account.")
        if token() and exc.code == 401:
            why += " An access token is set, so it may be wrong or expired."
        return why
    if exc.code == 429:
        return "Hugging Face is limiting requests right now. Wait a few minutes and try again."
    return f"Hugging Face answered with an error ({exc.code} {exc.reason})."


def list_files(ref: Ref) -> List[Dict[str, Any]]:
    """Every file in the repository: [{path, size, sha256}]."""
    base = f"{endpoint()}/api/models/{ref.repo}"
    if ref.revision != "main":
        base += "/revision/" + urllib.parse.quote(ref.revision, safe="")
    try:
        with _request(base + "?blobs=true", {"Accept": "application/json"}) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise HubError(_explain(exc, ref.repo)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise HubError(f"Could not reach Hugging Face ({reason}). Check your internet connection.") from exc
    except json.JSONDecodeError as exc:
        raise HubError("Hugging Face sent a reply Axis could not read. Try again later.") from exc
    if not isinstance(data, dict):
        raise HubError("Hugging Face sent a reply Axis could not read. Try again later.")
    files = []
    for s in data.get("siblings") or []:
        if not isinstance(s, dict) or not isinstance(s.get("rfilename"), str):
            continue
        lfs = s.get("lfs") if isinstance(s.get("lfs"), dict) else {}
        size = lfs.get("size") if isinstance(lfs.get("size"), int) else s.get("size")
        sha = lfs.get("sha256") if isinstance(lfs.get("sha256"), str) else None
        files.append({"path": s["rfilename"], "size": size if isinstance(size, int) else None,
                      "sha256": sha.lower() if sha and re.fullmatch(r"[0-9a-fA-F]{64}", sha) else None})
    return files


# -------------------------------------------------------------- choosing


def _option(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    sizes = [f["size"] for f in files]
    return {"file": files[0]["path"], "parts": len(files),
            "size": sum(sizes) if all(isinstance(s, int) for s in sizes) else None}


def _in_folder(files: List[Dict[str, Any]], path: str) -> List[Dict[str, Any]]:
    if not path:
        return files
    return [f for f in files if f["path"] == path or f["path"].startswith(path + "/")]


def _split_parts(files: List[Dict[str, Any]], first: Dict[str, Any]) -> List[Dict[str, Any]]:
    m = SPLIT_RE.search(first["path"])
    if not m:
        return [first]
    stem = first["path"][: m.start()]
    count = int(m.group(2))
    by_path = {f["path"]: f for f in files}
    parts = []
    for i in range(1, count + 1):
        name = f"{stem}-{i:05d}-of-{count:05d}.gguf"
        f = by_path.get(name) or next((x for x in files if x["path"].lower() == name.lower()), None)
        if f is None:
            raise HubError(f"Part {i} of {count} of {first['path']} is missing from the repository.")
        parts.append(f)
    return parts


def pick_gguf(files: List[Dict[str, Any]], ref: Ref, choice: str = "") -> List[Dict[str, Any]]:
    """The GGUF file (all of its parts, when split) for a chat model."""
    def ok(f):
        name = PurePosixPath(f["path"]).name.lower()
        if not name.endswith(".gguf") or "mmproj" in name:
            return False
        m = SPLIT_RE.search(name)
        return m is None or int(m.group(1)) == 1

    pool = _in_folder(files, ref.path)
    if choice:
        match = [f for f in files if f["path"] == choice]
        if not match or not match[0]["path"].lower().endswith(".gguf"):
            raise HubError(f"'{choice}' is not a GGUF file in {ref.repo}.")
        return _split_parts(files, match[0])
    if ref.path and len(pool) == 1 and pool[0]["path"] == ref.path:
        if not ref.path.lower().endswith(".gguf"):
            raise HubError(f"{ref.path} is not a GGUF file. llama.cpp runs .gguf models.")
        return _split_parts(files, pool[0])
    cands = sorted((f for f in pool if ok(f)), key=lambda f: f["path"].lower())
    if not cands:
        if any(f["path"].lower().endswith((".safetensors", ".bin")) for f in pool):
            raise HubError(f"{ref.repo} has no GGUF files, so llama.cpp cannot run it. Search Hugging Face for a "
                           "GGUF version of this model (often named ...-GGUF), or install it with Ollama instead.")
        raise HubError(f"No GGUF model files were found in {ref.text}.")
    if ref.tag:
        tag = ref.tag.lower()
        hits = [f for f in cands if tag in PurePosixPath(f["path"]).name.lower()]
        exact = [f for f in hits if re.search(rf"(^|[-._]){re.escape(tag)}([-._]|$)",
                                              SPLIT_RE.sub(".gguf", PurePosixPath(f["path"]).name.lower())[:-5])]
        hits = exact or hits
        if len(hits) == 1:
            return _split_parts(files, hits[0])
        if not hits:
            raise NeedChoice(f"No file in {ref.repo} matches '{ref.tag}'. Choose one:",
                             [_option(_split_parts(files, f)) for f in cands])
        raise NeedChoice(f"Several files in {ref.repo} match '{ref.tag}'. Choose one:",
                         [_option(_split_parts(files, f)) for f in hits])
    if len(cands) == 1:
        return _split_parts(files, cands[0])
    preferred = [f for f in cands if "q4_k_m" in f["path"].lower()]
    if len(preferred) == 1:
        return _split_parts(files, preferred[0])  # the quantization llama-server -hf picks by default
    raise NeedChoice(f"{ref.repo} has {len(cands)} versions. Choose one (Q4_K_M is a good balance of size and quality):",
                     [_option(_split_parts(files, f)) for f in cands])


def pick_image_file(files: List[Dict[str, Any]], ref: Ref, choice: str = "") -> Dict[str, Any]:
    """One weights file (checkpoint, diffusion model, VAE or text encoder) for stable-diffusion.cpp."""
    if choice:
        match = [f for f in files if f["path"] == choice]
        if not match:
            raise HubError(f"'{choice}' is not a file in {ref.repo}.")
        return match[0]
    pool = _in_folder(files, ref.path)
    if ref.path and len(pool) == 1 and pool[0]["path"] == ref.path:
        f = pool[0]
        if not f["path"].lower().endswith(IMAGE_EXTS):
            raise HubError(f"{f['path']} is not a model file. stable-diffusion.cpp reads .safetensors, .gguf and .ckpt files.")
        return f
    cands = [f for f in pool if f["path"].lower().endswith(IMAGE_EXTS)]
    if ref.tag:
        cands = [f for f in cands if ref.tag.lower() in PurePosixPath(f["path"]).name.lower()] or cands
    if not cands:
        raise HubError(f"No model files (.safetensors, .gguf, .ckpt) were found in {ref.text}.")
    if len(cands) == 1:
        return cands[0]
    # Files at the top of the repository first: deeper ones are usually diffusers parts.
    cands.sort(key=lambda f: (f["path"].count("/"), f["path"].lower()))
    raise NeedChoice(f"{ref.text} has {len(cands)} model files. Choose one:", [_option([f]) for f in cands[:200]])


# ------------------------------------------------------------- downloading


def safe_dest(root: Path, repo: str, path: str) -> Path:
    """Where a repository file is stored: <root>/<owner>__<name>/<path>."""
    folder = root / repo.replace("/", "__")
    dest = folder.joinpath(*_clean_path(path).split("/"))
    try:
        dest.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HubError(f"'{path}' would be saved outside the models folder.") from exc
    return dest


def _sha256_of(path: Path, limit: int, check: Callable[[], None], report: Callable[[int], None]) -> "hashlib._Hash":
    h = hashlib.sha256()
    done = 0
    with path.open("rb") as f:
        while done < limit:
            chunk = f.read(min(CHUNK * 8, limit - done))
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            check()
            report(done)
    return h


def download(ref: Ref, f: Dict[str, Any], dest: Path, check: Callable[[], None],
             report: Callable[[int, Optional[int]], None], phase: Callable[[str], None]) -> Path:
    """Download one repository file to dest, resuming a .part file and verifying it.

    check() raises to cancel. report(done_bytes, total_bytes) shows progress.
    """
    size: Optional[int] = f.get("size")
    sha: Optional[str] = f.get("sha256")
    if dest.is_file():
        if size is None or dest.stat().st_size == size:
            return dest  # already here (installed earlier, or shared with another model)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    meta = dest.with_name(dest.name + ".part.json")
    url = f"{endpoint()}/{ref.repo}/resolve/{urllib.parse.quote(ref.revision, safe='')}/" + \
        "/".join(urllib.parse.quote(p) for p in f["path"].split("/"))

    # Resume only a .part file that belongs to this exact file version.
    have = part.stat().st_size if part.is_file() else 0
    try:
        old = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    except (OSError, json.JSONDecodeError):
        old = {}
    if have and (old.get("sha256") != sha or old.get("size") != size or (size is not None and have > size)):
        have = 0
    if not have and part.exists():
        part.unlink()
    meta.write_text(json.dumps({"url": url, "size": size, "sha256": sha}), encoding="utf-8")

    need = (size - have) if size is not None else 0
    free = shutil.disk_usage(dest.parent).free
    if size is not None and free - need < DISK_MARGIN:
        raise HubError(f"Not enough disk space for {PurePosixPath(f['path']).name}: it needs "
                       f"{_gb(need)} more and {_gb(free)} is free on that drive.")

    hasher = None
    if have:
        phase(f"Checking the part already downloaded of {PurePosixPath(f['path']).name}")
        hasher = _sha256_of(part, have, check, lambda d: report(d, size)) if sha else None

    attempt = 0
    while True:
        check()
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            phase(f"Downloading {PurePosixPath(f['path']).name}")
            with _request(url, headers, timeout=READ_TIMEOUT) as resp:
                if have and resp.status != 206:      # the server ignored the range: start over
                    have, hasher = 0, None
                total = size
                if total is None:
                    length = resp.headers.get("Content-Length")
                    total = (have + int(length)) if length and length.isdigit() else None
                if hasher is None and sha:
                    hasher = hashlib.sha256()
                with part.open("ab" if have else "wb") as out:
                    last = 0.0
                    while True:
                        check()
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        if hasher is not None:
                            hasher.update(chunk)
                        have += len(chunk)
                        attempt = 0
                        now = time.time()
                        if now - last > 0.25:
                            report(have, total)
                            last = now
                report(have, total)
            if size is not None and have < size:
                raise ConnectionError("the download ended early")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and size is not None and have >= size:
                break  # already complete
            if exc.code in (500, 502, 503, 504) and attempt < RETRIES:
                attempt += 1
                _pause(attempt, check)
                continue
            raise HubError(_explain(exc, ref.repo)) from exc
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
                raise HubError("The disk is full. Free some space and install again; the download will resume.") from exc
            if attempt >= RETRIES:
                reason = getattr(exc, "reason", exc)
                raise HubError(f"The download kept failing ({reason}). Install again later; it will resume "
                               "where it stopped.") from exc
            attempt += 1
            phase(f"Connection problem; retrying ({attempt} of {RETRIES})")
            _pause(attempt, check)
            have = part.stat().st_size if part.is_file() else 0
            if hasher is not None and sha:
                hasher = _sha256_of(part, have, check, lambda d: report(d, size)) if have else hashlib.sha256()

    got = part.stat().st_size
    if size is not None and got != size:
        part.unlink(missing_ok=True)
        raise HubError(f"{PurePosixPath(f['path']).name} downloaded with the wrong size ({got} instead of {size} "
                       "bytes) and was deleted. Install again.")
    if sha:
        phase(f"Verifying {PurePosixPath(f['path']).name}")
        digest = hasher.hexdigest() if hasher is not None else _sha256_of(part, got, check, lambda d: None).hexdigest()
        if digest != sha:
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            raise HubError(f"{PurePosixPath(f['path']).name} failed its checksum and was deleted. Install again.")
    os.replace(part, dest)
    meta.unlink(missing_ok=True)
    return dest


def _pause(attempt: int, check: Callable[[], None]) -> None:
    deadline = time.time() + min(30.0, 2.0 ** attempt)
    while time.time() < deadline:
        check()
        time.sleep(0.2)


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def download_all(ref: Ref, files: List[Dict[str, Any]], root: Path, job: Any) -> List[Path]:
    """Download several files (for example the parts of a split GGUF) with one progress bar."""
    sizes = [f.get("size") for f in files]
    total: Optional[int] = sum(sizes) if all(isinstance(s, int) for s in sizes) else None
    done_before = 0
    out: List[Path] = []
    for f in files:
        dest = safe_dest(root, ref.repo, f["path"])
        base = done_before
        out.append(download(ref, f, dest, job.check,
                            lambda d, t, base=base: job.progress(done=base + d, total=total if total is not None else (base + t if t else None)),
                            lambda p: job.progress(phase=p)))
        done_before += f.get("size") or dest.stat().st_size
    return out


def cleanup_partial(root: Path, repo: str, files: List[Dict[str, Any]]) -> None:
    """Remove .part files left by a cancelled download (installing again would resume them otherwise)."""
    for f in files:
        try:
            dest = safe_dest(root, repo, f["path"])
        except HubError:
            continue
        for p in (dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".part.json")):
            try:
                p.unlink()
            except OSError:
                pass


def prune_empty(folder: Path, root: Path) -> None:
    """Remove empty folders from folder up to (not including) root."""
    try:
        folder = folder.resolve()
        root = root.resolve()
        while folder != root and root in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                return
            folder = folder.parent
    except OSError:
        return


def describe_choice(e: NeedChoice) -> Tuple[str, List[Dict[str, Any]]]:
    return str(e), e.options
