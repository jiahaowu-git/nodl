"""Zero-dependency HTTP client used by every resolver.

The whole point of this project is that it runs on a bare Python 3.8+ install
(no pip, no PyPI access required), so everything is built on top of
``urllib.request`` from the standard library.
"""

from __future__ import annotations

import gzip
import io
import json
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.cookiejar import CookieJar
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)

DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": UA_DESKTOP,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ByteDance / TikTok CDNs are picky: a request without a matching Referer
# normally answers 403 "Referer check failed".
PLATFORM_REFERERS: Dict[str, str] = {
    "douyin": "https://www.douyin.com/",
    "iesdouyin": "https://www.iesdouyin.com/",
    "tiktok": "https://www.tiktok.com/",
}


class HTTPError(Exception):
    """Raised for non-2xx responses that the caller did not explicitly allow."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body


class Response:
    __slots__ = ("status", "url", "headers", "body")

    def __init__(self, status: int, url: str, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.url = url
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def content_length(self) -> int:
        raw = self.headers.get("Content-Length")
        try:
            return int(raw) if raw else 0
        except (TypeError, ValueError):
            return 0


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if encoding == "gzip":
            return gzip.decompress(raw)
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:  # pragma: no cover - corrupt stream, fall back to raw
        return raw
    return raw


def _make_opener(cookie_jar: CookieJar, insecure: bool) -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [
        urllib.request.HTTPCookieProcessor(cookie_jar),
        urllib.request.HTTPSHandler(context=ctx),
    ]
    return urllib.request.build_opener(*handlers)


class Client:
    """A tiny session-like HTTP client with a cookie jar and retry logic."""

    def __init__(self, insecure: bool = False, timeout: int = 20) -> None:
        self.cookies = CookieJar()
        self.opener = _make_opener(self.cookies, insecure)
        self.timeout = timeout
        self._counter = 0
        self._no_redirect_opener: Optional[urllib.request.OpenerDirector] = None

    def _opener_for(self, allow_redirects: bool) -> urllib.request.OpenerDirector:
        if allow_redirects:
            return self.opener
        if self._no_redirect_opener is None:
            ctx = ssl.create_default_context()
            self._no_redirect_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self.cookies),
                urllib.request.HTTPSHandler(context=ctx),
                _NoRedirect(),
            )
        return self._no_redirect_opener

    # -- helpers ---------------------------------------------------------
    def next_ua(self) -> str:
        """Rotate a small pool of realistic UAs (not for evasion, just realism)."""
        pool = [UA_DESKTOP, UA_MOBILE, UA_ANDROID]
        self._counter += 1
        return pool[self._counter % len(pool)]

    def cookie_header(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.cookies)

    # -- requests --------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[bytes] = None,
        referer: Optional[str] = None,
        allow_redirects: bool = True,
        retries: int = 2,
        backoff: float = 0.8,
    ) -> Response:
        merged: Dict[str, str] = dict(DEFAULT_HEADERS)
        if headers:
            merged.update({k: v for k, v in headers.items() if v is not None})
        merged.setdefault("User-Agent", self.next_ua())
        if referer:
            merged["Referer"] = referer
        if self.cookie_header():
            merged.setdefault("Cookie", self.cookie_header())

        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method.upper())
            for k, v in merged.items():
                req.add_header(k, v)
            try:
                opener = self._opener_for(allow_redirects)
                with opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    body = _decompress(raw, resp.headers.get("Content-Encoding", ""))
                    return Response(resp.status, resp.geturl(), dict(resp.headers.items()), body)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                body = _decompress(raw, exc.headers.get("Content-Encoding", "") if exc.headers else "")
                # 3xx with redirects disabled: hand the Location header back to the caller
                if 300 <= exc.code < 400 and not allow_redirects:
                    return Response(exc.code, url, dict(exc.headers.items()), body)
                last_exc = HTTPError(exc.code, url, body.decode("utf-8", "replace"))
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(backoff * (attempt + 1) + random.uniform(0, 0.3))
                    continue
                raise last_exc from exc
            except Exception as exc:  # network hiccup, DNS, TLS
                last_exc = exc
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    continue
                raise
        raise last_exc if last_exc else RuntimeError("unreachable")

    def get(self, url: str, **kw: Any) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Response:
        return self.request("POST", url, **kw)

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Optional[Mapping[str, str]] = None,
        **kw: Any,
    ) -> Response:
        merged = {"Content-Type": "application/json;charset=UTF-8"}
        if headers:
            merged.update(headers)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.request("POST", url, headers=merged, data=body, **kw)

    def post_form(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Optional[Mapping[str, str]] = None,
        **kw: Any,
    ) -> Response:
        merged = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        if headers:
            merged.update(headers)
        body = urllib.parse.urlencode(payload).encode("utf-8")
        return self.request("POST", url, headers=merged, data=body, **kw)

    # -- downloads -------------------------------------------------------
    def stream(
        self,
        url: str,
        referer: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        start: int = 0,
        timeout: int = 60,
    ) -> Tuple[Any, Dict[str, str]]:
        """Open a streaming response. Returns (response object, headers)."""
        merged = dict(DEFAULT_HEADERS)
        merged["Accept"] = "*/*"
        merged.pop("Accept-Encoding", None)
        if headers:
            merged.update(headers)
        if referer:
            merged["Referer"] = referer
        if start:
            merged["Range"] = f"bytes={start}-"
        req = urllib.request.Request(url, method="GET")
        for k, v in merged.items():
            req.add_header(k, v)
        return self.opener.open(req, timeout=timeout), dict(req.header_items())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disable automatic redirects so we can observe the Location header."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def iter_chunks(resp: Iterable[bytes], size: int = 1 << 16) -> Iterable[bytes]:
    while True:
        chunk = resp.read(size)
        if not chunk:
            break
        yield chunk


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}GB"


__all__ = [
    "Client",
    "HTTPError",
    "Response",
    "UA_DESKTOP",
    "UA_MOBILE",
    "UA_ANDROID",
    "PLATFORM_REFERERS",
    "human",
    "iter_chunks",
]
