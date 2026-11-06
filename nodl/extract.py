"""Link normalisation: turn whatever the user pasted into a canonical target.

Users paste three very different kinds of strings:

* a bare share link            ``https://v.douyin.com/iQxAbC12/``
* a full share sentence        ``7.86 复制打开抖音... https://v.douyin.com/iQxAbC12/ ...``
* a long web URL               ``https://www.douyin.com/video/7615930241370348219``

This module extracts the first http(s) URL, expands short links, and figures out
which platform we are dealing with plus the platform specific video id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

from .httpclient import Client

URL_RE = re.compile(r"https?://[^\s'\"<>）)】]+", re.I)

DOYIN_HOSTS = ("douyin.com", "iesdouyin.com", "douyinvod.com")
TIKTOK_HOSTS = ("tiktok.com", "tiktokcdn.com", "tiktokv.com")

# https://www.douyin.com/video/7615930241370348219
# https://www.douyin.com/note/7615930241370348219   (image post)
DOYIN_ID_RE = re.compile(r"/(?:video|note|slides)/(\d{6,25})")
# https://www.tiktok.com/@user/video/6718335390845095173
TIKTOK_ID_RE = re.compile(r"/(?:@[\w.\-]+/)?video/(\d{6,25})")
# https://www.tiktok.com/t/ZTxxxxxxxx/
TIKTOK_SHORT_RE = re.compile(r"/t/([A-Za-z0-9]+)")
# Douyin "uri" – the short id used by the mobile playback endpoint
URI_RE = re.compile(r"^v[0-9a-z]{16,32}$")
# Hosts that already hand us a media file
CDN_HOSTS = (
    "douyinvod.com", "zjcdn.com", "bytecdn.com", "byteimg.com", "douyinstatic.com",
    "tiktokcdn.com", "tiktokcdn-us.com", "tokcdn.com", "muscdn.com", "ibytedtos.com",
    "musical-ly.com", "ssstiktok.com", "tiktokcdn-eu.com",
)


def is_direct_media(url: str) -> bool:
    """True when the URL already points at a media file."""
    host = _host(url).lower()
    path = urlparse(url).path.lower()
    if any(h in host for h in CDN_HOSTS):
        return True
    return path.endswith((".mp4", ".m3u8", ".mp3", ".webm", ".mov", ".m4a", ".aac"))


def is_uri(text: str) -> bool:
    return bool(URI_RE.match(text.strip()))


@dataclass
class Target:
    """A normalised download target."""

    platform: str                     # "douyin" | "tiktok"
    url: str                          # the expanded canonical url
    original: str                     # exactly what the user gave us
    video_id: str = ""
    page_url: str = ""                # the page we should scrape
    share_url: str = ""               # short/original share link
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def referer(self) -> str:
        return "https://www.douyin.com/" if self.platform == "douyin" else "https://www.tiktok.com/"


def find_url(text: str) -> Optional[str]:
    """Pull the first http(s) URL out of arbitrary pasted text."""
    if not text:
        return None
    m = URL_RE.search(text.strip())
    if not m:
        return None
    url = m.group(0)
    # trailing punctuation that commonly comes with share sentences
    url = url.rstrip(".,;:!?、，。；：！？”’)）")
    return url


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _expand(client: Client, url: str, referer: Optional[str] = None) -> str:
    """Follow redirects of a short link and return the final URL."""
    try:
        resp = client.get(url, referer=referer, allow_redirects=True, retries=1)
        return resp.url
    except Exception:
        return url


def normalise(client: Client, raw: str, expand: bool = True) -> Target:
    """Normalise pasted text into a :class:`Target`."""
    stripped = (raw or "").strip()

    # 1) a bare Douyin "uri" (v1e00fgi0000...) -> straight to the app endpoint
    if is_uri(stripped):
        return Target(platform="douyin-uri", url=stripped, original=stripped,
                      video_id=stripped, page_url="", share_url=stripped)

    url = find_url(stripped)
    if not url:
        raise ValueError("没有从输入里找到 http(s) 链接")

    original = stripped
    host = _host(url)

    # 2) a direct media url -> use it as-is
    if is_direct_media(url):
        return Target(platform="direct", url=url, original=original, page_url=url, share_url=url)

    is_douyin = any(h in host for h in DOYIN_HOSTS)
    is_tiktok = any(h in host for h in TIKTOK_HOSTS)
    if not (is_douyin or is_tiktok):
        # Unknown host: still try, but keep the original host for the request.
        raise ValueError(f"暂不支持的链接域名：{host or url}")

    share_url = url
    expanded = url
    if expand and host in ("v.douyin.com", "www.iesdouyin.com/share", "vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com"):
        expanded = _expand(client, url, referer="https://www.douyin.com/" if is_douyin else "https://www.tiktok.com/")

    ehost = _host(expanded)
    if is_douyin:
        vid = ""
        m = DOYIN_ID_RE.search(expanded) or DOYIN_ID_RE.search(url)
        if m:
            vid = m.group(1)
        if vid:
            page_url = f"https://www.douyin.com/video/{vid}"
        else:
            # A user profile link -> fall back to the mobile share page.
            page_url = expanded
        return Target(
            platform="douyin",
            url=expanded,
            original=original,
            video_id=vid,
            page_url=page_url,
            share_url=share_url,
        )

    vid = ""
    m = TIKTOK_ID_RE.search(expanded) or TIKTOK_ID_RE.search(url)
    if m:
        vid = m.group(1)
    else:
        m2 = TIKTOK_SHORT_RE.search(expanded)
        if m2:
            vid = m2.group(1)
    page_url = f"https://www.tiktok.com/@i/video/{vid}" if vid else expanded
    # keep the handle around, it makes the file name nicer
    handle = ""
    hm = re.search(r"tiktok\.com/@([\w.\-]+)/", expanded)
    if hm:
        handle = hm.group(1)
    return Target(
        platform="tiktok",
        url=expanded,
        original=original,
        video_id=vid,
        page_url=page_url,
        share_url=share_url,
        extra={"handle": handle},
    )


def platform_of(url: str) -> Optional[str]:
    host = _host(url)
    if any(h in host for h in DOYIN_HOSTS):
        return "douyin"
    if any(h in host for h in TIKTOK_HOSTS):
        return "tiktok"
    return None


__all__ = ["Target", "find_url", "normalise", "platform_of"]
