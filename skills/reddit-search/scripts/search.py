#!/usr/bin/env python3
"""Find Reddit threads through the Brave Search API (no Reddit API/scraping)."""
import argparse
import fcntl
import http.client
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

API_URL = "https://api.search.brave.com/res/v1/web/search"
TIMEOUT_SECONDS = 30
MAX_QUERY_LEN = 350
MIN_COUNT, MAX_COUNT, DEFAULT_COUNT = 1, 20, 10
FRESHNESS_VALUES = ("pd", "pw", "pm", "py")
DEFAULT_CAP = 900
KEY_ENV = "BRAVE_SEARCH_API_KEY"
CAP_ENV = "BRAVE_MONTHLY_CAP"
KEYCHAIN_CMD = ["security", "find-generic-password", "-s", "brave-search",
                "-a", "api-key", "-w"]
SETUP_HELP = (
    "No Brave Search API key found.\n"
    "Store it once in the macOS Keychain (it prompts, so the key stays out "
    "of shell history):\n"
    "  security add-generic-password -s brave-search -a api-key -w\n"
    "Or export BRAVE_SEARCH_API_KEY for this shell."
)
BODY_SNIPPET_LEN = 300
TAG_RE = re.compile(r"<[^>]+>")
SUB_RE = re.compile(r"/r/([A-Za-z0-9_]+)(?:/|$)")


class SearchError(Exception):
    """User-facing failure; carries the process exit code."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def parse_args(argv):
    p = argparse.ArgumentParser(prog="search.py", add_help=True)
    p.add_argument("query")
    p.add_argument("count", nargs="?", default=str(DEFAULT_COUNT))
    p.add_argument("lang", nargs="?", default="en")
    p.add_argument("country", nargs="?", default="us")
    p.add_argument("--freshness", default=None)
    p.add_argument("--json", action="store_true", dest="as_json")
    return p.parse_args(argv)


def validate(ns):
    """Return a validated settings dict or raise SearchError(code 2)."""
    query = ns.query.strip()
    if not query:
        raise SearchError("Query must not be empty.", 2)
    if len(query) > MAX_QUERY_LEN:
        raise SearchError(f"Query too long (max {MAX_QUERY_LEN} chars).", 2)
    try:
        count = int(ns.count)
    except ValueError:
        raise SearchError("count must be an integer.", 2)
    if not MIN_COUNT <= count <= MAX_COUNT:
        raise SearchError(f"count must be {MIN_COUNT}-{MAX_COUNT}.", 2)
    for name, val in (("lang", ns.lang), ("country", ns.country)):
        if not (len(val) == 2 and val.isascii() and val.isalpha()):
            raise SearchError(f"{name} must be a 2-letter code.", 2)
    if ns.freshness is not None and ns.freshness not in FRESHNESS_VALUES:
        raise SearchError(
            f"freshness must be one of {', '.join(FRESHNESS_VALUES)}.", 2)
    return {"query": query, "count": count, "lang": ns.lang.lower(),
            "country": ns.country.lower(), "freshness": ns.freshness,
            "as_json": ns.as_json}


def get_api_key(env, run_cmd):
    key = (env.get(KEY_ENV) or "").strip()
    if key:
        return key
    try:
        result = run_cmd(KEYCHAIN_CMD)
    except (OSError, subprocess.SubprocessError):
        raise SearchError(SETUP_HELP)
    key = (getattr(result, "stdout", "") or "").strip()
    if getattr(result, "returncode", 1) != 0 or not key:
        raise SearchError(SETUP_HELP)
    return key


def _cap_from_env(env):
    raw = env.get(CAP_ENV)
    if raw is None or raw == "":
        return DEFAULT_CAP
    try:
        cap = int(raw)
    except ValueError:
        raise SearchError(f"{CAP_ENV} must be an integer, got {raw!r}.")
    if cap < 0:
        raise SearchError(f"{CAP_ENV} must not be negative.")
    return cap


def _counter_path(cache_dir, today):
    return Path(cache_dir) / f"usage-{today.year:04d}-{today.month:02d}.json"


def _read_count(path):
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
        n = data["requests"]
    except (OSError, ValueError, KeyError, TypeError):
        raise SearchError(
            f"Usage counter {path} is unreadable; refusing to search so the "
            "monthly cap cannot be bypassed. Fix or delete it deliberately.")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise SearchError(f"Usage counter {path} has an invalid value.")
    return n


LOCK_NAME = ".usage.lock"


@contextmanager
def _exclusive_lock(cache_dir):
    """Serialize read/check/write of the counter across processes/threads."""
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        fh = open(Path(cache_dir) / LOCK_NAME, "a")
    except OSError as exc:
        raise SearchError(f"Cannot create usage lock in {cache_dir}: {exc}")
    with fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def reserve_request(env, cache_dir, today):
    """Check cap and bump the counter before sending (Brave bills sent calls)."""
    cap = _cap_from_env(env)
    path = _counter_path(cache_dir, today)
    with _exclusive_lock(cache_dir):
        used = _read_count(path)
        if used >= cap:
            raise SearchError(
                f"Monthly request cap reached ({used}/{cap}). Brave bills "
                f"past the free credit. Raise it with {CAP_ENV}=<number> "
                "if intended.")
        _write_count(path, used + 1)


def _write_count(path, value):
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".usage-")
        with os.fdopen(fd, "w") as fh:
            json.dump({"requests": value}, fh)
        os.replace(tmp, path)
    except OSError as exc:
        raise SearchError(f"Cannot write usage counter {path}: {exc}")


def build_request(settings, key):
    params = {"q": f"site:reddit.com {settings['query']}",
              "count": settings["count"],
              "search_lang": settings["lang"],
              "country": settings["country"]}
    if settings["freshness"]:
        params["freshness"] = settings["freshness"]
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    return urllib.request.Request(url, headers=headers)


def fetch(request, http_open):
    try:
        with http_open(request, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise SearchError(_http_message(exc))
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.HTTPException) as exc:
        raise SearchError(f"Network error reaching Brave Search: {exc}")
    try:
        data = json.loads(raw)
    except ValueError:
        raise SearchError("Brave Search returned non-JSON response.")
    return data if isinstance(data, dict) else {}


def _http_message(exc):
    if exc.code in (401, 403):
        return "Brave rejected the API key (HTTP %d). Check the stored key." \
            % exc.code
    if exc.code == 429:
        return "Brave rate limited or out of credit (HTTP 429)."
    try:
        body = exc.read().decode("utf-8", "replace")[:BODY_SNIPPET_LEN]
    except Exception:
        body = ""
    return f"Brave Search HTTP {exc.code}: {body}"


def clean_text(text):
    if not isinstance(text, str):
        return ""
    text = html.unescape(TAG_RE.sub("", text)).replace("\xa0", " ")
    return " ".join(text.split())


def is_reddit_host(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("reddit.com", "redd.it") or host.endswith(".reddit.com")


def subreddit_of(url):
    m = SUB_RE.search(urlparse(url).path)
    return m.group(1) if m else None


def extract_results(data):
    web = data.get("web")
    items = web.get("results") if isinstance(web, dict) else None
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not is_reddit_host(url):
            continue
        out.append({
            "title": clean_text(item.get("title")),
            "link": url,
            "subreddit": subreddit_of(url),
            "age": clean_text(item.get("age")) or None,
            "snippet": clean_text(item.get("description")),
        })
    return out


def format_text(results):
    if not results:
        return "No Reddit results."
    blocks = []
    for r in results:
        lines = [f"Title: {r['title']}", f"Link: {r['link']}"]
        if r["subreddit"]:
            lines.append(f"Subreddit: r/{r['subreddit']}")
        if r["age"]:
            lines.append(f"Age: {r['age']}")
        lines.append(f"Snippet: {r['snippet']}")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


def _default_run_cmd(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def run(argv, env, run_cmd, http_open, cache_dir, today):
    settings = validate(parse_args(argv))
    key = get_api_key(env, run_cmd)
    try:
        reserve_request(env, cache_dir, today)
        data = fetch(build_request(settings, key), http_open)
        results = extract_results(data)
        if settings["as_json"]:
            return redact(json.dumps(results, indent=2), key)
        return redact(format_text(results), key)
    except SearchError as exc:
        raise SearchError(redact(str(exc), key), exc.code)


def redact(text, key):
    return text.replace(key, "[redacted]") if key else text


def main(argv, *, env=None, run_cmd=None, http_open=None, cache_dir=None,
         today=None):
    env = os.environ if env is None else env
    run_cmd = run_cmd or _default_run_cmd
    http_open = http_open or urllib.request.urlopen
    cache_dir = cache_dir or (Path.home() / ".cache" / "reddit-search")
    today = today or date.today()
    try:
        print(run(argv, env, run_cmd, http_open, cache_dir, today))
    except SearchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
