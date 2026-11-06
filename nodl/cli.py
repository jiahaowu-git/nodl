"""Command line interface.

    python -m nodl "https://www.douyin.com/video/7615930241370348219"
    python -m nodl "<链接>" --list          # 只解析，列出所有可用清晰度
    python -m nodl "<链接>" -o ~/Videos     # 下载全部
    python -m nodl "<链接>" --mp3           # 只下载音频
    python -m nodl serve --port 8787        # 起本地网页版
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .downloader import Downloader, safe_name
from .httpclient import Client, human
from .models import MediaLink, Resolved
from .resolver import Resolver


def _read_cookie(args: argparse.Namespace) -> Optional[str]:
    if args.cookie:
        return args.cookie.strip()
    if args.cookie_file:
        with open(args.cookie_file, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    env = os.environ.get("NODL_COOKIE")
    return env.strip() if env else None


def _print_result(res: Resolved) -> None:
    print(f"\n平台      : {res.platform}")
    print(f"标题      : {res.title or '(无)'}")
    print(f"作者      : {res.author or '(未知)'}")
    if res.duration:
        print(f"时长      : {res.duration}s")
    print(f"解析方式  : {res.strategy}")
    print(f"源页面    : {res.source_url}")
    print()
    for label, group in (("视频", res.videos), ("音频", res.audios), ("图片", res.images)):
        if not group:
            continue
        print(f"【{label}】")
        for i, m in enumerate(group, 1):
            size = f"  {human(m.size)}" if m.size else ""
            print(f"  {i}. {m.label:<14} {m.quality:<8} {m.ext:<4}{size}")
            print(f"     {m.url[:150]}")
    print()


def _download_all(res: Resolved, args: argparse.Namespace) -> List[str]:
    client = Client()
    dl = Downloader(client, outdir=args.output, quiet=args.quiet)
    stem = safe_name(res.title or res.video_id or res.platform, fallback=res.platform)
    author = safe_name(res.author, fallback="") if res.author else ""
    prefix = f"{author}_{stem}" if author else stem

    links: List[MediaLink] = []
    if args.mp3:
        links = list(res.audios)
    elif args.mp4:
        links = list(res.videos)
    else:
        links = list(res.videos) + list(res.audios)

    saved: List[str] = []
    for idx, link in enumerate(links, 1):
        tag = link.label.replace(" ", "").replace("[", "").replace("]", "")
        if len(links) > 1:
            tag = f"{tag}_{idx}"
        name = f"{prefix}_{tag}.{link.ext}"
        try:
            saved.append(dl.download(link, filename=name, overwrite=args.overwrite))
        except Exception as exc:
            print(f"下载失败 ({link.label}): {exc}", file=sys.stderr)
    return saved


def cmd_resolve(args: argparse.Namespace) -> int:
    resolver = Resolver(cookie=_read_cookie(args), use_browser=args.browser,
                        sources=args.source)
    try:
        res = resolver.resolve(args.url)
    except Exception as exc:
        print(f"\n解析失败: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_result(res)

    # --json / --list 只输出结果，不触发下载（保证输出是纯 JSON / 可复制）
    if args.list or args.json:
        return 0
    saved = _download_all(res, args)
    if saved:
        print(f"\n共保存 {len(saved)} 个文件到 {os.path.abspath(args.output)}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve
    serve(host=args.host, port=args.port, cookie=_read_cookie(args), use_browser=args.browser,
          sources=args.source)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nodl",
        description="本地无水印视频下载器（抖音 / TikTok）——纯标准库实现，无需 pip 安装",
    )
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("resolve", help="解析一个链接（默认命令）")
    r.add_argument("url")
    r.add_argument("-o", "--output", default="downloads")
    r.add_argument("--list", action="store_true", help="只列出解析结果，不下载")
    r.add_argument("--json", action="store_true", help="输出 JSON")
    r.add_argument("--mp3", action="store_true", help="只下载音频")
    r.add_argument("--mp4", action="store_true", help="只下载视频")
    r.add_argument("--overwrite", action="store_true")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--cookie", help="浏览器 Cookie 字符串")
    r.add_argument("--cookie-file", help="浏览器 Cookie 文件")
    r.add_argument("--browser", action="store_true", help="用本地 Chromium 解析（需 playwright）")
    r.add_argument("--source", default="auto",
                   choices=["auto", "first-party", "tikwm", "ajaxsearch"],
                   help="解析源：auto=先一方后公共（默认）| first-party=只用一方 | tikwm/ajaxsearch=公共接口")
    r.set_defaults(func=cmd_resolve)

    s = sub.add_parser("serve", help="启动本地网页版（复刻 TikVideo 的交互）")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--cookie", help="浏览器 Cookie 字符串")
    s.add_argument("--cookie-file", help="浏览器 Cookie 文件")
    s.add_argument("--browser", action="store_true")
    s.add_argument("--source", default="auto",
                   choices=["auto", "first-party", "tikwm", "ajaxsearch"])
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("resolve", "serve", "-h", "--help"):
        argv.insert(0, "resolve")
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
