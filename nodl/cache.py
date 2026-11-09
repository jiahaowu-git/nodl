"""Local cache of Douyin/TikTok content identifiers.

Why this exists
---------------
The public third-party resolvers (``tikwm``, the ``ajaxsearch`` provider …)
are consumables: they change their API, put a captcha in front of it, or shut
down.  But they only ever hand us one piece of information that actually
matters — the **``uri``**:

    aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi0000d6ojganog65rfsorvlkg

That ``uri`` is a *permanent* content identifier.  It never expires, and the
platform's own playback endpoint will happily turn it into a **fresh** CDN url
(including a brand new signature) at any time, for free, forever.

So the strategy is:

1. whenever we resolve a video, remember its ``uri`` locally;
2. whenever we are asked to resolve a video we have seen before, skip every
   third-party source and go straight to the platform endpoint.

After that, the third-party sources are only needed for videos we have
**never** seen before.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

DEFAULT_DIR = os.environ.get("NODL_HOME") or os.path.join(os.path.expanduser("~"), ".nodl")
CACHE_FILE = os.path.join(DEFAULT_DIR, "uri_cache.json")

# aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi...  ->  v1e00fgi...
URI_IN_PLAY_RE = re.compile(r"[?&]video_id=(v[0-9a-zA-Z]+)")
# douyin play_addr.uri looks like v1e00fgi0000d6ojganog65rfsorvlkg
URI_RE = re.compile(r"^v[0-9a-zA-Z]{16,32}$")


def uri_from_play_url(url: str) -> Optional[str]:
    """Pull the ``uri`` out of an app-playback url."""
    if not url:
        return None
    m = URI_IN_PLAY_RE.search(url)
    return m.group(1) if m else None


class UriCache:
    """A tiny JSON file cache mapping ``platform:id`` -> ``uri`` + metadata."""

    def __init__(self, path: Optional[str] = None, autosave: bool = True) -> None:
        self.path = path or CACHE_FILE
        self.autosave = autosave
        self.data: Dict[str, Dict[str, Any]] = {}
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                self.data = raw.get("items", raw) if isinstance(raw.get("items"), dict) else raw
        except Exception:
            self.data = {}

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".",
                                       prefix=".cache-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "items": self.data}, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # -- api -------------------------------------------------------------
    @staticmethod
    def key(platform: str, video_id: str) -> str:
        return f"{platform}:{video_id}"

    def get(self, platform: str, video_id: str) -> Optional[Dict[str, Any]]:
        if not video_id:
            return None
        return self.data.get(self.key(platform, video_id))

    def get_by_uri(self, uri: str) -> Optional[Dict[str, Any]]:
        if not uri:
            return None
        for item in self.data.values():
            if item.get("uri") == uri:
                return item
        return None

    def put(self, platform: str, video_id: str, uri: str, **extra: Any) -> None:
        if not (video_id and uri):
            return
        k = self.key(platform, video_id)
        row = self.data.get(k, {})
        row.update({"uri": uri, "platform": platform, "video_id": video_id,
                    "ts": int(time.time())})
        row.update({k2: v for k2, v in extra.items() if v is not None})
        self.data[k] = row
        if self.autosave:
            self.save()

    # -- last known good direct urls -------------------------------------
    def put_url(self, platform: str, video_id: str, url: str, **extra: Any) -> None:
        """Remember a CDN url we resolved earlier.

        CDN urls carry a signed ``ft=`` parameter and therefore expire after a
        few hours.  We still keep them: if every source is dead we can hand
        back the last known url, which often still works for a while.
        """
        if not (video_id and url):
            return
        k = self.key(platform, video_id)
        row = self.data.setdefault(k, {"platform": platform, "video_id": video_id,
                                       "ts": int(time.time())})
        urls = row.setdefault("urls", [])
        if url not in urls:
            urls.insert(0, url)
            del urls[5:]
        row["urls_ts"] = int(time.time())
        row.update({k2: v for k2, v in extra.items() if v is not None and not isinstance(v, list)})
        self.data[k] = row
        if self.autosave:
            self.save()

    def last_urls(self, platform: str, video_id: str,
                  max_age: int = 6 * 3600) -> List[str]:
        """Recently seen direct urls for a video (newest first)."""
        row = self.get(platform, video_id)
        if not row:
            return []
        if max_age and (int(time.time()) - int(row.get("urls_ts") or 0)) > max_age:
            return []
        return list(row.get("urls") or [])

    def remember(self, platform: str, video_id: str, urls: List[str], **extra: Any) -> Optional[str]:
        """Scan a list of urls for an app-playback url and cache its ``uri``."""
        for u in urls or []:
            uri = uri_from_play_url(u)
            if uri:
                self.put(platform, video_id, uri, **extra)
                return uri
        return None

    def all(self) -> List[Dict[str, Any]]:
        return sorted(self.data.values(), key=lambda r: r.get("ts", 0), reverse=True)

    def __len__(self) -> int:
        return len(self.data)


__all__ = ["UriCache", "uri_from_play_url", "CACHE_FILE", "DEFAULT_DIR"]
