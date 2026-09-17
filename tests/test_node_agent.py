# -*- coding: utf-8 -*-
"""node_agent（L2 节点侧）端到端：真实 agent 连 hub，命令下发→执行→回执。"""
import importlib
import socket
import sys
import time
import types

sys.path.insert(0, '.')
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_node_control = importlib.import_module("software.extensions.node_control.main")
NodeControl = _node_control.NodeControl
_node_agent = importlib.import_module("software.extensions.node_agent.main")
NodeAgent = _node_agent.NodeAgent

SECRET = "e2e-secret-2026"
PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + " - " + name)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    port = _free_port()
    fw_hub = types.SimpleNamespace(config={
        "node_control": {"enabled": True, "host": "127.0.0.1", "port": port,
                         "nodes": [{"name": "n1", "secret": SECRET}]}})
    hub = NodeControl(fw_hub)
    hub.start()

    fw_node = types.SimpleNamespace(config={
        "node_agent": {"enabled": True, "hub_host": "127.0.0.1",
                       "hub_port": port, "name": "n1", "secret": SECRET,
                       "interval": 5, "allow_shell": False}},
        _start_time=time.time())
    agent = NodeAgent(fw_node)
    agent.start()

    # 等 agent 接入
    online = False
    for _ in range(30):
        if any(n["name"] == "n1" for n in hub.snapshot()):
            online = True
            break
        time.sleep(0.5)
    check("agent 主动外连接入 hub", online)

    res = hub.send_cmd("n1", "ping", {}, timeout=10)
    check("ping → pong", res == {"ok": True, "data": "pong"})

    res = hub.send_cmd("n1", "health", {}, timeout=10)
    check("health 返回 ok 状态", res["ok"] is True and
          isinstance(res["data"], dict) and res["data"].get("ok") is True)

    res = hub.send_cmd("n1", "shell", {"cmd": "echo hi"}, timeout=10)
    check("shell 默认禁用", res["ok"] is False and "禁用" in str(res["data"]))

    res = hub.send_cmd("n1", "no-such", {}, timeout=10)
    check("未知命令拒绝", res["ok"] is False)

    agent.stop()
    hub.stop()
    time.sleep(0.5)

    failed = [n for n, ok in PASS if not ok]
    print(f"\nresult: {len(PASS) - len(failed)}/{len(PASS)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
