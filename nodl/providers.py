"""Optional third-party resolvers.

The first-party Douyin/TikTok detail endpoints are gated (Argus signature +
captcha challenge) for anonymous traffic, so a purely first-party parser cannot
work for everyone. These providers are **fallbacks**: they are tried only when
the first-party strategies fail, and they can be switched off with
``--source first-party``.

Two are implemented:

``tikwm``
    the well known public parser API – supports both Douyin and TikTok.

``ajaxsearch``
    reads the public ``/api/ajaxSearch`` endpoint of the site we originally
    reverse engineered.  It returns HTML that contains download links wrapped
    in HS256 JWTs; the JWT *payload* (base64, no secret needed) carries the
    upstream CDN url in clear text, so we can lift it out and download
    straight from the CDN.
"""

from __future__ import annotations

import base64
import html as htmllib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .httpclient import Client
from .models import MediaLink, Resolved

JWT_RE = re.compile(r"(?:dl\.snapcdn\.app/get\?token=)?([A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")
HREF_RE = re.compile(r'href="([^"]+)"')
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"')
TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
VID_RE = re.compile(r'id="TikTokId"\s+value="(\d+)"')

AUDIO_HINTS = (".mp3", ".m4a", ".aac", "audio_mpeg", "audio/mp3", "music")


def decode_jwt_payload(token: str) -> Optional[Dict[str, Any]]:
    """Base64-decode the payload of a JWT. No signature verification needed."""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload)
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _is_audio(url: str, filename: str = "") -> bool:
    low = (url + " " + filename).lower()
    return any(h in low for h in AUDIO_HINTS)


def _classify(url: str, filename: str, referer: str) -> MediaLink:
    audio = _is_audio(url, filename)
    ext = "mp3" if audio else "mp4"
    if not audio:
        low = url.lower()
        if low.endswith(".m3u8"):
            ext = "m3u8"
        elif low.endswith(".webm"):
            ext = "webm"
    return MediaLink(
        kind="audio" if audio else "video",
        label="MP3 原声" if audio else "MP4 无水印",
        url=url,
        referer=referer,
        ext=ext,
    )


def resolve_via_ajaxsearch(client: Client, url: str, referer: str) -> Optional[Resolved]:
    """Lift the upstream CDN urls out of the public ajaxSearch HTML."""
    try:
        resp = client.post_form(
            "https://tikvideo.app/api/ajaxSearch",
            {"q": url, "lang": "en", "cftoken": ""},
            headers={"Referer": "https://tikvideo.app/", "X-Requested-With": "XMLHttpRequest"},
            retries=1,
        )
        data = resp.json()
    except Exception:
        return None
    html = data.get("data") if isinstance(data, dict) else None
    if not html or "TikTokId" not in html:
        return None

    title = ""
    m = TITLE_RE.search(html)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        title = title.replace("&#x1F60D;", "").replace("&amp;", "&").strip()
    cover = ""
    m = IMG_RE.search(html)
    if m:
        cover = htmllib.unescape(m.group(1))
    vid = ""
    m = VID_RE.search(html)
    if m:
        vid = m.group(1)

    videos: List[MediaLink] = []
    audios: List[MediaLink] = []
    seen = set()

    def add(link: MediaLink) -> None:
        if link.url in seen:
            return
        seen.add(link.url)
        (audios if link.kind == "audio" else videos).append(link)

    # 1) the JWT wrapped links
    for token in JWT_RE.findall(html):
        payload = decode_jwt_payload(token)
        if not payload:
            continue
        upstream = payload.get("url")
        if not upstream:
            continue
        add(_classify(htmllib.unescape(upstream), payload.get("filename", ""), referer))

    # 2) raw CDN links present in the markup
    for href in HREF_RE.findall(html):
        if "snapcdn.app" in href or href.startswith("/"):
            continue
        if not href.startswith("http"):
            continue
        add(_classify(htmllib.unescape(href), "", referer))

    if not videos and not audios:
        return None

    resolved = Resolved(
        platform="douyin" if "douyin" in url else "tiktok",
        video_id=vid,
        title=title,
        cover=cover,
        source_url=url,
        strategy="ajaxsearch",
    )
    resolved.videos = videos
    resolved.audios = audios
    resolved.notes.append("数据来自公共解析接口（第三方），如需完全自主可控请使用 --cookie 或 --browser")
    return resolved


def resolve_via_tikwm(client: Client, url: str, referer: str) -> Optional[Resolved]:
    """Use the public tikwm parser API (supports Douyin and TikTok)."""
    try:
        resp = client.get(
            "https://www.tikwm.com/api/?url=" + _quote(url) + "&hd=1&cookies=",
            headers={"Referer": "https://www.tikwm.com/", "Accept": "application/json"},
            retries=1,
        )
        data = resp.json()
    except Exception:
        return None
    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return None

    resolved = Resolved(
        platform="douyin" if "douyin" in url else "tiktok",
        video_id=str(payload.get("aweme_id") or payload.get("id") or ""),
        title=str(payload.get("title") or ""),
        author=str(payload.get("author") or ""),
        cover=str(payload.get("cover") or payload.get("origin_cover") or ""),
        duration=int(payload.get("duration") or 0),
        source_url=url,
        strategy="tikwm",
    )
    seen = set()
    for key, label, quality in (("hdplay", "MP4 HD", "1080p"), ("play", "MP4 [1]", "")):
        u = payload.get(key)
        if u and u not in seen:
            seen.add(u)
            resolved.videos.append(MediaLink(kind="video", label=label, url=u,
                                             quality=quality, referer=referer, ext="mp4"))
    music = payload.get("music") or payload.get("music_play_url")
    if music and music not in seen:
        seen.add(music)
        resolved.audios.append(MediaLink(kind="audio", label="MP3 原声", url=music,
                                         referer=referer, ext="mp3"))
    if not resolved.videos and not resolved.audios:
        return None
    resolved.notes.append("数据来自公共解析接口（第三方），如需完全自主可控请使用 --cookie 或 --browser")
    return resolved


def _quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s, safe="")


PROVIDERS = {
    "tikwm": resolve_via_tikwm,
    "ajaxsearch": resolve_via_ajaxsearch,
}


__all__ = ["PROVIDERS", "decode_jwt_payload", "resolve_via_ajaxsearch", "resolve_via_tikwm"]
