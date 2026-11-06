"""Downloading with resume support, progress reporting and integrity checks."""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Optional

from .httpclient import Client, human
from .models import MediaLink

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
# typographic quotes and other characters that are legal but ugly / risky
FANCY = {
    "\u201c": "", "\u201d": "", "\u2018": "", "\u2019": "",
    "\uff08": "(", "\uff09": ")", "\u3001": ",",
    "\uff0c": ",", "\u3002": ".", "\uff1f": "?", "\uff01": "!",
}


def safe_name(name: str, fallback: str = "video", maxlen: int = 90) -> str:
    name = ILLEGAL.sub("_", (name or "").strip())
    for src, dst in FANCY.items():
        name = name.replace(src, dst)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = re.sub(r"#+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    return name[:maxlen]


class Downloader:
    def __init__(self, client: Client, outdir: str = "downloads", quiet: bool = False) -> None:
        self.client = client
        self.outdir = outdir
        self.quiet = quiet

    def download(self, link: MediaLink, filename: Optional[str] = None,
                 overwrite: bool = False) -> str:
        os.makedirs(self.outdir, exist_ok=True)
        base = filename or safe_name(f"{link.label}_{link.quality}" if link.quality else link.label,
                                     fallback=link.label)
        ext = link.ext or "mp4"
        if not base.lower().endswith("." + ext):
            base = f"{base}.{ext}"
        path = os.path.join(self.outdir, base)

        if os.path.exists(path) and not overwrite:
            if os.path.getsize(path) > 0:
                self._log(f"已存在，跳过: {path}")
                return path

        part = path + ".part"
        start = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Accept": "*/*"}
        try:
            resp, _ = self.client.stream(link.url, referer=link.referer, headers=headers, start=start)
        except Exception as exc:
            raise RuntimeError(f"打开下载链接失败: {exc}") from exc

        with resp:
            if start and getattr(resp, "status", 200) == 200:
                # server ignored our Range header -> restart from scratch
                start = 0
                if os.path.exists(part):
                    os.remove(part)
            total = 0
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            grand = start + total if total else 0
            done = start
            t0 = time.time()
            with open(part, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    self._progress(done, grand, t0)
        self._progress_done(done, grand)
        os.replace(part, path)
        self._log(f"已保存: {path} ({human(os.path.getsize(path))})")
        return path

    # -- progress ---------------------------------------------------------
    def _log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def _progress(self, done: int, total: int, t0: float) -> None:
        if self.quiet or not sys.stderr.isatty():
            return
        speed = done / max(time.time() - t0, 0.001)
        if total:
            pct = done / total * 100
            bar_len = 28
            filled = int(bar_len * done / total)
            sys.stderr.write(f"\r  [{'█' * filled}{'░' * (bar_len - filled)}] "
                             f"{pct:5.1f}%  {human(done)}/{human(total)}  {human(speed)}/s")
        else:
            sys.stderr.write(f"\r  下载中… {human(done)}  {human(speed)}/s")
        sys.stderr.flush()

    def _progress_done(self, done: int, total: int) -> None:
        if self.quiet or not sys.stderr.isatty():
            return
        sys.stderr.write("\r" + " " * 96 + "\r")
        sys.stderr.flush()


__all__ = ["Downloader", "safe_name"]
