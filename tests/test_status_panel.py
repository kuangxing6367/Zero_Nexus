# -*- coding: utf-8 -*-
"""status_panel 扩展冒烟：真实起 HTTP 服务，验证 /health、/、404 与启停。"""
import io
import json
import os
import sys
import time
import types
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import importlib
status_panel = importlib.import_module("software.extensions.status_panel.main")

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PORT = 8197


def get(path):
    try:
        r = OPENER.open(f"http://127.0.0.1:{PORT}{path}", timeout=5)
        return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class _Fw(types.SimpleNamespace):
    pass


def fake_fw():
    fw = _Fw()
    fw._start_time = time.time() - 3600
    fw._memory_limit_mb = 256
    fw.version = "v0.0.1-alpha.0"
    fw._read_version = lambda: "v0.0.1-alpha.0"
    fw.db = types.SimpleNamespace(db_type="sqlite")
    fw.task_queue = types.SimpleNamespace(
        stats=lambda: {"pending": 0, "running": 0, "done": 2, "failed": 0})
    fw._loaded_extensions = ["webui", "status_panel"]
    fw._loaded_user_plugins = ["demo_kv"]
    fw.zkg_tools = {"store": object()}
    fw.config = {"status_panel": {"enabled": True, "host": "127.0.0.1",
                                  "port": PORT}}
    return fw


results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(("PASS" if cond else "FAIL") + f" {name}" + (f" | {extra}" if extra and not cond else ""))


fw = fake_fw()
ctx = types.SimpleNamespace(_framework=fw,
                            log=lambda m: print("[log]", m))

status_panel.register(ctx)
time.sleep(0.3)
try:
    code, body = get("/health")
    data = json.loads(body)
    check("/health 返回 200 + JSON", code == 200 and data["ok"] is True)
    check("/health 字段齐全",
          data["version"] == "v0.0.1-alpha.0"
          and data["database"] == "sqlite"
          and data["uptime_seconds"] >= 3600
          and data["task_queue"]["done"] == 2
          and data["extensions"] == ["webui", "status_panel"]
          and data["plugins"] == ["demo_kv"]
          and data["zkg_tools"] == ["store"])

    code, body = get("/")
    check("/ 返回 HTML 状态页", code == 200 and "状态面板" in body and "fetch('/health')" in body)

    code, body = get("/nope")
    check("未知路径 404", code == 404)

    code, body = get("/health")
    check("重复探活稳定", code == 200 and json.loads(body)["ok"] is True)
finally:
    status_panel.unregister()
    time.sleep(0.2)

# 卸载后端口应不可达
try:
    get("/health")
    check("卸载后端口关闭", False, "端口仍可访问")
except Exception:
    check("卸载后端口关闭", True)

# enabled=false 时不启动
fw2 = fake_fw()
fw2.config = {"status_panel": {"enabled": False}}
status_panel.register(types.SimpleNamespace(_framework=fw2,
                                            log=lambda m: None))
try:
    get("/health")
    check("enabled=false 不开端口", False)
except Exception:
    check("enabled=false 不开端口", True)
status_panel.unregister()

print(f"\n结果: {sum(results)}/{len(results)} PASS")
sys.exit(0 if all(results) else 1)
