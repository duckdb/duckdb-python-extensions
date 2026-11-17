"""HTTP requests with one user agent and one retry policy for the extension server, GitHub and PyPI."""

import http.client
import io
import json
import time
import urllib.error
import urllib.request

# extensions.duckdb.org sits behind Cloudflare, which answers 403 to Python's default user agent.
USER_AGENT = "duckdb-extension-wheel/1.0"

RETRIES = 3
RETRY_DELAY = 5


def _attempt(req, timeout, read):
    """One request. Returns (status, value) or raises; a 5xx and a dropped connection are retried by the caller."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, read(resp)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code >= 500:
            raise RuntimeError(f"{req.get_method()} {req.full_url} returned {e.code}") from e
        return e.code, None


def _with_retries(req, timeout, read):
    for attempt in range(RETRIES):
        try:
            return _attempt(req, timeout, read)
        except (RuntimeError, urllib.error.URLError, http.client.HTTPException, OSError) as e:
            last = e
        if attempt + 1 < RETRIES:
            time.sleep(RETRY_DELAY)
    raise last


def head(url):
    """The status of a HEAD request: 200, 404, or another non-5xx code."""
    status, _ = head_headers(url)
    return status


def _file_headers(resp):
    return {"etag": resp.headers.get("ETag"), "last_modified": resp.headers.get("Last-Modified")}


def head_headers(url):
    """The status of a HEAD request and the file's ETag and Last-Modified (None each when the server sends none)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    status, headers = _with_retries(req, 30, _file_headers)
    return status, headers or {"etag": None, "last_modified": None}


def get_json(url, accept, token=None):
    """A JSON document; raises urllib.error.HTTPError for a 4xx answer such as 404."""
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    status, body = _with_retries(req, 30, lambda resp: resp.read())
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "error", {}, io.BytesIO())
    return json.loads(body.decode("utf-8"))


def get_bytes(url):
    """The body of a GET, or None for a 404."""
    body, _ = get_bytes_headers(url)
    return body


def get_bytes_headers(url):
    """The body of a GET and the file's ETag and Last-Modified; (None, None) for a 404."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    status, result = _with_retries(req, 300, lambda resp: (resp.read(), _file_headers(resp)))
    if status == 404:
        return None, None
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "error", {}, io.BytesIO())
    return result


def get_text(url):
    """The body of a GET as UTF-8 text, or None for a 404."""
    body = get_bytes(url)
    return None if body is None else body.decode("utf-8")
