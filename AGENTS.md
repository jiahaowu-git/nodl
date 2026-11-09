# AGENTS.md

> 面向后续维护者与 AI 助手的工程约定。**用户视角的文档在 `README.md`**，
> 本文件只讲「代码长什么样、为什么这么写、改的时候要注意什么、怎么验证」。

---

## 0. 一句话定位

`nodl` 是一个**纯标准库**实现的本地无水印视频下载器（抖音 / TikTok），
复刻 `tikvideo.app` 的交互形态（粘贴链接 → MP4 [1] / MP4 [2] / MP4 HD / MP3）。

**上游原理**（详见 README 第一节）：平台同时存两份视频，`play_addr`（干净原片）
和 `download_addr`（烧录水印）。所谓「去水印」不是图像处理，而是**选对了源**。

---

## 1. 三条不可违背的硬约束

| # | 约束 | 原因 |
|---|---|---|
| **C1** | **核心链路零第三方依赖**，只用 Python 3.8+ 标准库 | 目标用户经常没有 PyPI 访问权、没有 pip 环境。`requests` / `beautifulsoup4` 一律不许进核心路径 |
| **C2** | **可选依赖必须能优雅降级** | `playwright` 缺失时 `browser.py` 只能抛 `BrowserUnavailable`，绝不能让 `import nodl` 挂掉 |
| **C3** | **Windows 脚本必须是 CRLF + 不用括号块** | 见 §6.3，这是本项目被用户实测打脸过一次的地方 |

---

## 2. 快速验证（改完代码先做这三件事）

```bash
# ① 语法与导入
python3 -c "import nodl.cli, nodl.server, nodl.resolver, nodl.providers, nodl.douyin, nodl.tiktok, nodl.browser"

# ② 自检（40 项：31 离线 + 9 在线）
python3 selftest.py
#   离线部分不联网：python3 selftest.py --offline

# ③ 真链路验证（必须走完下载，不能只看解析结果）
python3 -m nodl resolve "https://www.douyin.com/video/7661509500746108194" -o /tmp/dl
ffprobe -v error -show_entries stream=codec_name,width,height -of default=nw=1 /tmp/dl/*.mp4
ffmpeg -v error -ss 2 -i /tmp/dl/*HD*.mp4 -frames:v 1 /tmp/frame.png   # 抽帧目视：无水印
```

**交付标准：解析成功 + 文件能播 + 分辨率符合预期 + 抽帧无水印。** 只跑 `--list` 不算验证。

> Windows 上把上面的 `python3` 换成 `python` 或 `py`（很多机器没有 `python3` 这个名字，
> 见 §6.3）。另外**在线那 9 项依赖沙箱的 TLS 信任链**，偶尔会因为
> `CERTIFICATE_VERIFY_FAILED` 抖一下 —— 重跑一次通常就好，不是代码问题。

---

## 3. 架构地图

```
用户粘贴文本
      │
      ▼
extract.normalise()          ← 平台判定 / 短链展开 / ID 提取 / 直链与 uri 识别
      │  Target(platform ∈ {douyin, tiktok, direct, douyin-uri})
      ▼
resolver.Resolver.resolve()  ← 调度层：L0 缓存 → 一方策略 → 公共兜底 → 直链兜底 → 报错
      │
      ├── cache.UriCache            ⓪ 本地 uri 缓存（零依赖，永久有效）
      ├── douyin.DouyinResolver   ① 官方 detail 接口  ② 移动分享页 SSR
      ├── tiktok.TikTokResolver   ① 页面 __UNIVERSAL_DATA_FOR_REHYDRATION__  ② oEmbed
      ├── browser.browser_detail() ③ 可选：真实 Chromium 截获签名请求
      └── providers.PROVIDERS      ④ 公共兜底：tikwm / ajaxsearch
      │
      ▼
models.Resolved               ← 统一输出：videos[] / audios[] / images[] / uri
      │
      ├── cli.cmd_resolve()      → downloader.Downloader → ./downloads
      └── server.Handler         → http://127.0.0.1:8787
                                 + /api/resolve  + /api/download  + /api/ingest
```

### 模块职责表

| 文件 | 职责 | 不要往里塞什么 |
|---|---|---|
| `nodl/httpclient.py` | 零依赖 HTTP：Cookie jar、重试、gzip、Range、**禁重定向** | 任何平台特定逻辑 |
| `nodl/extract.py` | 输入归一化 → `Target` | 网络解析（只允许展开短链） |
| `nodl/jsonutil.py` | 递归找 key、清晰度排序、URL 反转义 | 平台字段名（那属于 douyin.py / tiktok.py） |
| `nodl/models.py` | `MediaLink` / `Resolved` 数据模型 | 任何逻辑 |
| `nodl/douyin.py` | 抖音字段语义（`play_addr.uri`、`bit_rate` 阶梯…） | 下载逻辑 |
| `nodl/tiktok.py` | TikTok 字段语义（`playAddr` vs `downloadAddr`） | 下载逻辑 |
| `nodl/browser.py` | 可选 Playwright 兜底 | 必选依赖 |
| `nodl/providers.py` | 公共解析源（tikwm / ajaxsearch） | 平台字段语义 |
| `nodl/cache.py` | uri / 直链本地缓存（韧性 L0 层） | 下载逻辑 |
| `nodl/resolver.py` | 四层调度 + 播放接口 302 升级 + 去重排序 + 码率排序 | 平台字段语义 |
| `nodl/downloader.py` | 流式下载、断点续传、文件名清洗 | 解析逻辑 |
| `nodl/cli.py` / `nodl/server.py` | 两种入口（server 另含 `/api/ingest` 手动导入） | 解析逻辑 |
| `snippets/capture.js` | 浏览器侧采集直链，POST 到 `/api/ingest` | 服务端逻辑 |

---

## 4. 关键事实速查（全部实测得出，**别再重复踩**）

### 4.1 无水印地址从哪来

| 平台 | 来源 | 说明 |
|---|---|---|
| 抖音 | `aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080&media_type=4&…` | **302** → `v*-coldy.douyinvod.com/…`，这是 1080p 原片 |
| 抖音 | `video.play_addr.uri` | 就是上面 `video_id=` 后面那个 `v1e00fgi…` |
| 抖音 | `music.play_url.url_list` | 独立音轨 → `lf*-music-east.douyinstatic.com/obj/ies-music-hj/{id}.mp3` |
| 抖音 | `video.bit_rate[]` | 各档清晰度阶梯，`gear_name` 含 `1080` / `720` |
| TikTok | `video.playAddr` | 干净流 |
| TikTok | `video.downloadAddr` | **带水印，永不使用** |
| TikTok | `video.bitrateInfo.bitrate[].PlayAddr` | 清晰度阶梯 |
| TikTok | `music.playUrl` | 独立音轨 |

### 4.2 平台拦截现状（2026-11 实测）

```
www.douyin.com/aweme/v1/web/aweme/detail/       → 403 ArgusSecurityPlugin "Uifid Not Found"
www.iesdouyin.com/aweme/v1/web/aweme/detail/     → 403 同上
www.iesdouyin.com/web/api/v2/aweme/iteminfo/     → 200 空 body（废弃 / 需有效会话）
aweme.snssdk.com/aweme/v1/aweme/detail/          → 200 空 body（需 X-Gorgon 签名）
爬虫 UA 访问作品页 / 分享页                     → 不含 play_addr
真实 Chromium 打开作品页                         → 触发 passport/web/challenge/ 验证码网关
```

**判据**：接口返回 `200` 但 **body 长度为 0** = 已废弃或需要会话，不是「网络问题」。
遇到这种情况不要浪费时间调参数。

### 4.3 `dl.snapcdn.app` 的 JWT 技巧

该站（也是 `providers.ajaxsearch` 的数据源）返回的下载按钮形如
`https://dl.snapcdn.app/get?token=<JWT>`。JWT 是 HS256：

- **payload 是 base64 明文**，无需密钥即可读取（`providers.decode_jwt_payload`）
- payload 内容 = `{url, filename?, nbf, exp, iat}`，`url` 就是上游 CDN 直链
- 该接口**不是 302**，而是代理回源（`200` + `Content-Disposition`）

### 4.4 CDN 的两个硬性要求

1. **必须带 `Referer`**（`https://www.douyin.com/` 或 `https://www.tiktok.com/`），否则 403。
   `server.py` 的 `/api/download` 用**代理回源**而不是 302，就是为了保住这个头。
2. **URL 里的 HTML 实体必须反转义**。页面里的地址含 `&amp;`，直接拿去请求会因为
   签名参数被破坏而 403。`jsonutil.normalise_url()` 统一处理（`html.unescape`）。

---

### 4.5 韧性分层（改代码时别破坏这个顺序）

`resolver.Resolver.resolve()` 的执行顺序是**有意的**，每一层都比下一层更不依赖外部：

```
L0  uri 缓存        零依赖，永久有效   ← nodl/cache.py
L1  官方接口        需 Cookie/浏览器   ← douyin.py / tiktok.py / browser.py
L2  公共解析源      依赖第三方，会失效   ← providers.py
L3  手动导入        零依赖，永远可用   ← server.py /api/ingest + snippets/capture.js
```

**关键事实**：抖音的 `uri`（`v1e00fgi…`）是永久标识，
`aweme.snssdk.com/aweme/v1/play/?video_id={uri}` 是平台自己的接口，能随时换出
**全新的带签名 CDN 直链**。所以只要缓存过 `uri`，那个视频就**永远不会因为第三方挂掉而下不了**。

改动时注意：

- `cache.py` 的 `put()` 存的是 **uri**（永久），`put_url()` 存的是 **CDN 直链**（会过期）
- `_from_cache()` 必须在 L1/L2 **之前**执行，否则缓存白存
- `_from_last_urls()` 是最后一道（CDN 签名过期后就失效了，只在几小时内有用，默认 6 小时）
- 新增 provider 时，**在 `_upgrade_play_urls()` 之前**把 uri 抓下来（升级 302 会丢掉 `video_id=`）；
  `_upgrade_play_urls()` 自己会在跟 302 **之前**把 uri 回填到 `res.uri`，
  但 provider 内部先抓一次更保险（那里拿到的才是原始播放地址）
- `Resolved.uri` 字段是第一方解析器回填 uri 的载体
- 缓存文件位置 `~/.nodl/uri_cache.json`，可用环境变量 `NODL_HOME` 改（见 `cache.py`）

---

## 5. 扩展指引

### 5.0 新增一个手动捕获来源

如果要支持新的浏览器捕获方式（比如别的平台的页面结构）：

- 改 `snippets/capture.js` 的收集函数（`collectFrom*`），它已经覆盖
  `performance` 资源 / `<video>` 标签 / 内联 JSON / 页面全局变量四路
- 收集到的地址 POST 到 `/api/ingest`（已开 CORS，跨源可用）
- `server.py` 的 `_ingest()` 只负责包成 `Resolved`，不解析平台语义

### 5.1 新增一个公共解析源

在 `nodl/providers.py` 里加一个函数，签名固定：

```python
def resolve_via_xxx(client: Client, url: str, referer: str) -> Optional[Resolved]:
    """返回 None 表示「这个源不行」，让调度层继续试下一个。"""
```

然后注册进 `PROVIDERS` 字典，并把名字加进 `resolver.py` 里 `order` 列表，
再在 `cli.py` / `server.py` 的 `--source` choices 里加上即可。**不需要改其他任何文件。**

### 5.2 新增一个平台

1. `extract.py`：加平台域名到 `DOYIN_HOSTS` / `TIKTOK_HOSTS`，加 ID 正则，
   在 `normalise()` 里加分支产出 `Target(platform="xxx")`；
2. 新建 `nodl/xxx.py`，实现 `XxxResolver.resolve(target, use_browser)` → `Resolved`；
3. `resolver.py`：`resolve()` 加分发分支；
4. `models.py` 不用改。

**字段语义用「递归找 key」而不是硬编码深路径**（见 `jsonutil.find_dict_containing`）——
平台每两周改一次结构，硬路径必炸。

### 5.3 输出格式

`models.Resolved.to_dict()` 是唯一的对外结构；CLI `--json` 和 `/api/resolve` 都用它。
加字段时同步更新 `web/index.html` 的 `render()`。

---

## 6. 常见坑

### 6.1 陈旧 `__pycache__`（本项目最坑的一次）

改完代码行为没变 → 沙箱/某些文件系统 mtime 精度不足导致 `.pyc` 没失效。

```bash
find . -name __pycache__ -type d -exec rm -rf {} +
```

**任何时候「代码明明改了却没生效」，先清缓存再怀疑别的。**

### 6.2 变量名遮蔽

`providers.py` 里局部变量 `html`（HTML 字符串）曾经遮蔽了 `import html`。
统一用 `import html as htmllib`。

### 6.3 Windows 批处理

- **必须 CRLF**。LF 的 `.bat` 在 cmd.exe 下会解析失败直接闪退。
  写完 `.bat` 一定要转：
  ```bash
  python3 -c "p='start.bat';d=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n');open(p,'wb').write(d)"
  ```
- **不要用 `if ( ... )` 括号块**，改用 `goto` 标签。括号块 + CRLF 组合在 cmd.exe 上很脆。
- 脚本结尾必须有 `pause`，否则出错时窗口秒关，用户看不到错误信息。
- `python` / `py` 都要探测；有的机器只有 `py`。

### 6.4 相似但不同的东西

| 像 | 实际是 |
|---|---|
| `download_addr` | 带水印的副本，**别用** |
| `v*.tokcdn.com/..._original.mp4?dl=1` | 无水印原片 |
| `aweme.snssdk.com/aweme/v1/play/` | 302 跳转接口，不是文件本身 |
| `dl.snapcdn.app/get?token=` | 代理回源，不是 302 |
| 缓存里的 `uri` | **永久**，只是内容标识，不是文件 |
| 缓存里的 `urls` | **会过期**（CDN 签名几小时就失效），只是兜底 |
| `MP4 HD` 标签 | 由 `br=` 码率参数或跟完 302 推断，**不是**接口保证的分辨率 |

### 6.5 命名

- 视频：`{作者}_{标题}_{标签}.mp4`，标签去空格去方括号
- 音频：`{作者}_{标题}_MP3原声.mp3`
- `safe_name()` 负责非法字符；**中文弯引号 `“”` 也要清**（Windows 下是合法但很丑，
  且部分工具链会出问题）

---

## 7. 代码风格

- 中文注释和文档字符串（项目面向中文用户）
- 类型标注齐全；`from __future__ import annotations`
- 单文件不超过 ~400 行；超过就拆
- 不用 f-string 嵌套引号（Py3.8 兼容）
- 日志用 `print(..., flush=True)`，因为输出可能被重定向到文件（缓冲会导致「看起来没反应」）

---

## 8. 质量门槛

提交前必须全部满足：

- [ ] `python3 selftest.py` 全绿（当前 40/40：31 离线 + 9 在线）
- [ ] 真实链接跑通**下载**（不是只 `--list`）
- [ ] `ffprobe` 确认分辨率 / 编码符合预期
- [ ] 抽帧目视确认**无水印**
- [ ] 新解压的干净目录里零安装跑通一次
- [ ] `.bat` 文件是 CRLF
- [ ] README 与实际行为一致（改了用法就改 README）

---

## 9. 明确不要做的事

- ❌ 不要引入 `requests` / `bs4` / `lxml` 等核心依赖（违反 C1）
- ❌ 不要把 Playwright 变成必选依赖（违反 C2）
- ❌ 不要使用 `download_addr`（带水印）
- ❌ 不要绕过「仅粉丝可见 / 私密」等访问控制
- ❌ 不要在代码里硬编码任何人的 Cookie / token / 账号
- ❌ 不要把用户粘贴的链接上传到任何地方（本项目主打本地处理）

---

## 10. 合规边界

工具本身是中立的：只把用户**本来就能看到的**链接解析成直链，不含任何绕过访问控制的能力，
不存储用户链接。

使用侧请遵守：只下载自己发布的作品或已获授权的素材；不二次分发牟利；遵守原作者版权声明；
抓取频率保持克制；商业用途自行确认当地法规。

---

## 11. 当前版本状态

- 版本：`1.2.0`（`nodl/__init__.py`）
- 自检：**40/40**（31 离线 + 9 在线）
  - 离线覆盖：链接归一化、TikTok / 抖音 JSON 夹具解析、JWT 解码、uri 缓存读写、
    `/api/ingest` 手动导入（本地起服务打真实 HTTP，不联网）
  - 在线覆盖：uri 缓存命中（零第三方请求）、ajaxsearch 公共源出 HD 直链、
    App 播放接口 302 → CDN 直链可下载
- 已验证可解析：抖音完整链接、抖音 uri、CDN 直链、TikTok 页面 JSON（离线夹具）
- **未在真实网络验证**：TikTok 线上解析（开发沙箱屏蔽 `tiktok.com`），
  公共源 `tikwm`（开发沙箱被 Cloudflare 拦截）

改动后请同步更新本文件与 `README.md`，并在 `README.md` 第四节追加新的实测记录。
