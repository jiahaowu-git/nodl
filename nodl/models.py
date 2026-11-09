"""Shared data model for a resolved media item."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MediaLink:
    """One downloadable artefact."""

    kind: str                 # "video" | "audio" | "image"
    label: str                # human readable, e.g. "MP4 [1]"
    url: str                  # direct CDN url
    quality: str = ""         # "1080p", "720p", ...
    referer: str = ""         # referer the CDN expects
    ext: str = "mp4"
    size: int = 0             # bytes if known
    width: int = 0
    height: int = 0

    @property
    def is_audio(self) -> bool:
        return self.kind == "audio"


@dataclass
class Resolved:
    """Everything we know about one video after resolution."""

    platform: str
    video_id: str = ""
    title: str = ""
    author: str = ""
    cover: str = ""
    duration: int = 0
    videos: List[MediaLink] = field(default_factory=list)
    audios: List[MediaLink] = field(default_factory=list)
    images: List[MediaLink] = field(default_factory=list)
    source_url: str = ""
    strategy: str = ""
    uri: str = ""                 # permanent content id (douyin play_addr.uri)
    notes: List[str] = field(default_factory=list)

    @property
    def all_links(self) -> List[MediaLink]:
        return self.videos + self.audios + self.images

    def best_video(self) -> Optional[MediaLink]:
        if not self.videos:
            return None
        return self.videos[0]

    def to_dict(self) -> Dict:
        return {
            "platform": self.platform,
            "video_id": self.video_id,
            "title": self.title,
            "author": self.author,
            "cover": self.cover,
            "duration": self.duration,
            "strategy": self.strategy,
            "uri": self.uri,
            "notes": self.notes,
            "videos": [vars(v) for v in self.videos],
            "audios": [vars(v) for v in self.audios],
            "images": [vars(v) for v in self.images],
        }


__all__ = ["MediaLink", "Resolved"]
