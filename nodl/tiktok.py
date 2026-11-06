"""TikTok resolver.

TikTok's web page ships the whole video record as JSON in a
``<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`` tag (older pages use
``window.SIGI_STATE``). Inside it the interesting node is
``webapp["video-detail"]["itemInfo"]["itemStruct"]["video"]`` which holds:

``playAddr``
    the clean stream – **no** TikTok watermark.
``downloadAddr``
    the burnt-in-watermark copy that the app's "save" button serves.
``bitrateInfo.bitrate[].playAddr``
    one entry per rendition, which is where the 1080p/720p ladder comes from.
``music.playUrl``
    the audio-only stream, i.e. our MP3 button.

So the rule is simple: **never use ``downloadAddr``**, use ``playAddr``.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from .extract import Target
from .httpclient import Client
from .jsonutil import find_dict_containing, normalise_url
from .models import MediaLink, Resolved

REFERER = "https://www.tiktok.com/"

UNIVERSAL_RE = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S
)
SIGI_RE = re.compile(r"window\._?SIGI_STATE\s*=\s*(\{)", re.S)
SIGI_RE2 = re.compile(r"<script[^>]+id=\"SIGI_STATE\"[^>]*>(.*?)</script>", re.S)


def _extract_json_blob(html_text: str, start: int) -> Optional[Dict[str, Any]]:
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html_text)):
        ch = html_text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html_text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_page(html_text: str) -> Optional[Dict[str, Any]]:
    """Return the universal/sigi payload embedded in a TikTok page."""
    m = UNIVERSAL_RE.search(html_text)
    if m:
        try:
            data = json.loads(html_text[m.start(1):m.end(1)] and m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    for pattern in (SIGI_RE, SIGI_RE2):
        m = pattern.search(html_text)
        if not m:
            continue
        blob = _extract_json_blob(html_text, m.start(1))
        if blob:
            return blob
    return None


def _locate_item(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Find the item struct, whatever nesting TikTok uses this month.

    Historically the payload looked like ``webapp["video-detail"]["itemInfo"]
    ["itemStruct"]``; newer pages wrap it in ``__DEFAULT_SCOPE__`` and the
    detail key becomes ``"webapp.video-detail"``.
    """
    def dig(scope: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(scope, dict):
            return None
        for key in ("video-detail", "webapp.video-detail", "video_detail"):
            detail = scope.get(key)
            if isinstance(detail, dict):
                info = detail.get("itemInfo")
                if isinstance(info, dict) and isinstance(info.get("itemStruct"), dict):
                    return info["itemStruct"]
                if isinstance(detail.get("itemStruct"), dict):
                    return detail["itemStruct"]
        return None

    for scope_key in ("webapp", "__DEFAULT_SCOPE__", "__UNIVERSAL_DATA_FOR_REHYDRATION__"):
        scope = payload.get(scope_key)
        hit = dig(scope)
        if hit:
            return hit
        # __DEFAULT_SCOPE__ holds flat keys like "webapp.video-detail"
        if isinstance(scope, dict):
            for value in scope.values():
                if isinstance(value, dict):
                    hit = dig(value)
                    if hit:
                        return hit
    # last resort: the dict that literally owns a "playAddr"
    owner = find_dict_containing(payload, ("playAddr",))
    if owner:
        return owner
    return None


def extract_item(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dig the video struct out of whatever payload shape TikTok used today."""
    item = _locate_item(payload)
    if not item:
        return None
    if "video" not in item and "playAddr" in item:
        # we landed straight on the video object
        item = {"id": "", "desc": "", "author": {}, "music": {}, "video": item}

    video = item.get("video") or {}
    music = item.get("music") or {}
    author = item.get("author") or {}

    play_addr = normalise_url(video.get("playAddr") or "")
    download_addr = normalise_url(video.get("downloadAddr") or "")

    renditions: List[Dict[str, Any]] = []
    bitrate_info = video.get("bitrateInfo") or {}
    for br in bitrate_info.get("bitrate") or []:
        if not isinstance(br, dict):
            continue
        addr = normalise_url(br.get("PlayAddr") or br.get("playAddr") or "")
        if not addr:
            continue
        renditions.append({
            "url": addr,
            "bitrate": br.get("Bitrate") or br.get("bitrate") or 0,
            "gear": (br.get("GearName") or br.get("gear_name") or ""),
            "quality": (br.get("QualityType") or br.get("quality_type") or ""),
            "width": br.get("Width") or 0,
            "height": br.get("Height") or 0,
        })
    renditions.sort(key=lambda r: (int(r.get("height") or 0), int(r.get("bitrate") or 0)), reverse=True)

    audio = ""
    if isinstance(music, dict):
        audio = normalise_url(music.get("playUrl") or music.get("PlayUrl") or "")
        if not audio:
            mu = music.get("playUrl") if isinstance(music.get("playUrl"), dict) else {}
            if isinstance(mu, dict):
                audio = normalise_url(mu.get("uri") or "")

    cover = normalise_url(video.get("cover") or video.get("originCover") or "")
    if not cover and isinstance(item.get("video"), dict):
        c = video.get("cover")
        if isinstance(c, dict):
            cover = normalise_url(c.get("url") or "")

    return {
        "id": str(item.get("id") or ""),
        "desc": item.get("desc") or "",
        "duration": int(item.get("duration") or video.get("duration") or 0),
        "play_addr": play_addr,
        "download_addr": download_addr,
        "renditions": renditions,
        "audio": audio,
        "cover": cover,
        "author": author.get("uniqueId") or author.get("nickname") or "",
        "nickname": author.get("nickname") or "",
    }


class TikTokResolver:
    """Resolve a TikTok link into downloadable media links."""

    def __init__(self, client: Client, cookie: Optional[str] = None) -> None:
        self.client = client
        self.cookie = cookie

    def resolve(self, target: Target, use_browser: bool = False) -> Resolved:
        errors: List[str] = []
        item = None
        strategy = ""

        url = target.page_url or target.url
        try:
            resp = self.client.get(url, referer=REFERER,
                                   headers={"Cookie": self.cookie} if self.cookie else None)
            payload = parse_page(resp.text)
            if payload:
                item = extract_item(payload)
                if item:
                    strategy = "page-json"
        except Exception as exc:  # network hiccup, geo block, captcha page
            errors.append(f"page fetch failed: {exc}")

        if not item and use_browser:
            from .browser import browser_tiktok
            item = browser_tiktok(url, cookie=self.cookie)
            if item:
                strategy = "browser"

        if not item:
            meta = self._oembed(target)
            if meta and meta.get("thumbnail_url"):
                # Metadata only – still give the user the cover and a clear error.
                raise RuntimeError(
                    "TikTok 页面没有返回视频数据（可能需要 Cookie / 地区限制 / 触发了人机校验）。\n"
                    "可用 --cookie 提供浏览器 Cookie，或安装 playwright 后加 --browser。\n"
                    f"已取到封面：{meta.get('thumbnail_url')}\n"
                    + ("已尝试: " + "; ".join(errors) if errors else "")
                )

        assert item is not None
        resolved = Resolved(
            platform="tiktok",
            video_id=item["id"] or target.video_id,
            title=item["desc"],
            author=item["author"],
            cover=item["cover"],
            duration=item["duration"],
            source_url=url,
            strategy=strategy,
        )

        seen = set()
        for idx, rend in enumerate(item["renditions"][:3]):
            if rend["url"] in seen:
                continue
            seen.add(rend["url"])
            label = "MP4 HD" if idx == 0 else f"MP4 [{idx}]"
            resolved.videos.append(MediaLink(
                kind="video", label=label, url=rend["url"],
                quality=_quality_label(rend), referer=REFERER, ext="mp4",
                width=int(rend.get("width") or 0), height=int(rend.get("height") or 0)))

        if item["play_addr"] and item["play_addr"] not in seen:
            seen.add(item["play_addr"])
            resolved.videos.append(MediaLink(
                kind="video", label="MP4 无水印", url=item["play_addr"],
                referer=REFERER, ext="mp4"))

        if item["audio"]:
            resolved.audios.append(MediaLink(
                kind="audio", label="MP3 原声", url=item["audio"], referer=REFERER, ext="mp3"))
        return resolved

    def _oembed(self, target: Target) -> Optional[Dict[str, Any]]:
        """Public metadata endpoint – works without any cookie."""
        url = target.url or target.share_url
        if not url:
            return None
        try:
            resp = self.client.get(
                f"https://www.tiktok.com/oembed?url={urllib.parse.quote(url, safe='')}",
                headers={"Accept": "application/json"},
                retries=1)
            return resp.json()
        except Exception:
            return None


def _quality_label(rend: Dict[str, Any]) -> str:
    gear = str(rend.get("gear") or "")
    for token in ("2160", "1440", "1080", "720", "540", "480"):
        if token in gear:
            return f"{token}p"
    h = int(rend.get("height") or 0)
    if h:
        return f"{h}p"
    return ""


__all__ = ["TikTokResolver", "parse_page", "extract_item"]
