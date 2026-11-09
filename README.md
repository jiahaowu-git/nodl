# tikvideo.app 原理拆解 + 本地复刻（nodl）

> 一份「先逆向、再实现、再实测」的完整记录。所有结论都来自对 `tikvideo.app`
> 的线上抓包与实测，不是二手资料。

---

## 零、三分钟上手

```bat
:: Windows：双击 start.bat，或在命令行里
python -m nodl serve
:: 浏览器打开 http://127.0.0.1:8787，把链接粘进去点「下载」
```

或者直接命令行下载：

```bash
python3 -m nodl resolve "https://www.douyin.com/video/7661509500746108194" -o ~/Videos
```

**环境要求：只要 Python 3.8+，不需要 `pip install` 任何东西。**

| 文件 | 作用 |
|---|---|
| `README.md` | 你正在看的：原理拆解 + 使用说明（给人看） |
| `AGENTS.md` | 架构约定、扩展指引、质量门槛（给后续维护者/AI 助手看） |
| `selftest.py` | 40 项自检（31 离线 + 9 在线），改完代码先跑它 |
| `check.bat` | Windows 一键环境自检 |
| `start.bat` / `start.sh` | 一键启动本地网页版（可传端口号） |
| `snippets/capture.js` | 浏览器捕获片段，第三方源全挂时的兜底 |

---

## 目录

- [零、三分钟上手](#零三分钟上手)
- [一、tikvideo.app 是怎么做的](#一tikvideoapp-是怎么做的)
- [二、原理：为什么能拿到无水印高清](#二原理为什么能拿到无水印高清)
- [三、本地复刻：nodl](#三本地复刻nodl)
- [四、实测记录与平台拦截现状](#四实测记录与平台拦截现状)
- [五、常见问题](#五常见问题)
- [六、合规边界](#六合规边界)

---

## 一、tikvideo.app 是怎么做的

### 1.1 它其实只有两个后端接口

页面 HTML 里内联了一段全局变量，直接暴露了全部接口：

```js
var k_url_next="https://tikvideo.app/",
    k_url_check_task="https://tikvideo.app/api/ajaxConvert/checkTask",
    k_url_convert="https://s3.tik-cdn.com/api/json/convert",
    k_url_search="https://tikvideo.app/api/ajaxSearch",
    k_token="4b17129f…", k_exp="1792293921", k_page="mp4";
```

| 接口 | 作用 |
|---|---|
| `POST https://tikvideo.app/api/ajaxSearch` | **核心**：传链接，返回解析好的视频元数据 + 一段服务端渲染的 HTML 片段 |
| `POST https://s3.tik-cdn.com/api/json/convert` | 音视频转换（抽 MP3、转码），带 `token`/`exp` 签名 |
| `wss://s3.tik-cdn.com/sub/{jobId}` | 转换任务的进度推送（WebSocket） |
| `GET https://dl.snapcdn.app/get?token=<JWT>` | **签名下载代理**（下面细说） |

`/api/ajaxSearch` 实际并不强制校验 Cloudflare Turnstile —— 直接 POST
`q=<链接>&lang=en&cftoken=` 就能返回结果。

### 1.2 完整链路

```
浏览器
  │  ① POST /api/ajaxSearch  {q: 链接, lang, cftoken}
  ▼
tikvideo.app 后端 ──解析链接──► 拿到平台的无水印 CDN 直链
  │
  │  ② 返回 HTML 片段：
  │     <input type="hidden" id="TikTokId" value="7615930241370348219">
  │     <img src="https://p16-common-sign.tiktokcdn-us.com/…webp">
  │     <a href="https://dl.snapcdn.app/get?token=eyJhbGciOiJIUzI1NiIs…">下载 MP4 [1]</a>
  │     <a href="https://dl.snapcdn.app/get?token=…">下载 MP4 [2]</a>
  │     <a href="https://dl.snapcdn.app/get?token=…">下载 MP4 HD</a>
  │     <a href="https://dl.snapcdn.app/get?token=…">下载 MP3</a>
  ▼
浏览器 ──③ 点击按钮──► GET dl.snapcdn.app/get?token=JWT
                         │
                         │ 验签 + 校验 nbf/exp → 回源拉流
                         ▼
                      200 video/mp4
                      Content-Disposition: attachment; filename="TikVideo.App_7615930241370348219-hd.mp4"
```

### 1.3 `dl.snapcdn.app` 是什么：JWT 签名下载代理

把它的 JWT payload 解出来（HS256，无需密钥即可读 payload）：

```json
{
  "url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi0000d6ojganog65rfsorvlkg&line=0&ratio=1080&media_type=4&vr_type=0&improve_bitrate=0&is_play_url=1&is_support_h265=0&source=PackSourceEnum_PUBLISH",
  "filename": "TikVideo.App_7615930241370348219-hd.mp4",
  "nbf": 1793927693, "exp": 1793931293, "iat": 1793927693
}
```

也就是说：**真实 CDN 地址明文放在 JWT 里**，服务端只负责验签和定时（1 小时窗口）。

实测行为（不是 302，是**代理回源**）：

```
$ curl -D - "https://dl.snapcdn.app/get?token=…"
HTTP/2 200
content-type: video/mp4
content-length: 6585380
content-disposition: attachment; filename="TikVideo.App_7615930241370348219.mp4"
```

它做三件事：
1. 用服务端密钥验 JWT 签名；
2. 校验 `nbf`/`exp`（过期就返回 `Download Link is expired.`）；
3. 回源拉取 CDN 文件，用 `Content-Disposition` 指定文件名吐给浏览器。

作用是**防盗链 + 隐藏真实源站 + 统一文件名**，而不是加密。

### 1.4 它是怎么拿到「无水印直链」的

从 JWT 里能直接看到它的上游来源，这是整个站点的核心：

| 按钮 | 上游真实地址 |
|---|---|
| 抖音 MP4 HD | `https://aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi0000d6ojganog65rfsorvlkg&line=0&ratio=1080&media_type=4&…` |
| 抖音 MP3 | `https://lf3-music-east.douyinstatic.com/obj/ies-music-hj/7615930215939820347.mp3` |
| 抖音 封面 | `https://p5-ex-gddgtc-sign.douyinpic.com/tos-cn-p-0015c000-ce/…tplv-dy-360p.webp` |
| TikTok MP4 HD | `https://v16.tokcdn.com/bf79913f74a7021d2eb3ae3789f74cef/5d3b9400/6718335390845095173_original.mp4?dl=1` |
| TikTok MP3 | `https://v16-ies-music.tiktokcdn-us.com/…/video/tos/…?mime_type=audio_mpeg` |
| 视频播放流 | `https://v5-dy-ov-experiment.zjcdn.com/…/video/tos/cn/tos-cn-ve-15c000-ce/…` |

**结论：它没有做任何「去水印算法」。** 它只是知道每个平台「干净原片」放在哪个 URL，
然后把这条 URL 包进自己的签名代理里发出去。

---

## 二、原理：为什么能拿到无水印高清

### 2.1 平台同时存两份视频

| 字段 | 内容 | 有没有水印 |
|---|---|---|
| `video.play_addr` / `playAddr` | 干净的原片流 | ❌ 无 |
| `video.download_addr` / `downloadAddr` | App「保存到本地」用的那份 | ✅ 烧录了 logo + 作者 ID |

网上流传的「把 `playwm` 改成 `play`」是老版本玩法（URL 里的文件名标识），
现在的对应物是**根本不用 `download_addr`**。

### 2.2 抖音：走 App 的播放接口拿原片

抖音 App 播放视频时请求：

```
https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&line=0&ratio=1080&media_type=4&…
```

它返回 **302**，跳到真正存原片的 CDN：

```
$ curl -I "https://aweme.snssdk.com/aweme/v1/play/?video_id=v1e00fgi0000d6ojganog65rfsorvlkg&ratio=1080&media_type=4&…"
HTTP/2 302
location: https://v26-cold.douyinvod.com/…/video/tos/cn/tos-cn-ve-15c000-ce/oEBftAem8YEQpV7uBVgNIfLIITJ1bTxrGMJnCp/?a=1128&br=6133&bt=6133&mime_type=video_mp4&…
```

`video_id=` 后面那个 `v1e00fgi0000…` 就是 `video.play_addr.uri`，它来自作品详情接口。

> **实测验证**：跟完这个 302 下载到 9,891,411 字节的 MP4，
> `ffprobe` 显示 `h264 / 1080×1920 / aac`，抽帧目视确认**画面上没有任何抖音水印**。

参数含义：`ratio=1080` 请求 1080P、`media_type=4` 指定视频轨、`improve_bitrate=1` 提升码率、
`is_play_url=1` 表示要「播放地址」而不是「下载地址」。

### 2.3 TikTok：网页 JSON 里直接给了干净地址

TikTok 作品页把整条记录塞在 `<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">` 里：

```
webapp["video-detail"]["itemInfo"]["itemStruct"]["video"]
  ├── playAddr        ← 干净
  ├── downloadAddr    ← 带水印
  ├── bitrateInfo.bitrate[].PlayAddr  ← 各档清晰度（1080/720/…）
  └── duration
```

外加 `itemStruct.music.playUrl` 就是 MP3 的直链。

### 2.4 MP3 从哪来

两个平台的音频都是**独立轨**，不是从视频里抽的：

- 抖音：`music.play_url.url_list` → `lf3-music-east.douyinstatic.com/obj/ies-music-hj/{music_id}.mp3`
- TikTok：`music.playUrl` → `v16-ies-music.tiktokcdn-us.com/…?mime_type=audio_mpeg`

所以 tikvideo.app 那个 `s3.tik-cdn.com/api/json/convert` 接口（`ftype=mp4`、`fquality=1080p`）
只是**兜底**：万一只有视频没有独立音频轨，就服务端合成一个。

---

## 三、本地复刻：nodl

### 3.1 设计目标

| 目标 | 实现 |
|---|---|
| 零依赖 | 纯 Python 3.8+ 标准库，`pip install` 都不需要 |
| 交互对齐 | 本地网页版：粘贴链接 → MP4 [1] / MP4 [2] / MP4 HD / MP3 四个按钮 |
| 抗改版 | 用「递归找 key」代替硬编码 JSON 路径，平台改结构不会直接 KeyError |
| 可审计 | 每个下载链接都带正确的 `Referer`，不走任何第三方中转 |
| 抗失效 | 四层韧性递进（uri 缓存 → 一方接口 → 公共源 → 手动导入），见 §3.5 |

### 3.2 目录结构

```
nodl/
├── nodl/
│   ├── httpclient.py   # 零依赖 HTTP 客户端（Cookie jar、重试、gzip、Range、禁重定向）
│   ├── extract.py      # 链接归一化：短链展开、平台判定、ID 提取、直链识别
│   ├── jsonutil.py     # 递归查找 JSON 字段 + 清晰度排序
│   ├── models.py       # MediaLink / Resolved 数据模型
│   ├── douyin.py       # 抖音解析器（分享页 SSR / Web 详情接口 / App 播放接口）
│   ├── tiktok.py       # TikTok 解析器（__UNIVERSAL_DATA_FOR_REHYDRATION__ / oEmbed）
│   ├── browser.py      # 可选：Playwright 借真实浏览器签名取数
│   ├── providers.py    # 公共解析源兜底（tikwm / ajaxsearch）
│   ├── cache.py        # uri / 直链本地缓存（韧性 L0 层）
│   ├── downloader.py   # 断点续传、进度条、文件名校验
│   ├── resolver.py     # 四层调度（uri 缓存 → 一方接口 → 公共源 → 直链兜底）
│   ├── cli.py          # 命令行
│   ├── __main__.py     # python -m nodl 入口
│   └── server.py       # 本地 Web 服务（http.server）
├── web/index.html      # 仿 TikVideo 的网页界面
├── snippets/capture.js # 浏览器捕获片段（DevTools Console / 书签小工具）
├── selftest.py         # 自检脚本（离线夹具 + 在线连通性）
├── docs/               # 实测截图
├── start.bat / start.sh / check.bat
├── AGENTS.md
└── README.md
```

### 3.3 快速开始

```bash
cd nodl

# 1) 解析并下载（默认输出到 ./downloads）
python3 -m nodl resolve "https://v.douyin.com/xxxxxxx/"

# 2) 只看解析结果，不下载
python3 -m nodl resolve "<链接>" --list
python3 -m nodl resolve "<链接>" --json

# 3) 只要音频 / 只要视频
python3 -m nodl resolve "<链接>" --mp3
python3 -m nodl resolve "<链接>" --mp4

# 4) 指定解析源
python3 -m nodl resolve "<链接>" --source first-party   # 只用官方接口
python3 -m nodl resolve "<链接>" --source tikwm          # 只用公共接口 A
python3 -m nodl resolve "<链接>" --source ajaxsearch     # 只用公共接口 B

# 5) 本地网页版（复刻 TikVideo 的交互）
python3 -m nodl serve            # → http://127.0.0.1:8787
```

### 3.4 四种运行模式（重要）

由于平台把匿名接口锁了（详见第四节），nodl 提供四条路：

**A. 自动（默认，推荐先试这个）**

`--source auto` 会**先走官方接口，失败自动切到公共解析接口**。
绝大多数情况下你什么都不用配，直接粘链接就能下。

**B. 直链 / uri 直通（零配置，最可控）**

```bash
# 已知 uri（就是 play_addr.uri，v 开头）时：
python3 -m nodl resolve "v1e00fgi0000d6ojganog65rfsorvlkg"

# 已知 CDN 直链时：
python3 -m nodl resolve "https://v26-cold.douyinvod.com/…/video/tos/…"
```

**C. 自带 Cookie（最自主可控）**

```bash
# 浏览器 F12 → Application → Cookies → 复制 ttwid / msToken / sessionid
python3 -m nodl serve --cookie-file cookie.txt
python3 -m nodl resolve "<链接>" --cookie "ttwid=xxx; msToken=yyy"
```

登录态 Cookie 能绕过 Argus 的 `Uifid Not Found`，详情接口直接返回
`aweme_detail.video.play_addr.uri`。

**D. 真实浏览器（最稳，且不依赖任何第三方）**

```bash
pip install playwright && playwright install chromium
python3 -m nodl resolve "<链接>" --browser
```

`nodl/browser.py` 打开真实页面，**监听页面自己发出的详情请求**，
把返回的 JSON 截下来 —— 签名、验证码都由页面自己搞定。

> 注意：抖音的验证码是**按 IP 信誉**触发的。机房 IP / 代理 IP 很容易被弹滑块，
> 家用宽带成功率高得多。

### 3.5 韧性设计：第三方源挂了怎么办

公共解析源（`tikwm`、`ajaxsearch`）本质是**耗材**——它们随时可能改接口、上验证码、
调整 HTML 结构，甚至直接跑路。所以 nodl 的架构是**四层递进**，越往下越不依赖外部：

| 层 | 依赖 | 什么时候用 | 寿命 |
|---|---|---|---|
| **L0 uri 缓存** | **零** | 解析过一次的视频，之后直接命中 | **永久**（uri 永不过期） |
| **L1 官方接口** | 你自己的 Cookie / 浏览器 | 默认第一选择 | 长期稳定 |
| **L2 公共解析源** | 第三方站点 | L1 失败时自动兜底 | 不确定，可能随时失效 |
| **L3 手动导入** | **零** | L2 全挂时的最终手段 | **永久** |

#### L0：uri 缓存（关键设计）

抖音的 `uri`（`v1e00fgi0000…`）是**永久内容标识**，永不过期；而
`aweme.snssdk.com/aweme/v1/play/?video_id={uri}` 是**平台自己的**接口，
随时能换出一条**全新的、带新签名**的 CDN 直链。

所以 nodl 每次解析成功都会把 `uri` 存到 `~/.nodl/uri_cache.json`。
之后同一个视频再来，**一个第三方请求都不会发**，直接走平台接口拿新链接。
第三方全挂了，你解析过的视频**依然能下**。

> 缓存目录可以用环境变量 `NODL_HOME` 改（默认 `~/.nodl`）。
> 命中缓存时解析结果的 `strategy` 会显示 `uri-cache`，命令行加 `--json` 就能看到。

#### L3：手动导入（永远可用的兜底）

网页版底部有一个「🛠 手动导入直链」面板，两条路：

1. **手动**：浏览器 F12 → Network → 筛 `Media` 或搜 `.mp4` → 右键 Copy link address → 粘进去
2. **自动**：把 `snippets/capture.js` 贴进 DevTools Console 执行，脚本会扫描
   `performance` 资源、视频标签、内联 JSON 三处，自动把直链 POST 到本地服务

脚本走的是 `POST /api/ingest`（已开 CORS，跨源可用），服务端只负责把直链包成
`Resolved`，不解析任何平台语义。

命令行同样支持：`python3 -m nodl resolve "https://cdn.../video.mp4"`

### 3.6 公共解析源是怎么工作的

`providers.py` 里的 `ajaxsearch` 源做的事，正好是我们逆向 tikvideo.app 时看到的：

1. POST `tikvideo.app/api/ajaxSearch`，拿到服务端渲染的 HTML 片段；
2. 片段里每个下载按钮都是一个 `https://dl.snapcdn.app/get?token=<JWT>`；
3. JWT 的 payload 是 **base64 明文**（HS256 只需要服务端验签才能防伪，
   但**读取** payload 不需要密钥），解出来就是上游 CDN 直链 + 文件名；
4. 如果那条直链是 `aweme.snssdk.com/aweme/v1/play/?video_id=…`，
   再跟一次 302，就拿到 `v*-coldy.douyinvod.com` 的 **1080p 原片**。

实测：抖音链接 `7661509500746108194` → 解析出 1920×1080 MP4 (5.5MB) + 720P MP4 + 独立音轨 MP3，
抽帧确认画面无任何水印。

---

## 四、实测记录与平台拦截现状

以下都是在 2026-11-06 沙箱里实测的结果（`curl` 直接打）：

| 接口 | 结果 |
|---|---|
| `www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=…` | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| `www.iesdouyin.com/aweme/v1/web/aweme/detail/?aweme_id=…` | 同上 `403` |
| `www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=…` | `200` 但 **body 为空**（该接口已废弃） |
| `www.douyin.com/video/{id}` 页面内嵌 `RENDER_DATA` | 只有 `tccConfig` 配置项，**没有作品详情** |
| `www.iesdouyin.com/share/video/{id}/` 的 `_ROUTER_DATA` | 已不再下发 `videoInfoRes` |
| 作品页网络面板 | 出现 `passport/web/challenge/` → **验证码网关**；详情接口压根没被调用 |
| `aweme.snssdk.com/aweme/v1/play/?video_id={uri}` | ✅ `302` → 干净原片 |
| `dl.snapcdn.app/get?token=…`（该站自己的代理） | ✅ `200` + `Content-Disposition` |

**结论**：抖音 Web 端详情接口对匿名请求已完全关闭，
现在唯一稳定的纯 HTTP 路径是 `aweme.snssdk.com` 播放接口 —— 而它需要 `uri`。
`uri` 必须通过登录 Cookie、真实浏览器，或公共解析接口拿到。这就是为什么 nodl
提供 `--cookie` / `--browser` / `--source` 三条路，而不是假装纯 requests 能搞定。

**另外还试过并确认失效的路径**（都实测返回空 body）：

| 尝试 | 结果 |
|---|---|
| `aweme.snssdk.com/aweme/v1/aweme/detail/?aweme_id=…` | `200` 空 body（要 X-Gorgon/Argus 签名） |
| `www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=…` | `200` 空 body（分享页自己在调，但需要有效 server-token + 会话） |
| `www.douyin.com/web/api/v2/aweme/iteminfo/?item_ids=…` | `200` 空 body |
| `www.iesdouyin.com/web/api/v2/aweme/iteminfo/` 各种参数组合 | `200` 空 body |
| 爬虫 UA（百度 / Googlebot）访问作品页 | 返回普通页面，**不含** `play_addr` |
| `Baiduspider` UA 访问分享页 | `302` 跳到桌面版作品页，同样拿不到数据 |

> 附带发现：抖音桌面作品页里还埋了
> `https://www.douyin.com/video/7397724463833435442?source=Baiduspider-sdc`
> —— 说明百度爬虫通道会拿到 SSR 版页面，这是搜索引擎优化用的旁路。

### 4.1 v1.2.0 新增实测（2026-11-09）

上一节的接口状态表是 11-06 打的；这一节是 1.2.0 加了 L0 缓存层和 L3 手动导入之后
重新跑的记录（`python selftest.py` = **40/40**：31 离线 + 9 在线）。

| 项 | 结果 |
|---|---|
| `selftest.py --offline` | 31/31 —— 含 uri 缓存读写落盘、`/api/ingest` 本地起服务打真实 HTTP |
| `selftest.py`（联网） | 40/40 —— 缓存命中零第三方请求、ajaxsearch 出 HD 直链、App 播放接口可下载 |
| uri 缓存命中 | `strategy = "uri-cache"`，解析结果里带新签名的 CDN 直链，**没发任何第三方请求** |
| `ajaxsearch` 公共源 | 抖音链接 `7661509500746108194` → 第一条就是 `MP4 HD` 直链（不是播放接口地址）+ 独立 MP3 音轨 |
| App 播放接口 | `uri` → 302 → `v*-cold.douyinvod.com` / `zjcdn.com` 直链，`Content-Type: video/mp4`，文件头 `ftyp` |
| `POST /api/ingest` | 返回 `ok:true` + 视频直链 + 原标题；响应带 `Access-Control-Allow-Origin: *`；缺 `url` 返回 400；`OPTIONS` 预检返回 204 |

> 在线那 9 项依赖本机 TLS 信任链，沙箱里偶尔会抖一下
> （`CERTIFICATE_VERIFY_FAILED`），重跑一次通常就绿，不是代码问题。

---

## 五、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 下载返回 403 | CDN 校验 `Referer` | nodl 已自动带 `Referer`；如果是自改的代码，记得保留 `Referer: https://www.douyin.com/` |
| 下载得到 0 字节 / 文件损坏 | 网络中断 | 重新运行即可，nodl 支持 `.part` 断点续传 |
| 改了代码但行为没变 | Python 字节码缓存 | `find . -name __pycache__ -exec rm -rf {} +` 后重跑 |
| 解析提示「需要登录态」 | 见第四节 | 换 `--source auto`（默认）自动走公共接口；或 `--cookie` / `--browser` |
| 想要最自主可控、不依赖任何第三方 | — | `--source first-party` + `--cookie` |
| MP3 按钮缺失 | 该作品没有独立音轨 | 平台侧通常都有音轨；实在没有可加 `--mp4` 抓视频音轨 |
| 所有源都解析失败 | 公共解析源是耗材，挂了就是挂了 | 用 L3：网页版「🛠 手动导入直链」面板，或 `snippets/capture.js` 自动捕获 |
| 第二次解析没走网络 | 命中了本地 uri 缓存（L0） | 正常，这是设计；想强制重解析就删掉 `~/.nodl/uri_cache.json` |
| 端口被占用 | 8787 冲突 | `./start.sh 9000`（Windows：`start.bat 9000`） |

---

## 六、合规边界

- 只下载**你自己发布**的作品，或**已获作者授权**的素材。
- 不要绕过「仅粉丝可见 / 私密」等权限限制。
- 不要把下载内容用于二次分发牟利，遵守原作者的版权声明。
- 抓取频率保持克制，别打平台接口。
- 平台条款和版权法因地区而异，商业用途请自行确认。

工具本身是**中立的**：它只把你已经能看到的链接解析成直链，不包含任何绕过
访问控制的能力，也不存储你的链接。

---

## 附：实测产物

| 文件 | 说明 |
|---|---|
| `docs/ui_shot.png` | 本地网页版实际运行截图：解析源选择器 + MP4 HD / MP4 无水印 / MP3 原声 三个下载按钮 + 「🛠 手动导入直链」面板 |
| `docs/hd_frame.png` | 解析出的 1920×1080 原片抽帧 —— 画面上没有任何抖音水印 |

---

## 附：一张图看懂全链路

```
你粘贴的链接
      │
      ├─ 短链 ─► v.douyin.com ──302──► /video/{aweme_id}
      │
      ▼
 ┌──────────────────────────────────────────────┐
 │  ① 取作品详情                                  │
 │     抖音: aweme/v1/web/aweme/detail           │
 │            → video.play_addr.uri  (v1e00fgi…)  │
 │            → music.play_url        (mp3)        │
 │     TikTok: __UNIVERSAL_DATA_FOR_REHYDRATION__│
 │            → video.playAddr / music.playUrl    │
 └──────────────────────────────────────────────┘
      │
      ▼
 ┌──────────────────────────────────────────────┐
 │  ② 换算成无水印直链                            │
 │     抖音: aweme.snssdk.com/aweme/v1/play/     │
 │           ?video_id={uri}&ratio=1080  ──302──► │
 │           v26-cold.douyinvod.com/…(原片)      │
 │     TikTok: video.playAddr (本身就是干净流)     │
 └──────────────────────────────────────────────┘
      │
      ▼
 ┌──────────────────────────────────────────────┐
 │  ③ 下载（带正确 Referer，否则 CDN 403）         │
 └──────────────────────────────────────────────┘
```
