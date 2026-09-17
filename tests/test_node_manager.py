# -*- coding: utf-8 -*-
"""node_manager 扩展（多机 L1）：nodes 表读写 / 轮询状态翻转 / snapshot。"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import types
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import importlib
node_manager = importlib.import_module("software.extensions.node_manager.main")

results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(("PASS" if cond else "FAIL") + f" {name}" + (f" | {extra}" if extra and not cond else ""))


class _DB:
    """最小 sqlite 适配：提供 node_manager 用的 execute/query/query_one。
    生产 engine 是每线程连接；这里用 check_same_thread=False + 锁等价模拟。"""

    def __init__(self, path):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=None):
        with self._lock:
            cur = self.conn.execute(sql, params or ())
            self.conn.commit()
            return cur.rowcount

    def query(self, sql, params=None):
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params or ()).fetchall()]

    def query_one(self, sql, params=None):
        with self._lock:
            r = self.conn.execute(sql, params or ()).fetchone()
            return dict(r) if r else None


# ── 假节点：起一个真实 /health ──────────────────────────
def _fake_node(port, ok=True):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": ok, "version": "v0.0.1-alpha.0",
                               "uptime_seconds": 60, "memory_mb": 42.5,
                               "uptime_human": "0h 1m"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


srv1 = _fake_node(8231)
srv2 = _fake_node(8232, ok=False)   # 返回 ok:false → 视为 down

import tempfile as _tf
tmp_db = os.path.join(_tf.gettempdir(), "_nodes_tmp_test.db")
if os.path.exists(tmp_db):
    os.remove(tmp_db)

fw = types.SimpleNamespace(
    db=_DB(tmp_db),
    config={"node_manager": {"enabled": True, "interval": 5, "timeout": 3,
                             "nodes": [
                                 {"name": "node-a", "url": "http://127.0.0.1:8231"},
                                 {"name": "node-b", "url": "http://127.0.0.1:8232"},
                                 {"name": "node-x", "url": "http://127.0.0.1:1"},  # 连不上
                             ]}})


class _Services:
    def __init__(self):
        self._s = {}

    def register(self, name, obj):
        self._s[name] = obj

    def get(self, name):
        return self._s.get(name)


fw.services = _Services()
logs = []
ctx = types.SimpleNamespace(_framework=fw, log=logs.append)

try:
    node_manager.register(ctx)
    mgr = fw.services.get("node_manager")
    check("服务已注册 + 3 节点纳管", mgr is not None and len(mgr.nodes) == 3)
    check("启动日志", any("节点管理器已启动" in m for m in logs))

    r = mgr.poll_now()
    by = {x["name"]: x for x in r}
    check("up 节点健康数据", by["node-a"]["status"] == "up"
          and by["node-a"]["version"] == "v0.0.1-alpha.0"
          and by["node-a"]["memory_mb"] == 42.5)
    check("ok:false 视为 down", by["node-b"]["status"] == "down")
    check("不可达视为 down", by["node-x"]["status"] == "down")

    snap = mgr.snapshot()
    check("snapshot 聚合 3 行", len(snap) == 3
          and {s["name"] for s in snap} == {"node-a", "node-b", "node-x"})
    check("心跳 updated_at 落库", all(s["updated_at"] for s in snap))

    # 再轮询一轮：状态未翻转不应再打日志
    n_logs = len(logs)
    mgr.poll_now()
    check("状态稳定不再打日志", len(logs) == n_logs)

    # 节点宕机 → 状态翻转
    srv1.shutdown()
    r = mgr.poll_now()
    check("宕机后翻转 down + 有日志",
          {x["name"]: x["status"] for x in r}["node-a"] == "down"
          and any("node-a" in m and "down" in m for m in logs[n_logs:]))

    # enabled=false 不启动
    fw2_db = _DB(tmp_db)
    fw2 = types.SimpleNamespace(
        db=fw2_db, config={"node_manager": {"enabled": False}})
    node_manager.register(types.SimpleNamespace(_framework=fw2, log=lambda m: None))
    check("enabled=false 不注册服务",
          "node_manager" not in getattr(fw2, "services", {}))
finally:
    node_manager.unregister()
    srv1.server_close()
    srv2.server_close()
    # 不显式关闭 sqlite 连接：后台轮询线程可能仍在使用，
    # 强行 close 在 Windows 上有段错误风险；进程退出即释放。
    try:
        os.remove(tmp_db)
    except Exception:
        print(f"[warn] 临时库未清理: {tmp_db}")

print(f"\n结果: {sum(results)}/{len(results)} PASS")
sys.exit(0 if all(results) else 1)
