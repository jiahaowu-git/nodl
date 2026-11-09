"""自检脚本：离线夹具 + 在线连通性，逐项验证 nodl 各层。

    python3 selftest.py          # 全部
    python3 selftest.py --offline  # 只跑离线部分

离线部分用一份按 TikTok / 抖音真实结构构造的 JSON 夹具，
这样即使当前网络访问不到 tiktok.com 也能验证解析逻辑。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nodl.douyin import extract_item, play_url_for_uri  # noqa: E402
from nodl.cache import UriCache, uri_from_play_url  # noqa: E402
from nodl.providers import decode_jwt_payload  # noqa: E402
from nodl.extract import find_url, is_direct_media, is_uri  # noqa: E402
from nodl.jsonutil import find_dict_containing, pick_quality  # noqa: E402
from nodl.tiktok import extract_item as tk_extract, parse_page  # noqa: E402

OK, FAIL = "\033[32m✔\033[0m", "\033[31m✘\033[0m"
results = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  {OK if cond else FAIL} {name}" + (f"  — {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- fixtures
TIKTOK_PAGE = """<!DOCTYPE html><html><head>
<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">%s</script>
</head><body></body></html>"""

TIKTOK_PAYLOAD = {
    "__DEFAULT_SCOPE__": {
        "webapp.video-detail": {
            "itemInfo": {
                "itemStruct": {
                    "id": "6718335390845095173",
                    "desc": "Scramble up ur name #foryoupage",
                    "createTime": "1700000000",
                    "author": {"id": "MS4wLjABAAA", "uniqueId": "scout2015",
                               "nickname": "Scout", "avatarLarger": "https://p16-sign.tiktokcdn.com/a.jpeg"},
                    "music": {"id": "7300000000000000000", "title": "original sound",
                              "authorName": "Scout",
                              "playUrl": "https://sf16-ies-music.tiktokcdn.com/abc123.mp3"},
                    "video": {
                        "id": "6718335390845095173",
                        "duration": 9,
                        "cover": "https://p16-sign-va.tiktokcdn.com/cover.jpeg",
                        "originCover": "https://p16-sign-va.tiktokcdn.com/origin.jpeg",
                        "playAddr": "https://v16-webapp.tiktokcdn.com/clean.mp4?a=1",
                        "downloadAddr": "https://v16-web.tiktokcdn.com/watermarked.mp4?a=1",
                        "bitrateInfo": {"bitrate": [
                            {"GearName": "normal_720_1", "QualityType": "HD", "Bitrate": 1200000,
                             "PlayAddr": "https://v16-webapp.tiktokcdn.com/720.mp4", "Width": 720, "Height": 1280},
                            {"GearName": "normal_1080_1", "QualityType": "HD", "Bitrate": 2800000,
                             "PlayAddr": "https://v16-webapp.tiktokcdn.com/1080.mp4", "Width": 1080, "Height": 1920},
                        ]},
                    },
                }
            }
        }
    }
}

DOUYIN_ITEM = {
    "aweme_id": "7615930241370348219",
    "desc": "夜宵简单吃个炭烤牛肋排",
    "duration": 12,
    "author": {"nickname": "某吃货", "unique_id": "chihuo"},
    "music": {"id": 7615930215939820347, "play_url": {"url_list": [
        "https://lf3-music-east.douyinstatic.com/obj/ies-music-hj/7615930215939820347.mp3"]}},
    "video": {
        "cover": {"url_list": ["https://p5.douyinpic.com/cover.webp"]},
        "play_addr": {"uri": "v1e00fgi0000d6ojganog65rfsorvlkg",
                      "url_list": ["https://v26-cold.douyinvod.com/clean.mp4?a=1128"]},
        "download_addr": {"url_list": ["https://v26-cold.douyinvod.com/watermarked.mp4"]},
        "bit_rate": [
            {"bit_rate": 4083, "gear_name": "adapt_lowest_720_1",
             "play_addr": {"url_list": ["https://v5.zjcdn.com/720.mp4"], "height": 1280}},
            {"bit_rate": 8000, "gear_name": "normal_1080_1",
             "play_addr": {"url_list": ["https://v5.zjcdn.com/1080.mp4"], "height": 1920}},
        ],
    },
}


def offline_tests() -> None:
    print("\n[离线] 链接解析")
    check("从分享文案里抽出 URL",
          find_url("7.86 复制打开抖音，看看这个 https://v.douyin.com/iQxAbC12/ 很好看") == "https://v.douyin.com/iQxAbC12/")
    check("识别 Douyin uri", is_uri("v1e00fgi0000d6ojganog65rfsorvlkg"))
    check("识别 CDN 直链",
          is_direct_media("https://v26-cold.douyinvod.com/abc/video/tos/x.mp4?a=1"))

    print("\n[离线] TikTok 页面解析")
    html = TIKTOK_PAGE % json.dumps(TIKTOK_PAYLOAD)
    payload = parse_page(html)
    check("抽出 __UNIVERSAL_DATA_FOR_REHYDRATION__", isinstance(payload, dict))
    item = tk_extract(payload) if payload else None
    check("定位 itemStruct", item is not None)
    if item:
        check("拿到干净 playAddr", item["play_addr"].endswith("clean.mp4?a=1"), item["play_addr"])
        check("不把 downloadAddr 当主源", "watermarked" not in item["play_addr"])
        check("拿到音频直链", item["audio"].endswith(".mp3"), item["audio"])
        check("清晰度按高到低排序",
              [r["height"] for r in item["renditions"]] == [1920, 1280],
              str([r["height"] for r in item["renditions"]]))
        check("标题/作者解析", item["desc"].startswith("Scramble") and item["author"] == "scout2015")

    print("\n[离线] 抖音作品详情解析")
    info = extract_item(DOUYIN_ITEM)
    check("拿到 play_addr.uri", info["uri"] == "v1e00fgi0000d6ojganog65rfsorvlkg", info["uri"])
    check("拿到音乐 mp3", info["audio_urls"] and info["audio_urls"][0].endswith(".mp3"))
    check("拿到封面", info["cover"].endswith("cover.webp"))
    check("bit_rate 生成清晰度阶梯", len(info["renditions"]) == 2)
    check("阶梯按高到低",
          info["renditions"][0]["gear_name"].endswith("1080_1"),
          str(info["renditions"][0]["gear_name"]))
    check("play 接口 URL 拼装正确",
          play_url_for_uri(info["uri"]).startswith(
              "https://aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi0000d6ojganog65rfsorvlkg"))

    print("\n[离线] 公共解析源 / JWT 解码")
    jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
           "eyJ1cmwiOiJodHRwczovL2V4YW1wbGUuY29tL2EuY21wNCJ9."
           "c2lnbmF0dXJl")
    payload = decode_jwt_payload(jwt)
    check("解出 JWT payload", payload is not None and "url" in payload, str(payload))
    check("坏 JWT 不炸", decode_jwt_payload("not.a.jwt") is None)

    print("\n[离线] uri 缓存（第三方源全挂时的救命稻草）")
    import tempfile, os as _os
    tmp = _os.path.join(tempfile.mkdtemp(), "cache.json")
    c = UriCache(path=tmp)
    play = ("https://aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi0000d6ojganog65rfsorvlkg"
            "&line=0&ratio=1080")
    check("从播放接口 URL 里提取 uri",
          uri_from_play_url(play) == "v1e00fgi0000d6ojganog65rfsorvlkg",
          str(uri_from_play_url(play)))
    c.put("douyin", "123456", "v1e00fgi0000d6ojganog65rfsorvlkg", title="t")
    check("写入并读回 uri", (c.get("douyin", "123456") or {}).get("uri", "").startswith("v1e00"))
    c.put_url("douyin", "123456", "https://cdn.example/a.mp4")
    check("保存直链兜底", bool(c.last_urls("douyin", "123456")))
    check("过期直链不返回", c.last_urls("douyin", "123456", max_age=-1) == [])
    c2 = UriCache(path=tmp)
    check("落盘后重载仍在", (c2.get("douyin", "123456") or {}).get("uri", "").startswith("v1e00"))

    print("\n[离线] JSON 工具")
    hit = find_dict_containing(TIKTOK_PAYLOAD, ("playAddr",))
    check("递归查找任意层级字段", hit is not None and "playAddr" in hit,
          str(list(hit.keys())) if hit else "None")
    ordered = pick_quality([{"bit_rate": 100}, {"bit_rate": 900, "gear_name": "normal_1080_1"}])
    check("清晰度排序", ordered[0]["bit_rate"] == 900)


def online_tests() -> None:
    print("\n[在线] 缓存命中时不再请求第三方")
    try:
        import tempfile, os as _os
        from nodl.cache import UriCache
        from nodl.resolver import Resolver
        tmp = _os.path.join(tempfile.mkdtemp(), "cache.json")
        cache = UriCache(path=tmp)
        cache.put("douyin", "7661509500746108194", "v1e00fgi0000d6ojganog65rfsorvlkg")
        res = Resolver(sources="first-party", cache=cache).resolve(
            "https://www.douyin.com/video/7661509500746108194")
        check("命中 uri 缓存", res.strategy == "uri-cache", res.strategy)
        check("缓存路径也能拿到直链", bool(res.best_video() and res.best_video().url.startswith("http")))
    except Exception as exc:
        check("缓存链路", False, str(exc))

    print("\n[在线] 公共解析源（抖音完整链接 -> 直链）")
    try:
        from nodl.resolver import Resolver
        res = Resolver(sources="ajaxsearch").resolve(
            "https://www.douyin.com/video/7661509500746108194")
        check("ajaxsearch 解析出视频直链", len(res.videos) > 0)
        check("第一条是 HD", bool(res.videos) and res.videos[0].label == "MP4 HD",
              res.videos[0].label if res.videos else "")
        check("直链不是接口地址",
              bool(res.videos) and "aweme.snssdk.com/aweme/v1/play" not in res.videos[0].url)
        check("解析出独立音轨", len(res.audios) > 0)
    except Exception as exc:
        check("ajaxsearch 链路", False, str(exc))

    print("\n[在线] 抖音 App 播放接口（无水印原片）")
    uri = "v1e00fgi0000d6ojganog65rfsorvlkg"
    try:
        from nodl.resolver import Resolver
        res = Resolver().resolve(uri)
        link = res.best_video()
        check("uri 解析出直链", bool(link and link.url.startswith("https://")))
        check("直链指向抖音 CDN", bool(link and ("douyinvod" in link.url or "zjcdn" in link.url)),
              link.url if link else "")
        if link:
            import urllib.request
            req = urllib.request.Request(link.url, headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
                "Referer": "https://www.douyin.com/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                head = r.read(4096)
                check("直链可下载且是 mp4",
                      r.headers.get("Content-Type", "").startswith("video/mp4") and head[4:8] == b"ftyp",
                      r.headers.get("Content-Type", ""))
    except Exception as exc:
        check("在线链路", False, str(exc))


def main() -> int:
    offline_tests()
    if "--offline" not in sys.argv:
        online_tests()
    passed = sum(1 for _, c, _ in results if c)
    print(f"\n通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
