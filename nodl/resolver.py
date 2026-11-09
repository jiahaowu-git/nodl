"""Platform dispatch: turn a pasted link into a :class:`Resolved` bundle."""

from __future__ import annotations

from typing import List, Optional

from .cache import UriCache, uri_from_play_url
from .douyin import DouyinResolver
from .extract import Target, normalise
from .httpclient import UA_MOBILE, Client
from .models import Resolved
from .tiktok import TikTokResolver


class Resolver:
    def __init__(self, cookie: Optional[str] = None, timeout: int = 20,
                 use_browser: bool = False, sources: str = "auto",
                 cache: Optional[UriCache] = None) -> None:
        self.client = Client(timeout=timeout)
        self.cookie = cookie
        self.use_browser = use_browser
        # "auto"      -> first-party, then the public fallbacks
        # "first-party"-> only the first-party strategies
        # "tikwm" / "ajaxsearch" -> only that provider
        self.sources = sources
        self.cache = cache if cache is not None else UriCache()

    def resolve(self, raw: str) -> Resolved:
        target = normalise(self.client, raw)
        if target.platform == "direct":
            return self._direct(target)
        if target.platform == "douyin-uri":
            return self._from_uri(target)

        errors: List[str] = []

        # --- 0) local uri cache ------------------------------------------
        # The uri never expires, so a video we have resolved before can be
        # downloaded straight from the platform endpoint without touching
        # any third party at all.
        cached = self._from_cache(target)
        if cached:
            return cached

        # --- 1) first-party strategies ---------------------------------
        if self.sources in ("auto", "first-party"):
            try:
                if target.platform == "douyin":
                    res = DouyinResolver(self.client, self.cookie).resolve(
                        target, use_browser=self.use_browser)
                else:
                    res = TikTokResolver(self.client, self.cookie).resolve(
                        target, use_browser=self.use_browser)
                self._remember(res)
                return res
            except Exception as exc:
                errors.append(f"first-party: {exc}")

        # --- 2) public fallback resolvers ------------------------------
        if self.sources in ("auto", "tikwm", "ajaxsearch"):
            from .providers import PROVIDERS
            order = ["tikwm", "ajaxsearch"] if self.sources == "auto" else [self.sources]
            for name in order:
                fn = PROVIDERS.get(name)
                if not fn:
                    continue
                try:
                    res = fn(self.client, target.url, target.referer)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    continue
                if res:
                    self._upgrade_play_urls(res)
                    self._remember(res)
                    return res
                errors.append(f"{name}: 没拿到可用数据")

        # --- 3) last known good (cache) ---------------------------------
        cached = self._from_last_urls(target)
        if cached:
            return cached

        hint = ("可以加 --cookie <浏览器Cookie> 或 --browser（需 playwright）走一方接口；"
                "或用 --source 指定公共解析源")
        raise RuntimeError("解析失败。\n  - " + "\n  - ".join(errors) + "\n提示：" + hint)

    # -- uri cache -------------------------------------------------------
    def _from_last_urls(self, target: Target) -> Optional[Resolved]:
        """Last resort: hand back a CDN url we saw recently (may still work)."""
        if not target.video_id:
            return None
        urls = self.cache.last_urls(target.platform, target.video_id)
        if not urls:
            return None
        from .models import MediaLink
        return Resolved(
            platform=target.platform,
            video_id=target.video_id,
            title="（缓存的直链）",
            source_url=urls[0],
            strategy="url-cache",
            videos=[MediaLink(kind="video", label="MP4 缓存直链", url=u,
                              referer=target.referer, ext="mp4") for u in urls[:2]],
            notes=["所有在线源都失败，返回本地缓存的直链；CDN 签名过期后需重新解析"],
        )
    def _from_cache(self, target: Target) -> Optional[Resolved]:
        """Rebuild a download straight from a previously cached ``uri``."""
        if target.platform != "douyin" or not target.video_id:
            return None
        row = self.cache.get("douyin", target.video_id)
        if not row or not row.get("uri"):
            return None
        res = self._from_uri(Target(platform="douyin-uri", url=row["uri"],
                                    original=row["uri"], video_id=row["uri"]))
        res.video_id = target.video_id
        res.title = row.get("title") or res.title
        res.author = row.get("author") or ""
        res.strategy = "uri-cache"
        res.notes.append(f"命中本地 uri 缓存（{row.get('ts')}），未请求任何第三方接口")
        return res

    def _remember(self, res: Resolved) -> None:
        """Persist the ``uri`` so future downloads never need a third party."""
        if not res.video_id:
            return
        platform = res.platform or "douyin"
        urls = [m.url for m in res.videos] + [m.url for m in res.audios]
        uri = getattr(res, "uri", "") or ""
        if not uri:
            for u in urls:
                uri = uri_from_play_url(u)
                if uri:
                    break
        if uri:
            self.cache.put(platform, res.video_id, uri,
                           title=res.title, author=res.author)
        # remember the direct urls too – they are the fallback of the fallback
        for u in [m.url for m in res.videos if m.kind == "video"]:
            self.cache.put_url(platform, res.video_id, u,
                               title=res.title, author=res.author)

    # -- post processing -------------------------------------------------
    def _upgrade_play_urls(self, res: Resolved) -> None:
        """Follow the app playback 302 so we hand out the real 1080p CDN url.

        The app playback endpoint answers with ``ratio=1080`` and therefore
        points at the HD master, while the plain CDN links that sit in the
        page markup are usually the 720p rendition.  So: upgrade first, drop
        duplicates, then put the HD one on top.
        """
        upgraded: List = []
        for link in res.videos:
            if "aweme.snssdk.com/aweme/v1/play/" in link.url:
                uri = uri_from_play_url(link.url)
                if uri and not getattr(res, "uri", ""):
                    res.uri = uri
                final = self._follow_redirect(link.url)
                if final:
                    link.url = final
                    link.label = "MP4 HD"
                    link.quality = "1080p"
            upgraded.append(link)

        # de-duplicate by full url, then by path (query strings often differ)
        seen_url, seen_path, unique = set(), set(), []
        for link in upgraded:
            if link.url in seen_url:
                continue
            path = link.url.split("?")[0]
            if path in seen_path:
                continue
            seen_url.add(link.url)
            seen_path.add(path)
            unique.append(link)

        # 码率越高画质越好（CDN 用 br= 参数标码率）
        def bitrate(m) -> int:
            q = m.url.split("?", 1)[1] if "?" in m.url else ""
            for part in q.split("&"):
                if part.startswith("br="):
                    try:
                        return int(part[3:])
                    except ValueError:
                        return 0
            return 0

        unique.sort(key=lambda m: (0 if m.label == "MP4 HD" else 1, -bitrate(m)))
        if unique and unique[0].label != "MP4 HD" and bitrate(unique[0]) > 0:
            unique[0].label = "MP4 HD"
            unique[0].quality = "1080p"
        res.videos = unique

    def _follow_redirect(self, url: str) -> Optional[str]:
        try:
            resp = self.client.get(url, headers={"User-Agent": UA_MOBILE},
                                   referer="https://www.douyin.com/",
                                   allow_redirects=False, retries=1)
        except Exception:
            return None
        loc = resp.headers.get("Location") or resp.headers.get("location")
        return loc or None

    # -- shortcuts -------------------------------------------------------
    def _direct(self, target) -> Resolved:
        """The user pasted a media url – hand it straight to the downloader."""
        from .models import MediaLink, Resolved
        ext = "mp4"
        low = target.url.lower()
        for e in ("mp3", "m4a", "aac", "m3u8", "webm", "mov"):
            if low.endswith(e):
                ext = e
                break
        referer = "https://www.douyin.com/" if "douyin" in target.url or "zjcdn" in target.url \
            else "https://www.tiktok.com/"
        return Resolved(
            platform="direct",
            title=target.url.rsplit("/", 1)[-1][:60],
            source_url=target.url,
            strategy="direct-url",
            videos=[MediaLink(kind="video" if ext != "mp3" else "audio",
                              label="MP4 直链" if ext != "mp3" else "MP3 直链",
                              url=target.url, referer=referer, ext=ext)],
        )

    def _from_uri(self, target) -> Resolved:
        """A bare Douyin uri -> ask the mobile playback endpoint for the file."""
        from .douyin import play_url_for_uri
        from .models import MediaLink, Resolved
        play = play_url_for_uri(target.video_id)
        try:
            resp = self.client.get(play, headers={"User-Agent": UA_MOBILE},
                                   referer="https://www.douyin.com/", allow_redirects=False)
            location = resp.headers.get("Location") or resp.headers.get("location")
        except Exception:
            location = None
        if not location:
            raise RuntimeError(f"无法用 uri 解析出直链（播放接口没有返回 302）：{target.video_id}")
        return Resolved(
            platform="douyin",
            video_id=target.video_id,
            title="uri 直达",
            source_url=play,
            strategy="uri->play-endpoint",
            videos=[MediaLink(kind="video", label="MP4 无水印原片", url=location,
                              quality="1080p", referer="https://www.douyin.com/", ext="mp4")],
        )


__all__ = ["Resolver"]
