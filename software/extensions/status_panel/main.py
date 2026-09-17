# -*- coding: utf-8 -*-
"""
轻量状态面板（官方扩展）

第一个与 IM 无关的官方软件级扩展：以只读方式暴露框架运行状态。
纯标准库（http.server + 可选 psutil），零第三方依赖。

端点（均只读，默认绑定 127.0.0.1，不对外）：
  GET /health  → JSON：框架版本 / 运行时长 / 内存 / 插件与扩展清单 /
                  任务队列统计 / 数据库类型（监控探活用，恒 200）
  GET /        → 暗色 HTML 状态页，前端每 5s 拉取 /health 刷新

启用：
  config.yaml → status_panel.enabled: true（默认开启）
  status_panel.host / port（默认 127.0.0.1:8090）
"""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger('zernus')

__plugin_meta__ = {
    "name": "状态面板",
    "version": "1.0.0",
    "author": "Zeronus",
    "desc": "只读运行状态：/health JSON + Web 状态页",
    "priority": 20,
    "official": True,
}

_server = None
_thread = None

_PAGE = '''<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>Zeronus 状态面板</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { background:#0f1115; color:#d8dee9; font:14px/1.6 ui-sans-serif,system-ui,"Microsoft YaHei";
         margin:0; padding:32px; }
  h1 { font-size:18px; font-weight:600; letter-spacing:.04em; margin:0 0 4px; }
  .sub { color:#6b7480; font-size:12px; margin-bottom:24px; }
  .ok { color:#4ade80; } .bad { color:#f87171; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; }
  .card { background:#171a21; border:1px solid #232833; border-radius:8px; padding:14px 16px; }
  .k { color:#6b7480; font-size:12px; }
  .v { font-size:16px; margin-top:2px; font-variant-numeric:tabular-nums; }
  ul { margin:6px 0 0; padding-left:18px; }
  table { width:100%; border-collapse:collapse; margin-top:6px; }
  td,th { padding:3px 8px; text-align:left; border-bottom:1px solid #232833; font-size:12px; }
  th { color:#6b7480; font-weight:500; }
</style></head><body>
<h1>Zeronus 状态面板</h1>
<div class="sub" id="sub">加载中…</div>
<div class="grid" id="grid"></div>
<script>
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const card = (t, rows) => `<div class="card"><div class="k">${esc(t)}</div>${rows}</div>`;
async function refresh() {
  try {
    const h = await (await fetch('/health')).json();
    document.getElementById('sub').innerHTML =
      `v${esc(h.version)} · 运行 ${esc(h.uptime_human)} · <span class="${h.ok?'ok':'bad'}">${h.ok?'运行中':'异常'}</span>`;
    const q = h.task_queue || {};
    document.getElementById('grid').innerHTML =
      card('进程', `<div class="v">${esc(h.memory_mb)} MB RSS</div>`
        + `<div class="k">上限 ${esc(h.memory_limit_mb)} MB</div>`)
      + card('任务队列', `<table><tr><th>pending</th><th>running</th><th>done</th><th>failed</th></tr>`
        + `<tr><td>${esc(q.pending ?? '-')}</td><td>${esc(q.running ?? '-')}</td>`
        + `<td>${esc(q.done ?? '-')}</td><td>${esc(q.failed ?? '-')}</td></tr></table>`)
      + card('数据库', `<div class="v">${esc(h.database)}</div>`)
      + card(`官方扩展（${(h.extensions||[]).length}）`,
        `<ul>${(h.extensions||[]).map(e=>`<li>${esc(e)}</li>`).join('')}</ul>`)
      + card(`用户插件（${(h.plugins||[]).length}）`,
        (h.plugins||[]).length
          ? `<ul>${h.plugins.map(e=>`<li>${esc(e)}</li>`).join('')}</ul>`
          : `<div class="k">暂无（zkg new 创建后放入 software/plugins/）</div>`);
  } catch (e) {
    document.getElementById('sub').innerHTML = '<span class="bad">无法获取状态</span>';
  }
}
refresh();
setInterval(refresh, 5000);
</script></body></html>'''


def _collect(fw) -> dict:
    """只读收集运行状态（任何一项失败都不影响整体）。"""
    now = time.time()
    uptime = max(0, int(now - getattr(fw, '_start_time', now)))
    h, m = divmod(uptime // 60, 60), uptime % 60
    uptime_human = f"{uptime // 86400}d {h[0]}h {m}m" if uptime >= 86400 else f"{h[0]}h {m}m"

    mem_mb, cpu = None, None
    try:
        import psutil
        p = psutil.Process()
        mem_mb = round(p.memory_info().rss / 1048576, 1)
        cpu = round(p.cpu_percent(interval=None), 1)
    except Exception:
        pass  # psutil 缺失时降级

    tq_stats = {}
    try:
        tq_stats = fw.task_queue.stats()
    except Exception:
        pass

    db_type = 'unknown'
    try:
        db_type = getattr(fw.db, 'db_type', 'unknown')
    except Exception:
        pass

    try:
        version = fw._read_version()
    except Exception:
        version = 'unknown'

    return {
        'ok': True,
        'version': version,
        'uptime_seconds': uptime,
        'uptime_human': uptime_human,
        'memory_mb': mem_mb,
        'memory_limit_mb': getattr(fw, '_memory_limit_mb', None),
        'cpu_percent': cpu,
        'database': db_type,
        'task_queue': tq_stats,
        'extensions': list(getattr(fw, '_loaded_extensions', []) or []),
        'plugins': list(getattr(fw, '_loaded_user_plugins', []) or []),
        'zkg_tools': sorted((getattr(fw, 'zkg_tools', {}) or {}).keys()),
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def _make_handler(fw):
    class StatusHandler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/health':
                self._send(200, json.dumps(_collect(fw), ensure_ascii=False),
                           'application/json; charset=utf-8')
            elif path == '/':
                self._send(200, _PAGE, 'text/html; charset=utf-8')
            else:
                self._send(404, '{"ok": false, "error": "not found"}',
                           'application/json; charset=utf-8')

        def log_message(self, fmt, *args):  # 静默访问日志
            pass

    return StatusHandler


def register(ctx):
    """启动只读状态 HTTP 服务（status_panel.enabled 默认 true）"""
    global _server, _thread
    fw = ctx._framework

    cfg = fw.config.get('status_panel', {})
    if cfg.get('enabled') is False:
        ctx.log("状态面板已禁用 (status_panel.enabled: false)")
        return

    host = cfg.get('host', '127.0.0.1')
    port = int(cfg.get('port', 8090))

    _server = ThreadingHTTPServer((host, port), _make_handler(fw))
    _server.daemon_threads = True
    _thread = threading.Thread(target=_server.serve_forever,
                               name='status-panel', daemon=True)
    _thread.start()
    ctx.log(f"状态面板已启动: http://{host}:{port}/ （/health 为 JSON，只读）")


def unregister():
    """卸载时停止"""
    global _server, _thread
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
    _thread = None
