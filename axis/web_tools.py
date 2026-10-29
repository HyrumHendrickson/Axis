"""Actions that reach the internet: web search, reading a web page, and Wikipedia.

Every action takes a size argument (max_results and/or max_chars) so the agent
decides how much text comes back. A large model can ask for a lot; a small model
with a short context can ask for a little. Long pages and articles can be read in
pieces with start.

Only the Python standard library is used. Web search uses DuckDuckGo's HTML
page by default. Set AXIS_SEARCH_URL to a SearXNG instance (for example
http://127.0.0.1:8888) to use that instead; its JSON format must be enabled.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__

USER_AGENT = f"Axis/{__version__} (https://github.com/HyrumHendrickson/Axis)"
TIMEOUT = 15            # seconds to wait for each answer from a site
TOTAL_TIMEOUT = 45      # seconds for a whole page: a site that trickles bytes cannot hold a reply forever
MAX_DOWNLOAD = 3 * 1024 * 1024

MAX_RESULTS = 10
MAX_CHARS = 20000
MIN_CHARS = 200
DEFAULT_SEARCH_CHARS = 2000
DEFAULT_PAGE_CHARS = 3000

DDG_URL = "https://html.duckduckgo.com/html/"
WIKI_LANG = re.compile(r"^[a-z][a-z-]{1,15}$")


class WebError(Exception):
    pass


# ---------------------------------------------------------------- definitions

def _size_param(default: int, what: str) -> Dict[str, Any]:
    return {"type": "integer", "description": f"Most characters of {what} to return, {MIN_CHARS}-{MAX_CHARS} "
            f"(default {default}). Ask for less if your context is small, more if you need detail."}


START_PARAM = {"type": "integer", "description": "Character to start from (default 0). Use the number given at "
               "the end of a cut-off result to read the next part."}

SPECS: Dict[str, Dict[str, Any]] = {
    "web_search": {
        "description": "Search the web. Returns titles, links and short snippets. "
        "Use fetch_page on a link to read the full page.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "max_results": {"type": "integer", "description": f"Number of results, 1-{MAX_RESULTS} (default 5)"},
                "max_chars": _size_param(DEFAULT_SEARCH_CHARS, "results"),
            },
            "required": ["query"],
        },
    },
    "fetch_page": {
        "description": "Read the text of a web page (http or https link).",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full link, starting with http:// or https://"},
                "max_chars": _size_param(DEFAULT_PAGE_CHARS, "page text"),
                "start": START_PARAM,
            },
            "required": ["url"],
        },
    },
    "wikipedia_search": {
        "description": "Search Wikipedia for article titles matching a topic, with a short snippet for each.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic to look for"},
                "max_results": {"type": "integer", "description": f"Number of articles, 1-{MAX_RESULTS} (default 5)"},
                "lang": {"type": "string", "description": "Wikipedia language code, e.g. en, de, la (default en)"},
            },
            "required": ["query"],
        },
    },
    "wikipedia_article": {
        "description": "Read a Wikipedia article as plain text. Set intro_only to true for just the opening summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Article title, e.g. Marcus Aurelius"},
                "intro_only": {"type": "boolean", "description": "true for only the summary before the first heading (default false)"},
                "max_chars": _size_param(DEFAULT_PAGE_CHARS, "article text"),
                "start": START_PARAM,
                "lang": {"type": "string", "description": "Wikipedia language code (default en)"},
            },
            "required": ["title"],
        },
    },
}


# -------------------------------------------------------------------- helpers

def _int(args: Dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    value = args.get(key, default)
    if value is None or value == "":
        return default
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        raise WebError(f"{key} must be a whole number.")
    return max(lo, min(hi, value))


def _bool(args: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value)


def _str(args: Dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WebError(f"{key} must be non-empty text.")
    return value.strip()


def _lang(args: Dict[str, Any]) -> str:
    lang = str(args.get("lang") or "en").strip().lower()
    if not WIKI_LANG.fullmatch(lang):
        raise WebError("lang must be a Wikipedia language code such as en, de or la.")
    return lang


def _window(text: str, start: int, limit: int, more_hint: str) -> str:
    """Return text[start:start+limit] and say how to read the rest."""
    total = len(text)
    if start >= total and total:
        return f"(Nothing at start={start}; the text is only {total} characters long.)"
    piece = text[start:start + limit]
    end = start + len(piece)
    prefix = f"(Showing characters {start}-{end} of {total}.)\n" if start else ""
    suffix = f"\n... ({total - end} more characters. {more_hint} start={end} to read on.)" if end < total else ""
    return prefix + piece + suffix


def _blocked(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """True for an address that is not on the public internet.

    is_global also rules out the shared range 100.64.0.0/10 (carrier-grade NAT, used by Tailscale), which
    is_private does not. An IPv4 address written inside an IPv6 one (::ffff:127.0.0.1) is judged as IPv4.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)


_LOCAL_REFUSED = "Links to this computer or the local network cannot be opened."


def _check_public(url: str) -> None:
    """Refuse links to this computer or the local network."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError("Only http:// and https:// links can be opened.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise WebError(f"Could not find the site {parts.hostname}.")
    for info in infos:
        if _blocked(ipaddress.ip_address(info[4][0].split("%")[0])):
            raise WebError(_LOCAL_REFUSED)


def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Open a connection, checking the address that is actually connected to.

    _check_public looks a name up once and the connection looks it up again; a site that answers the first
    lookup with a public address and the second with 127.0.0.1 (DNS rebinding) would reach this computer.
    Checking here, on the address used, closes that gap.
    """
    host, port = address
    error: Optional[OSError] = None
    for family, kind, proto, _, sockaddr in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        if _blocked(ipaddress.ip_address(sockaddr[0].split("%")[0])):
            raise WebError(_LOCAL_REFUSED)
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    raise error or OSError("The site has no address.")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        # Through a proxy the connection goes to the proxy, which is allowed to be on a private network.
        proxied = req.selector.lower().startswith(("http://", "https://"))
        return self.do_open(http.client.HTTPConnection if proxied else _PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        proxied = bool(getattr(req, "_tunnel_host", None))
        return self.do_open(http.client.HTTPSConnection if proxied else _PublicHTTPSConnection, req,
                            context=self._context)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_OPENER = urllib.request.build_opener(_PublicRedirects, _PublicHTTPHandler, _PublicHTTPSHandler)


def _read_limited(resp) -> bytes:
    """Read at most MAX_DOWNLOAD + 1 bytes, giving up if the whole answer takes longer than TOTAL_TIMEOUT."""
    deadline = time.monotonic() + TOTAL_TIMEOUT
    buf = bytearray()
    while len(buf) <= MAX_DOWNLOAD:
        want = min(64 * 1024, MAX_DOWNLOAD + 1 - len(buf))
        # read1 returns what has arrived; read(n) would wait for all n bytes, and the deadline could not act.
        chunk = resp.read1(want) if hasattr(resp, "read1") else resp.read(want)
        if not chunk:
            break
        buf += chunk
        if time.monotonic() > deadline:
            raise WebError(f"The site sent its answer too slowly (more than {TOTAL_TIMEOUT} seconds).")
    return bytes(buf)


def _http(url: str, data: Optional[bytes] = None, public_only: bool = False,
          accept: str = "*/*") -> Tuple[int, str, str]:
    """Fetch url. Returns (status, content type, decoded text)."""
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en;q=0.9, *;q=0.5"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    if public_only:
        _check_public(url)
    opener = _PUBLIC_OPENER if public_only else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = _read_limited(resp)
            status = resp.status
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise WebError(f"The site answered with error {exc.code} ({exc.reason}).")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, WebError):
            raise reason
        raise WebError(f"Could not reach the site: {reason}. Check the internet connection.")
    except (socket.timeout, TimeoutError):
        raise WebError(f"The site did not answer within {TIMEOUT} seconds.")
    try:
        text = raw[:MAX_DOWNLOAD].decode(charset, errors="replace")
    except LookupError:
        text = raw[:MAX_DOWNLOAD].decode("utf-8", errors="replace")
    return status, ctype.split(";")[0].strip().lower(), text


def _get_json(url: str, params: Dict[str, Any]) -> Any:
    _, _, text = _http(url + "?" + urllib.parse.urlencode(params), accept="application/json")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise WebError("The service sent back something that was not JSON.")


class _TextExtractor(HTMLParser):
    """Turn HTML into readable plain text."""

    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe", "canvas", "form", "button", "select"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "nav",
             "aside", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "dd", "dt", "figcaption", "main"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if tag in ("h1", "h2", "h3", "h4"):
                self.parts.append("## " if tag != "h1" else "# ")
            elif tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\r\f\v\u00a0]+", " ", ln).strip() for ln in raw.split("\n")]
        out: List[str] = []
        for ln in lines:
            if ln in ("", "-", "#", "##") and (not out or out[-1] == ""):
                continue
            out.append("" if ln in ("-", "#", "##") else ln)
        return "\n".join(out).strip()


def html_to_text(html: str) -> Tuple[str, str]:
    """Returns (title, text)."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed HTML: keep whatever was read
        pass
    return parser.title.strip(), parser.text()


def _strip_tags(html: str) -> str:
    return html_to_text(html)[1].replace("\n", " ").strip()


# ----------------------------------------------------------------- web search

class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self.field: Optional[str] = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.results.append({"title": "", "url": _ddg_target(dict(attrs).get("href") or ""), "snippet": ""})
            self.field, self.depth = "title", 1
        elif "result__snippet" in classes and self.results:
            self.field, self.depth = "snippet", 1
        elif self.field:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.field:
            self.depth -= 1
            if self.depth <= 0:
                self.field = None

    def handle_data(self, data):
        if self.field and self.results:
            self.results[-1][self.field] += data


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps links as //duckduckgo.com/l/?uddg=<real link>."""
    if href.startswith("//"):
        href = "https:" + href
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
    return query["uddg"][0] if "uddg" in query else href


def _search_duckduckgo(query: str, count: int) -> List[Dict[str, str]]:
    status, _, html = _http(DDG_URL, data=urllib.parse.urlencode({"q": query}).encode("utf-8"))
    parser = _DuckDuckGoParser()
    parser.feed(html)
    results = [r for r in parser.results if r["url"].startswith("http") and "duckduckgo.com/y.js" not in r["url"]]
    if not results and (status == 202 or "anomaly" in html.lower()):
        raise WebError("The search service is limiting requests right now. Wait a minute and try again. "
                       "(The person running Axis can set AXIS_SEARCH_URL to a SearXNG server to avoid this.)")
    return results[:count]


def _search_searxng(base: str, query: str, count: int) -> List[Dict[str, str]]:
    data = _get_json(base.rstrip("/") + "/search", {"q": query, "format": "json"})
    items = data.get("results", []) if isinstance(data, dict) else []
    return [{"title": str(i.get("title") or ""), "url": str(i.get("url") or ""), "snippet": str(i.get("content") or "")}
            for i in items[:count] if isinstance(i, dict) and i.get("url")]


def web_search(args: Dict[str, Any]) -> str:
    query = _str(args, "query")
    count = _int(args, "max_results", 5, 1, MAX_RESULTS)
    budget = _int(args, "max_chars", DEFAULT_SEARCH_CHARS, MIN_CHARS, MAX_CHARS)
    base = os.environ.get("AXIS_SEARCH_URL", "").strip()
    results = _search_searxng(base, query, count) if base else _search_duckduckgo(query, count)
    if not results:
        return f"No results for '{query}'. Try different or fewer words."
    blocks = []
    for n, r in enumerate(results, 1):
        title = " ".join(r["title"].split()) or r["url"]
        snippet = " ".join(r["snippet"].split())
        blocks.append(f"{n}. {title}\n   {r['url']}" + (f"\n   {snippet}" if snippet else ""))
    out, used = [], 0
    for b in blocks:
        if out and used + len(b) > budget:
            out.append(f"... ({len(blocks) - len(out)} more results left out to stay under max_chars={budget})")
            break
        if len(b) > budget:
            b = b[:budget] + "..."
        out.append(b)
        used += len(b) + 2
    return "\n\n".join(out)


def fetch_page(args: Dict[str, Any]) -> str:
    url = _str(args, "url")
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise WebError("Only http:// and https:// links can be opened.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    limit = _int(args, "max_chars", DEFAULT_PAGE_CHARS, MIN_CHARS, MAX_CHARS)
    start = _int(args, "start", 0, 0, 10 ** 9)
    _, ctype, body = _http(url, public_only=True, accept="text/html, text/plain;q=0.9, */*;q=0.5")
    if ctype in ("text/html", "application/xhtml+xml", ""):
        title, text = html_to_text(body)
    elif ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
        title, text = "", body.strip()
    else:
        raise WebError(f"That link is not a text page (it is {ctype}), so it cannot be read.")
    if not text:
        return "The page has no readable text. It may need JavaScript, which fetch_page cannot run."
    head = f"{title}\n{url}\n\n" if title else f"{url}\n\n"
    return head + _window(text, start, limit, "Call fetch_page again with")


# ------------------------------------------------------------------ Wikipedia

def _wiki_api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def wikipedia_search(args: Dict[str, Any]) -> str:
    query = _str(args, "query")
    count = _int(args, "max_results", 5, 1, MAX_RESULTS)
    lang = _lang(args)
    data = _get_json(_wiki_api(lang), {
        "action": "query", "list": "search", "srsearch": query, "srlimit": count,
        "srprop": "snippet|wordcount", "format": "json", "formatversion": 2,
    })
    hits = (data.get("query") or {}).get("search") or [] if isinstance(data, dict) else []
    if not hits:
        return f"No Wikipedia articles found for '{query}'."
    lines = []
    for n, hit in enumerate(hits, 1):
        snippet = _strip_tags(str(hit.get("snippet") or ""))
        words = hit.get("wordcount")
        lines.append(f"{n}. {hit.get('title')}" + (f"  ({words} words)" if words else "") + (f"\n   {snippet}" if snippet else ""))
    return "\n".join(lines) + "\n\nUse wikipedia_article with one of these titles to read it."


def wikipedia_article(args: Dict[str, Any]) -> str:
    title = _str(args, "title")
    lang = _lang(args)
    limit = _int(args, "max_chars", DEFAULT_PAGE_CHARS, MIN_CHARS, MAX_CHARS)
    start = _int(args, "start", 0, 0, 10 ** 9)
    params: Dict[str, Any] = {
        "action": "query", "prop": "extracts|info", "inprop": "url", "explaintext": 1,
        "exsectionformat": "wiki", "redirects": 1, "titles": title, "format": "json", "formatversion": 2,
    }
    if _bool(args, "intro_only"):
        params["exintro"] = 1
    data = _get_json(_wiki_api(lang), params)
    pages = (data.get("query") or {}).get("pages") or [] if isinstance(data, dict) else []
    page = pages[0] if pages and isinstance(pages[0], dict) else {}
    if not page or page.get("missing") or page.get("invalid"):
        return f"There is no Wikipedia article titled '{title}'. Use wikipedia_search to find the exact title."
    text = re.sub(r"\n{3,}", "\n\n", str(page.get("extract") or "")).strip()
    if not text:
        return f"'{page.get('title', title)}' has no text (it may be a disambiguation or special page)."
    head = f"{page.get('title', title)}\n{page.get('fullurl', '')}\n\n"
    return head + _window(text, start, limit, "Call wikipedia_article again with")


HANDLERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "wikipedia_search": wikipedia_search,
    "wikipedia_article": wikipedia_article,
}
