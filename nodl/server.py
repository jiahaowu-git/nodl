"""A tiny local web app that mirrors the TikVideo workflow.

    python -m nodl serve        # then open http://127.0.0.1:8787

Paste a link -> the server resolves it -> you get MP4 [1] / MP4 [2] / MP4 HD /
MP3 buttons, exactly like the site you were looking at.

The download endpoint proxies the bytes locally instead of redirecting, because
both CDNs check the ``Referer`` header and a plain 302 from the browser would
lose it.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .downloader import safe_name
from .httpclient import Client, human
from .models import MediaLink
from .resolver import Resolver

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

_client = Client(timeout=30)


class Handler(BaseHTTPRequestHandler):
    cookie: Optional[str] = None
    use_browser: bool = False
    sources: str = "auto"
    server_version = "nodl/1.0"

    # -- helpers ---------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        pass

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_html(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send_html(os.path.join(WEB_DIR, "index.html"))
            return
        if parsed.path == "/api/download":
            self._download(urllib.parse.parse_qs(parsed.query))
            return
        if parsed.path == "/api/proxy":
            self._proxy(urllib.parse.parse_qs(parsed.query))
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/api/resolve", "/api/ingest"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
            return
        if parsed.path == "/api/ingest":
            self._ingest(payload)
            return
        url = (payload.get("url") or "").strip()
        if not url:
            self._send_json({"ok": False, "error": "缺少 url 参数"}, 400)
            return
        try:
            source = (payload.get("source") or self.sources or "auto").strip()
            if source not in ("auto", "first-party", "tikwm", "ajaxsearch"):
                source = "auto"
            resolver = Resolver(cookie=self.cookie, use_browser=self.use_browser,
                                sources=source)
            res = resolver.resolve(url)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 200)
            return
        data = res.to_dict()
        data["ok"] = True
        self._send_json(data)

    def _ingest(self, payload: Dict[str, Any]) -> None:
        """Accept a media url captured by the user (bookmarklet / DevTools)."""
        url = (payload.get("url") or "").strip()
        if not url:
            self._send_json({"ok": False, "error": "缺少 url"}, 400)
            return
        candidates = payload.get("candidates") or []
        if isinstance(candidates, list):
            candidates = [c for c in candidates if isinstance(c, str)]
        title = (payload.get("title") or "").strip()
        try:
            from .extract import Target
            from .resolver import Resolver
            target = Target(platform="direct", url=url, original=url,
                            page_url=url, share_url=url)
            res = Resolver()._direct(target)
            res.title = title or res.title
            # remember it so a later resolve can reuse it
            vid = url.split("/")[-1].split("?")[0][:40]
            try:
                Resolver().cache.put_url("direct", vid, url, title=res.title)
            except Exception:
                pass
            data = res.to_dict()
            data["ok"] = True
            data["candidates"] = candidates
            self._send_json(data)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 200)

    def _proxy(self, qs: Dict[str, list]) -> None:
        """Fetch an image (cover) with the Referer the CDN expects."""
        url = (qs.get("u") or [""])[0]
        referer = (qs.get("r") or [""])[0]
        if not url:
            self.send_error(400, "missing u")
            return
        try:
            resp, _ = _client.stream(url, referer=referer or None, timeout=30)
        except Exception as exc:
            self.send_error(502, f"upstream failed: {exc}")
            return
        with resp:
            ctype = resp.headers.get("Content-Type") or "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            while True:
                chunk = resp.read(1 << 15)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _download(self, qs: Dict[str, list]) -> None:
        url = (qs.get("u") or [""])[0]
        name = (qs.get("n") or ["download.mp4"])[0]
        referer = (qs.get("r") or [""])[0]
        if not url:
            self.send_error(400, "missing u")
            return
        try:
            resp, _ = _client.stream(url, referer=referer or None, timeout=120)
        except Exception as exc:
            self.send_error(502, f"upstream failed: {exc}")
            return
        with resp:
            total = 0
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            ctype = resp.headers.get("Content-Type") or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(total))
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + urllib.parse.quote(safe_name(name)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                self.wfile.write(chunk)


def serve(host: str = "127.0.0.1", port: int = 8787,
          cookie: Optional[str] = None, use_browser: bool = False,
          sources: str = "auto") -> None:
    Handler.cookie = cookie
    Handler.use_browser = use_browser
    Handler.sources = sources
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"[错误] 无法绑定 {host}:{port} —— {exc}", flush=True)
        print(f"       端口可能已被占用，换一个即可，例如：", flush=True)
        print(f"       python -m nodl serve --port {port + 1}", flush=True)
        raise SystemExit(1) from exc
    print(f"本地无水印下载器已启动 -> http://{host}:{port}", flush=True)
    print("按 Ctrl+C 退出", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出", flush=True)
    finally:
        httpd.server_close()


__all__ = ["serve"]
