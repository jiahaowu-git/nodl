/* ===================================================================
 * nodl · 浏览器捕获片段
 * ===================================================================
 *
 * 用法（任选其一）：
 *
 *  A) DevTools 片段
 *     1. 浏览器打开抖音 / TikTok 作品页，**先把视频播起来**
 *     2. F12 → Console → 粘贴本文件全部内容 → 回车
 *     3. 控制台会打印找到的直链，并自动 POST 到本地 nodl 服务
 *
 *  B) 书签小工具（Bookmarklet）
 *     把下面这行压缩后拖到书签栏，点击即可（它把页面里已加载的直链发到本地服务）：
 *
 *     javascript:(function(){fetch('http://127.0.0.1:8787/api/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:(document.querySelector('video')||{}).currentSrc||location.href,title:document.title})}).then(r=>r.text()).then(t=>alert(t)).catch(e=>alert('nodl 未运行：'+e))})()
 *
 *  前提：本地 nodl 服务已启动（python -m nodl serve）
 * =================================================================== */

(function () {
  var ENDPOINT = 'http://127.0.0.1:8787/api/ingest';

  var MEDIA_RE = /\.(mp4|m4s|m3u8|mp3|m4a)(\?|$)|\/video\/tos\/|douyinvod|zjcdn|bytecdn|tiktokcdn|tokcdn/i;

  function collectFromPerformance() {
    var out = [];
    try {
      performance.getEntriesByType('resource').forEach(function (e) {
        if (MEDIA_RE.test(e.name)) out.push(e.name);
      });
    } catch (e) {}
    return out;
  }

  function collectFromVideoTag() {
    var out = [];
    try {
      document.querySelectorAll('video').forEach(function (v) {
        var s = v.currentSrc || v.src || '';
        if (s && s.indexOf('blob:') !== 0 && MEDIA_RE.test(s)) out.push(s);
      });
    } catch (e) {}
    return out;
  }

  function collectFromInlineJson() {
    var out = [];
    try {
      var scripts = document.querySelectorAll('script[type="application/json"], script:not([src])');
      scripts.forEach(function (s) {
        var t = s.textContent || '';
        if (t.indexOf('playAddr') < 0 && t.indexOf('play_addr') < 0) return;
        var re = /"(?:playAddr|play_addr)"\s*:\s*"([^"]+)"/g, m;
        while ((m = re.exec(t))) out.push(m[1]);
        var re2 = /"url_list"\s*:\s*\[\s*"([^"]+)"/g;
        while ((m = re2.exec(t))) out.push(m[1]);
      });
    } catch (e) {}
    return out;
  }

  function collectFromGlobals() {
    var out = [];
    try {
      var candidates = [window._ROUTER_DATA, window.__INIT_PROPS__, window.__pace_f];
      candidates.forEach(function (c) {
        if (!c) return;
        var s = typeof c === 'string' ? c : JSON.stringify(c);
        var re = /"(?:playAddr|play_addr)"\s*:\s*"([^"]+)"/g, m;
        while ((m = re.exec(s))) out.push(m[1]);
      });
    } catch (e) {}
    return out;
  }

  var found = []
    .concat(collectFromPerformance())
    .concat(collectFromVideoTag())
    .concat(collectFromInlineJson())
    .concat(collectFromGlobals());

  // 去重
  var seen = {}, uniq = [];
  found.forEach(function (u) {
    u = u.replace(/&amp;/g, '&');
    if (!seen[u]) { seen[u] = 1; uniq.push(u); }
  });

  if (!uniq.length) {
    console.warn('[nodl] 没找到媒体直链。\n' +
      '常见原因：\n' +
      '  1) 视频还没开始播放 —— 先点一下播放按钮，等画面出来\n' +
      '  2) 页面是验证码/登录墙 —— 换个视频或先登录\n' +
      '  3) 视频走的是 m3u8 分片，DevTools 里搜 .m3u8');
    return;
  }

  console.log('[nodl] 找到 ' + uniq.length + ' 条候选直链:');
  uniq.slice(0, 10).forEach(function (u) { console.log('   ', u); });

  fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: uniq[0], candidates: uniq.slice(0, 10), title: document.title })
  })
    .then(function (r) { return r.text(); })
    .then(function (t) {
      console.log('[nodl] 已发送到本地服务：', t);
      alert('[nodl] 已捕获并送到本地下载器 ✓\n' + t.slice(0, 300));
    })
    .catch(function (e) {
      console.error('[nodl] 发送失败：', e);
      alert('[nodl] 发送失败（本地服务没启动？）\n' + e +
            '\n\n请先运行： python -m nodl serve');
    });
})();
