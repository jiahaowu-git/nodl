"""Douyin resolver.

How the no-watermark trick actually works
-----------------------------------------
Douyin stores two copies of every video:

``video.play_addr``
    the *clean* original stream, addressed by a short ``uri`` like
    ``v1e00fgi0000d6ojganog65rfsorvlkg``.
``video.download_addr``
    the copy the "保存本地" button serves, with the Douyin logo and the
    creator id burned into the pixels.

``aweme.snssdk.com/aweme/v1/play/?video_id=<uri>`` is the endpoint the
*mobile app* uses to play a video. It answers with a ``302`` to the CDN host
that holds the clean master file. That redirect is the whole trick: ask the
app's own playback endpoint for the stream and you get the clean file.

The only hard part is getting hold of ``uri`` in the first place, because
``www.douyin.com`` now guards the detail endpoint with the Argus signer plus
a captcha challenge for anonymous traffic. We therefore try, in order:

1. the mobile share page (``www.iesdouyin.com/share/video/<id>/``) which
   still server-renders the item for a lot of videos,
2. the web detail endpoint when the user supplies their own browser cookie,
3. a real Chromium session that borrows the page's own signed request
   (see :mod:`nodl.browser`).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .extract import Target
from .httpclient import UA_MOBILE, Client, HTTPError
from .jsonutil import find_dict_containing, find_first, normalise_url, pick_quality
from .models import MediaLink, Resolved

REFERER = "https://www.douyin.com/"
SHARE_REFERER = "https://www.iesdouyin.com/"

# The mobile-app playback endpoint. 302 -> clean CDN url.
PLAY_ENDPOINT = "https://aweme.snssdk.com/aweme/v1/play/"

ROUTER_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{)", re.S)


def play_url_for_uri(uri: str, ratio: str = "1080") -> str:
    """Build the app playback url that redirects to the clean master file."""
    return (
        f"{PLAY_ENDPOINT}?video_id={uri}&line=0&ratio={ratio}&media_type=4"
        "&vr_type=0&improve_bitrate=0&is_play_url=1&is_support_h265=0"
    )


def _router_data(html: str) -> Optional[Dict[str, Any]]:
    """Extract the JSON blob the share page assigns to ``window._ROUTER_DATA``."""
    m = ROUTER_RE.search(html)
    if not m:
        return None
    start = m.start(1)
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
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
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _unwrap_share_item(router: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Find the aweme item inside the share page's router payload."""
    hit = find_dict_containing(router, ("video", "author"))
    if hit:
        return hit
    item = find_dict_containing(router, ("play_addr",))
    if item:
        return item
    res = find_dict_containing(router, ("item_list",))
    if res:
        for p, v in _iter(res):
            if isinstance(v, dict) and "video" in v:
                return v
    return None


def _iter(obj: Any):
    from .jsonutil import walk
    return walk(obj)


def extract_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the interesting bits out of an aweme item."""
    video = item.get("video") or {}
    music = item.get("music") or {}
    author = item.get("author") or {}

    uri = ""
    play_addr = video.get("play_addr") or {}
    if isinstance(play_addr, dict):
        uri = play_addr.get("uri") or ""

    url_lists: List[str] = []
    for key in ("play_addr", "download_addr", "play_addr_h265"):
        addr = video.get(key) or {}
        if isinstance(addr, dict):
            for u in addr.get("url_list") or []:
                u = normalise_url(u)
                if u and u not in url_lists:
                    url_lists.append(u)

    # bit_rate[] carries one entry per rendition -> our MP4 [1]/[2]/HD ladder
    renditions: List[Dict[str, Any]] = []
    for br in video.get("bit_rate") or []:
        if not isinstance(br, dict):
            continue
        addr = br.get("play_addr") or {}
        urls = [normalise_url(u) for u in (addr.get("url_list") or [])]
        urls = [u for u in urls if u]
        if not urls:
            continue
        renditions.append({
            "url": urls[0],
            "urls": urls,
            "bit_rate": br.get("bit_rate") or 0,
            "gear_name": br.get("gear_name") or "",
            "height": br.get("play_addr", {}).get("height") or br.get("height") or 0,
            "quality_type": br.get("quality_type") or "",
        })

    audio_urls: List[str] = []
    play_url = music.get("play_url") or {}
    if isinstance(play_url, dict):
        audio_urls = [normalise_url(u) for u in (play_url.get("url_list") or [])]
    if not audio_urls and isinstance(music.get("play_url"), str):
        audio_urls = [normalise_url(music["play_url"])]

    cover = ""
    for key in ("cover", "origin_cover", "dynamic_cover"):
        c = video.get(key) or {}
        if isinstance(c, dict) and c.get("url_list"):
            cover = normalise_url(c["url_list"][0])
            break
        if isinstance(c, str):
            cover = normalise_url(c)
            break

    return {
        "aweme_id": str(item.get("aweme_id") or ""),
        "desc": item.get("desc") or "",
        "duration": int(item.get("duration") or video.get("duration") or 0),
        "uri": uri,
        "url_lists": url_lists,
        "renditions": pick_quality(renditions),
        "audio_urls": [u for u in audio_urls if u],
        "music_id": str(music.get("id") or music.get("mid") or ""),
        "cover": cover,
        "author": author.get("nickname") or "",
        "author_id": str(author.get("unique_id") or author.get("short_id") or ""),
        "images": item.get("images") or item.get("image_post_info", {}).get("images") or [],
    }


class DouyinResolver:
    """Resolve a Douyin link into downloadable media links."""

    def __init__(self, client: Client, cookie: Optional[str] = None) -> None:
        self.client = client
        self.cookie = cookie

    # -- strategies ------------------------------------------------------
    def resolve(self, target: Target, use_browser: bool = False) -> Resolved:
        errors: List[str] = []

        item = None
        strategy = ""

        if target.video_id:
            item = self._from_web_detail(target.video_id)
            if item:
                strategy = "web-detail"
        if not item and target.video_id:
            item = self._from_share_page(target.video_id)
            if item:
                strategy = "share-page"
        if not item and use_browser:
            from .browser import browser_detail
            item = browser_detail(target.video_id, cookie=self.cookie)
            if item:
                strategy = "browser"
        if not item:
            raise RuntimeError(
                "抖音接口当前要求登录态 / 签名（Argus + 验证码网关）。\n"
                "解决办法：用 --cookie 提供浏览器里的抖音 Cookie，或安装 playwright "
                "后加 --browser 让真实浏览器去取。\n"
                + ("已尝试: " + "; ".join(errors) if errors else "")
            )

        info = extract_item(item)
        resolved = Resolved(
            platform="douyin",
            video_id=info["aweme_id"] or target.video_id,
            title=info["desc"],
            author=info["author"],
            cover=info["cover"],
            duration=info["duration"],
            source_url=target.page_url,
            strategy=strategy,
            uri=info["uri"],
        )

        # 1) the rendition ladder straight from bit_rate[]
        seen = set()
        for idx, rend in enumerate(info["renditions"][:3]):
            url = rend["url"]
            if url in seen:
                continue
            seen.add(url)
            label = "MP4 HD" if idx == 0 else f"MP4 [{idx}]"
            resolved.videos.append(MediaLink(
                kind="video",
                label=label,
                url=url,
                quality=_quality_label(rend),
                referer=REFERER,
                ext="mp4",
                width=int(rend.get("height") or 0),
            ))

        # 2) the clean master via the app playback endpoint
        if info["uri"]:
            clean = self._resolve_play_redirect(info["uri"])
            if clean and clean not in seen:
                seen.add(clean)
                resolved.videos.append(MediaLink(
                    kind="video",
                    label="MP4 无水印原片",
                    url=clean,
                    quality="1080p",
                    referer=REFERER,
                    ext="mp4",
                ))

        # 3) whatever else we found
        for url in info["url_lists"]:
            if url in seen:
                continue
            seen.add(url)
            resolved.videos.append(MediaLink(
                kind="video", label="MP4 (备用源)", url=url, referer=REFERER, ext="mp4"))

        for url in info["audio_urls"]:
            resolved.audios.append(MediaLink(
                kind="audio", label="MP3 原声", url=url, referer=REFERER, ext="mp3"))

        # 4) image posts
        for img in info["images"] or []:
            urls = []
            if isinstance(img, dict):
                urls = [normalise_url(u) for u in (img.get("url_list") or [])]
            for url in urls:
                resolved.images.append(MediaLink(
                    kind="image", label="图片", url=url, referer=REFERER, ext="jpg"))
        return resolved

    # -- internals --------------------------------------------------------
    def _from_web_detail(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        url = (
            "https://www.douyin.com/aweme/v1/web/aweme/detail/"
            f"?aweme_id={aweme_id}&device_platform=webapp&aid=6383&channel=channel_pc_web"
        )
        try:
            resp = self.client.get(url, referer=REFERER, headers={"Cookie": self.cookie} if self.cookie else None)
            data = resp.json()
        except Exception:
            return None
        item = data.get("aweme_detail") if isinstance(data, dict) else None
        if item:
            return item
        return None

    def _from_share_page(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        url = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
        try:
            resp = self.client.get(url, headers={"User-Agent": UA_MOBILE}, referer=SHARE_REFERER)
        except Exception:
            return None
        router = _router_data(resp.text)
        if not router:
            return None
        item = _unwrap_share_item(router)
        if not item:
            return None
        return item

    def _resolve_play_redirect(self, uri: str) -> Optional[str]:
        """Follow the app playback endpoint and return the clean CDN url."""
        url = play_url_for_uri(uri)
        try:
            resp = self.client.get(url, headers={"User-Agent": UA_MOBILE}, referer=REFERER,
                                   allow_redirects=False)
        except HTTPError:
            return None
        except Exception:
            return None
        loc = resp.headers.get("Location") or resp.headers.get("location")
        return normalise_url(loc) if loc else None


def _quality_label(rend: Dict[str, Any]) -> str:
    gear = str(rend.get("gear_name") or "")
    for token in ("2160", "1440", "1080", "720", "540", "480"):
        if token in gear:
            return f"{token}p"
    height = int(rend.get("height") or 0)
    if height:
        return f"{height}p"
    bitrate = int(rend.get("bit_rate") or 0)
    if bitrate:
        return f"{bitrate // 1000}k"
    return ""


__all__ = ["DouyinResolver", "extract_item", "play_url_for_uri"]
