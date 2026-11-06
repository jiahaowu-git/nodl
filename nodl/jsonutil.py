"""Recursive helpers to dig structured payloads apart.

Both Douyin and TikTok like to move their JSON around every couple of weeks.
Rather than hard-coding a deep path (and shipping a KeyError every other
Tuesday) we search the tree for the keys we care about.
"""

from __future__ import annotations

import html as _html
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "walk",
    "find_all",
    "find_first",
    "find_dict_containing",
    "pick_quality",
    "normalise_url",
]


def walk(obj: Any, path: str = "") -> Iterable[Tuple[str, Any]]:
    """Yield ``(json_path, value)`` for every node in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            yield p, v
            yield from walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{path}[{i}]"
            yield p, v
            yield from walk(v, p)


def find_all(obj: Any, key: str) -> List[Tuple[str, Any]]:
    return [(p, v) for p, v in walk(obj) if p.rsplit(".", 1)[-1].split("[")[0] == key]


def find_first(obj: Any, keys: Iterable[str]) -> Optional[Tuple[str, Any]]:
    """Return the first match for any of ``keys``, searching in key order."""
    for key in keys:
        hits = find_all(obj, key)
        if hits:
            return hits[0]
    return None


def find_dict_containing(obj: Any, required: Iterable[str]) -> Optional[Dict[str, Any]]:
    """Return the first dict that contains *all* ``required`` keys."""
    req = set(required)
    for _, v in walk(obj):
        if isinstance(v, dict) and req.issubset(v.keys()):
            return v
    return None


def pick_quality(candidates: List[Dict[str, Any]], height_keys=("height",)) -> List[Dict[str, Any]]:
    """Sort quality candidates (bitrate/height aware) from best to worst."""
    def score(item: Dict[str, Any]) -> Tuple[int, int]:
        height = 0
        for k in height_keys:
            if isinstance(item.get(k), int):
                height = item[k]
                break
        bitrate = item.get("bit_rate") or item.get("bitrate") or 0
        if not isinstance(bitrate, (int, float)):
            bitrate = 0
        gear = str(item.get("gear_name") or item.get("quality_type") or "")
        extra = 1 if ("1080" in gear or "2160" in gear) else 0
        return (height + extra * 10000, int(bitrate))

    return sorted(candidates, key=score, reverse=True)


def normalise_url(url: str) -> str:
    """Un-escape the ``\\u002F`` style escapes some CDNs put in their urls."""
    if not url:
        return url
    url = (
        url.replace("\\u002F", "/")
        .replace("\\u0026", "&")
        .replace("\\/", "/")
    )
    return _html.unescape(url)
