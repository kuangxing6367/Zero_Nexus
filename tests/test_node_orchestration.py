# -*- coding: utf-8 -*-
"""多机管理 L3 测试：分组解析 / 并发与滚动下发 / 能力上报 / 包同步闸门。"""
import asyncio
import importlib
import json
import socket
import struct
import sys
import threading
import time
import types

sys.path.insert(0, '.')
from service.transport.framed import FramedClient, HEADER_LEN

_ctl = importlib.import_module("software.extensions.node_control.main")
_agent_mod = importlib.import_module("software.extensions.node_agent.main")
NodeControl = _ctl.NodeControl
NodeAgent = _agent_mod.NodeAgent
F_HELLO, F_CMD, F_RESULT = 1, 3, 4

SECRET_A = b"l3-secret-node-a"
SECRET_B = b"l3-secret-node-b"
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


class _Client:
    """独立的假节点：握手（带 capabilities）+ 按命令回 pong。"""

    def __init__(self, port, secret, name, caps=None, delay=0.0):
        self.port, self.secret, self.name = port, secret, name
        self.caps, self.delay = caps or {}, delay
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.client = None
        self._out_seq = 1
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._session())
        except Exception:
            pass
        finally:
            self.closed.set()
            loop.close()

    async def _send(self, ftype, payload):
        self._out_seq += 1
        await self.client.send(ftype, self._out_seq, payload)

    async def _read_frame(self):
        head = await self.client._reader.readexactly(HEADER_LEN)
        plen = struct.unpack_from(">I", head, 21)[0]
        payload = await self.client._reader.readexactly(plen) if plen else b""
        return self.client._codec.decode(head + payload)

    async def _session(self):
        self.client = FramedClient(self.secret)
        await self.client.connect("127.0.0.1", self.port, timeout=5)
        await self._send(F_HELLO, json.dumps(
            {"name": self.name, "version": "l3-test",
             "capabilities": self.caps}).encode())
        self.ready.set()
        while True:
            frame = await self._read_frame()
            if frame.type != F_CMD:
                continue
            req = json.loads(frame.text())
            if self.delay:
                await asyncio.sleep(self.delay)
            await self._send(F_RESULT, json.dumps(
                {"id": req.get("id"), "ok": True,
                 "data": "pong:" + self.name}).encode())


def _agent(cfg_extra=None, tools=None):
    cfg = {"name": "unit-node", "secret": "unit-secret"}
    cfg.update(cfg_extra or {})
    fw = types.SimpleNamespace(config={"node_agent": cfg},
                               zkg_tools=tools or {})
    return NodeAgent(fw)


def main():
    # ── 1. 能力上报与包同步闸门（节点侧，不联网）──
    a = _agent({"allow_packages": False}, tools={"store": object()})
    caps = a.capabilities()
    check("pkg.list always enabled", caps.get("pkg.list") is True)
    check("shell disabled by default", caps.get("shell") is False)
    check("pkg.sync disabled by default", caps.get("pkg.sync") is False)

    res = a._exec("pkg.list", {})
    check("pkg.list returns loaded tools", res["ok"] and
          res["data"]["loaded_tools"] == ["store"])
    check("pkg.list returns repo packages", res["ok"] and
          "cron" in res["data"]["repo_packages"])

    res = a._exec("pkg.sync", {})
    check("pkg.sync blocked without allow_packages", res["ok"] is False and
          "allow_packages" in str(res["data"]))

    import service.startup as _startup
    _orig_run_zkg = _startup.run_zkg
    _startup.run_zkg = lambda fw: {"loaded_tools": ["store"], "plugin_count": 3,
                                   "api_incompatible": []}
    try:
        b = _agent({"allow_packages": True}, tools={"store": object()})
        res = b._exec("pkg.sync", {})
    finally:
        _startup.run_zkg = _orig_run_zkg
    check("pkg.sync allowed when explicitly enabled", res["ok"] and
          res["data"]["loaded_tools"] == ["store"])
    check("pkg.sync reports plugin_count", res["data"]["plugin_count"] == 3)

    # ── 2. hub 分组 + 在线聚合 ──
    port = _free_port()
    cfg = {"enabled": True, "host": "127.0.0.1", "port": port,
           "nodes": [{"name": "node-a", "secret": SECRET_A.decode()},
                     {"name": "node-b", "secret": SECRET_B.decode()}],
           "groups": {"edge": ["node-a", "node-b"], "solo": ["node-a"],
                      "mix": ["node-a", "node-ghost"]}}
    fw = types.SimpleNamespace(config={"node_control": cfg})
    hub = NodeControl(fw)
    check("unknown group resolves to None",
          hub.group_members("nope") is None)
    check("empty hub resolves all to []", hub.group_members(None) == [])
    res = hub.send_group("nope", "ping")
    check("unknown group is rejected without dispatch",
          res.get("error") and res["total"] == 0)
    hub.start()
    time.sleep(1.2)

    ca = _Client(port, SECRET_A, "node-a",
                 caps={"ping": True, "shell": False})
    cb = _Client(port, SECRET_B, "node-b", caps={"ping": True})
    ca.thread.start()
    cb.thread.start()
    check("node A connected", ca.ready.wait(5))
    check("node B connected", cb.ready.wait(5))
    time.sleep(0.8)

    groups = hub.list_groups()
    check("group members listed", groups["edge"]["members"] == ["node-a", "node-b"])
    check("group online members resolved",
          sorted(groups["edge"]["online"]) == ["node-a", "node-b"])
    check("offline member marked",
          groups["mix"]["offline"] == ["node-ghost"])
    snap = {n["name"]: n for n in hub.snapshot()}
    check("snapshot carries capabilities",
          snap["node-a"]["capabilities"].get("shell") is False)
    check("snapshot carries group membership",
          snap["node-a"]["groups"] == ["edge", "mix", "solo"])

    # ── 3. 分组命令：逐台回执 + 聚合摘要 ──
    res = hub.send_group("edge", "ping", {}, timeout=10, concurrency=2)
    check("edge group total=2", res["total"] == 2)
    check("edge group all ok", res["ok"] == 2 and res["failed"] == 0)
    check("edge group results ordered",
          [r["node"] for r in res["results"]] == ["node-a", "node-b"])
    check("edge group data per node",
          {r["data"] for r in res["results"]} == {"pong:node-a", "pong:node-b"})

    res = hub.send_group("solo", "ping", {}, timeout=10)
    check("solo group only one node",
          res["total"] == 1 and res["results"][0]["node"] == "node-a")

    res = hub.broadcast("ping", {}, timeout=10, concurrency=2)
    check("broadcast reaches all online nodes",
          res["total"] == 2 and res["ok"] == 2)

    res = hub.send_group("mix", "ping", {}, timeout=10, concurrency=2)
    check("offline member counted as failed",
          res["total"] == 2 and res["ok"] == 1 and res["failed"] == 1)
    check("offline member reported separately",
          res["offline"] == ["node-ghost"])

    # ── 4. concurrency / stagger 调度语义（用包装器计量，避免看网络时序）──
    orig = hub.send_cmd
    lock = threading.Lock()
    active = [0]
    peak = [0]
    starts = []

    def wrapped(name, cmd, args=None, timeout=30.0):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            starts.append(time.monotonic())
        try:
            time.sleep(0.15)
            return orig(name, cmd, args, timeout)
        finally:
            with lock:
                active[0] -= 1

    hub.send_cmd = wrapped
    try:
        peak[0] = 0
        hub.send_group("edge", "ping", {}, timeout=10, concurrency=2)
        check("concurrency=2 dispatches in parallel", peak[0] == 2)

        peak[0] = 0
        hub.send_group("edge", "ping", {}, timeout=10, concurrency=1)
        check("concurrency=1 keeps one command in flight", peak[0] == 1)

        starts.clear()
        hub.send_group("edge", "ping", {}, timeout=10, concurrency=2,
                       stagger=0.25)
        gap = starts[1] - starts[0] if len(starts) > 1 else 0
        check("stagger delays rolling dispatch", gap >= 0.15)
    finally:
        hub.send_cmd = orig

    hub.stop()
    time.sleep(0.5)

    failed = [n for n, ok in PASS if not ok]
    print(f"\nresult: {len(PASS) - len(failed)}/{len(PASS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
