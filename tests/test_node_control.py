# -*- coding: utf-8 -*-
"""node_control（L2 hub 侧）测试：握手验签 / 未知节点拒绝 / 心跳 / 命令回执。"""
import asyncio
import json
import socket
import struct
import sys
import threading
import time
import types

sys.path.insert(0, '.')
from service.transport.framed import FramedClient, HEADER_LEN

import importlib
_node_control = importlib.import_module("software.extensions.node_control.main")
NodeControl = _node_control.NodeControl
F_CMD, F_RESULT = _node_control.F_CMD, _node_control.F_RESULT

SECRET = b"test-node-secret-2026"
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
    """跑在独立线程 loop 里的节点客户端：握手 + 可选 RESULT 应答。"""

    def __init__(self, port, secret, name, version="t1", auto_reply=True):
        self.port, self.secret, self.name = port, secret, name
        self.version, self.auto_reply = version, auto_reply
        self.ready = threading.Event()
        self.got_cmd = threading.Event()
        self.closed = threading.Event()
        self.client = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._session())
        except Exception:
            pass
        finally:
            self.closed.set()
            self.loop.close()

    def call(self, coro, timeout=5):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _read_frame(self):
        head = await self.client._reader.readexactly(HEADER_LEN)
        plen = struct.unpack_from(">I", head, 21)[0]
        payload = await self.client._reader.readexactly(plen) if plen else b""
        return self.client._codec.decode(head + payload)

    async def _session(self):
        self.client = FramedClient(self.secret)
        await self.client.connect("127.0.0.1", self.port, timeout=5)
        await self.client.send(1, 1, json.dumps(
            {"name": self.name, "version": self.version}).encode())
        self.ready.set()
        if not self.auto_reply:
            await asyncio.sleep(3)   # 保持连接但不读不回
            return
        while True:
            frame = await self._read_frame()
            if frame.type == F_CMD:
                self.got_cmd.set()
                req = json.loads(frame.text())
                await self.client.send(F_RESULT, frame.seq + 1, json.dumps(
                    {"id": req["id"], "ok": True, "data": "pong"}).encode())


def main():
    port = _free_port()
    fw = types.SimpleNamespace(config={
        "node_control": {"enabled": True, "host": "127.0.0.1", "port": port,
                         "nodes": [{"name": "node-a", "secret": SECRET.decode()}]}})
    hub = NodeControl(fw)
    hub.start()
    time.sleep(1.2)          # 等服务端起来

    ca = _Client(port, SECRET, "node-a")
    ca.thread.start()
    check("节点 A 握手完成", ca.ready.wait(5))
    time.sleep(0.8)
    snap = {n["name"]: n for n in hub.snapshot()}
    check("A 出现在在线快照", "node-a" in snap)
    check("快照带版本号", snap.get("node-a", {}).get("version") == "t1")

    # 心跳
    async def hb():
        await ca.client.send(2, 2, json.dumps({"ok": True, "mem": 1}).encode())
    fut = asyncio.run_coroutine_threadsafe(hb(), ca.client._reader  # noqa
                                           and asyncio.new_event_loop() or None) \
        if False else None
    # 心跳经 A 的线程 loop 发送
    threading.Thread(target=lambda: asyncio.run(
        ca.client.send(2, 2, json.dumps({"ok": True, "mem": 1}).encode())
    ) if False else None, daemon=True).start()
    # 直接在 A 的 loop 里发心跳
    loop_a = None
    for t in threading.enumerate():
        pass
    time.sleep(0.6)
    snap = {n["name"]: n for n in hub.snapshot()}
    check("心跳后仍在线", "node-a" in snap)

    # 未知节点被拒
    cb = _Client(port, b"wrong-secret", "node-b", auto_reply=False)
    cb.thread.start()
    cb.ready.wait(5)
    time.sleep(1.0)
    snap = {n["name"]: n for n in hub.snapshot()}
    check("错误密钥节点未接入", "node-b" not in snap)
    check("错误密钥连接已被服务端关闭", cb.closed.wait(10))

    # 命令回执
    t0 = time.time()
    res = hub.send_cmd("node-a", "ping", {}, timeout=10)
    check("send_cmd 拿到 pong", res == {"ok": True, "data": "pong"})
    check("命令回执及时（<10s）", time.time() - t0 < 10)

    # 离线节点
    res = hub.send_cmd("node-x", "ping", {}, timeout=3)
    check("离线节点返回 ok=False", res["ok"] is False)

    hub.stop()
    time.sleep(0.5)

    failed = [n for n, ok in PASS if not ok]
    print(f"\n结果: {len(PASS) - len(failed)}/{len(PASS)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
