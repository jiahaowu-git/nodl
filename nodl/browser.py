"""Optional Playwright helper.

Douyin and TikTok both wrap their detail endpoints in a signature + captcha
gate for anonymous clients. A real browser already solves both: the page's
own JavaScript signs its requests and passes the challenge. So we let Chromium
do the work and simply *listen* to the JSON that comes back.

This module is optional – install ``playwright`` and run ``playwright install
chromium`` to enable it.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:  # optional dependency
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None  # type: ignore

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserUnavailable(RuntimeError):
    pass


def available() -> bool:
    return sync_playwright is not None


def _find_chromium() -> Optional[str]:
    """Locate a chromium binary (playwright's own download, or the system one)."""
    env = os.environ.get("NODL_CHROMIUM") or os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    if env and os.path.exists(env):
        return env
    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/headless_shell"),
        "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def _cookie_list(cookie: Optional[str]) -> List[Dict[str, Any]]:
    if not cookie:
        return []
    out = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        out.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".douyin.com",
            "path": "/",
        })
    return out


def _launch(p):
    exe = _find_chromium()
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled",
            "--autoplay-policy=no-user-gesture-required"]
    if exe:
        try:
            return p.chromium.launch(args=args, executable_path=exe)
        except Exception:
            pass
    return p.chromium.launch(args=args)


def _new_context(p, cookie: Optional[str], mobile: bool = False):
    if mobile:
        ctx = p.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
                       "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
            device_scale_factor=3, locale="zh-CN")
    else:
        ctx = p.new_context(user_agent=UA_DESKTOP, viewport={"width": 1440, "height": 900},
                            locale="zh-CN")
    cookies = _cookie_list(cookie)
    if cookies:
        try:
            ctx.add_cookies(cookies)
        except Exception:
            pass
    return ctx


def browser_detail(aweme_id: str, cookie: Optional[str] = None, timeout: int = 45000) -> Optional[Dict[str, Any]]:
    """Open the Douyin video page and return the ``aweme_detail`` payload."""
    if not available():
        raise BrowserUnavailable("未安装 playwright：pip install playwright && playwright install chromium")
    captured: List[Dict[str, Any]] = []

    def on_response(resp):
        url = resp.url
        if "/aweme/v1/web/aweme/detail" not in url:
            return
        try:
            body = resp.text()
        except Exception:
            return
        if not body or not body.strip().startswith("{"):
            return
        try:
            data = json.loads(body)
        except Exception:
            return
        if isinstance(data, dict) and data.get("aweme_detail"):
            captured.append(data["aweme_detail"])

    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(p, cookie)
        page = ctx.new_page()
        page.on("response", on_response)
        try:
            page.goto(f"https://www.douyin.com/video/{aweme_id}",
                      timeout=timeout, wait_until="domcontentloaded")
            for _ in range(10):
                page.wait_for_timeout(2000)
                if captured:
                    break
                try:
                    page.mouse.wheel(0, 400)
                except Exception:
                    pass
            if not captured:
                # fallback: the page may embed the payload already
                payload = _universal_data(page)
                if payload:
                    from .tiktok import extract_item
                    item = extract_item(payload)
                    if item:
                        return {"video": {"play_addr": {"url_list": [item["play_addr"]]}},
                                "desc": item["desc"], "author": {"nickname": item["author"]},
                                "aweme_id": item["id"]}
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return captured[0] if captured else None


def browser_tiktok(url: str, cookie: Optional[str] = None, timeout: int = 45000) -> Optional[Dict[str, Any]]:
    """Open a TikTok page and return the item struct."""
    if not available():
        raise BrowserUnavailable("未安装 playwright：pip install playwright && playwright install chromium")
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(p, cookie)
        page = ctx.new_page()
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            payload = _universal_data(page)
            if payload:
                from .tiktok import extract_item
                item = extract_item(payload)
                if item:
                    return {
                        "id": item["id"],
                        "desc": item["desc"],
                        "duration": item["duration"],
                        "author": {"uniqueId": item["author"], "nickname": item["nickname"]},
                        "video": {
                            "playAddr": item["play_addr"],
                            "downloadAddr": item["download_addr"],
                            "cover": item["cover"],
                            "duration": item["duration"],
                            "bitrateInfo": {"bitrate": [
                                {"PlayAddr": r["url"], "GearName": r["gear"], "Bitrate": r["bitrate"],
                                 "Width": r["width"], "Height": r["height"], "QualityType": r["quality"]}
                                for r in item["renditions"]]},
                        },
                        "music": {"playUrl": item["audio"]},
                    }
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return None


def _universal_data(page) -> Optional[Dict[str, Any]]:
    try:
        raw = page.evaluate(
            "() => window.__UNIVERSAL_DATA_FOR_REHYDRATION__ "
            "|| (window.__UNIVERSAL_DATA_FOR_REHYDRATION__ = null)")
    except Exception:
        raw = None
    if isinstance(raw, dict) and raw:
        return raw
    try:
        text = page.evaluate(
            "() => { const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');"
            " return el ? el.textContent : null; }")
        if text:
            return json.loads(text)
    except Exception:
        pass
    return None


__all__ = ["browser_detail", "browser_tiktok", "available", "BrowserUnavailable"]
