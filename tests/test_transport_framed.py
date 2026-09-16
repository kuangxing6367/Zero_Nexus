# -*- coding: utf-8 -*-
"""framed 传输层冒烟测试：整帧 HMAC 签名、防伪造、服务端空闲/半帧超时、
客户端连接超时、端到端收发。

脚本式测试（顶层直接运行，不适用 unittest discover）：
    python tests/test_transport_framed.py
"""
import asyncio
import hashlib
import hmac
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.transport.framed import (
    FrameCodec, FramedClient, FramedServer,
    HEADER_LEN, TOKEN_OFF, TOKEN_LEN, TS_OFF, PLEN_OFF, SEQ_OFF,
)

SECRET = b"smoke-secret-zeronus"
FTYPE = 0x07

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def sync_cases():
    """编解码 / 整帧签名 / 防篡改 / 旧式伪造拒绝。"""
    print("-- 同步编解码与校验 --")
    codec = FrameCodec(SECRET)
    now = int(time.time())
    payload = "hello zeronus".encode()
    raw = codec.encode(FTYPE, now, 1, payload)
    fr = codec.decode(raw)
    check("往返 type/ts/seq/payload 一致",
          fr.type == FTYPE and fr.ts == now and fr.seq == 1 and fr.payload == payload)
    ok, reason = codec.verify_seq(fr, 0)
    check("verify_seq 合法帧通过", ok, f"reason={reason}")

    # 篡改 payload 第一个字节 → bad-token（整帧签名生效的关键证据）
    raw2 = bytearray(raw)
    raw2[HEADER_LEN] ^= 0xFF
    ok, reason = codec.verify_seq(codec.decode(bytes(raw2)), 1)
    check("篡改 payload 被拒 bad-token", (not ok) and reason == "bad-token",
          f"ok={ok} reason={reason}")

    # 篡改 seq → bad-token（仅签 ts 时此帧会通过，伪造重放的核心漏洞）
    raw3 = bytearray(raw)
    struct.pack_into(">I", raw3, SEQ_OFF, 99)
    ok, reason = codec.verify_seq(codec.decode(bytes(raw3)), 0)
    check("篡改 seq 被拒 bad-token", (not ok) and reason == "bad-token",
          f"ok={ok} reason={reason}")

    # 篡改 ftype → bad-token
    raw4 = bytearray(raw)
    raw4[0] ^= 0xFF
    ok, reason = codec.verify_seq(codec.decode(bytes(raw4)), 1)
    check("篡改 ftype 被拒 bad-token", (not ok) and reason == "bad-token",
          f"ok={ok} reason={reason}")

    # 旧式仅签 ts 的 token（嗅探重放场景）→ bad-token
    old_token = hmac.new(SECRET, struct.pack(">I", now), hashlib.sha256).digest()[:TOKEN_LEN]
    raw5 = bytearray(raw)
    raw5[TOKEN_OFF:TOKEN_OFF + TOKEN_LEN] = old_token
    ok, reason = codec.verify_seq(codec.decode(bytes(raw5)), 0)
    check("旧式仅签 ts 的 token 被拒", (not ok) and reason == "bad-token",
          f"ok={ok} reason={reason}")

    # ts 模式：合法通过 + 单调 ts 重放拒绝
    fr6 = codec.decode(raw)
    ok, reason = codec.verify_ts(fr6, 0)
    check("verify_ts 合法帧通过", ok, f"reason={reason}")
    ok, reason = codec.verify_ts(fr6, now)
    check("verify_ts 单调时间戳重放拒绝", (not ok) and reason == "replay-seq",
          f"ok={ok} reason={reason}")


async def async_cases():
    """端到端收发、空闲/半帧超时、伪造帧拒收、客户端连接超时。"""
    print("-- 异步端到端 --")
    received = []

    async def on_frame(peer, frame, writer):
        received.append((peer, frame))

    server = FramedServer(SECRET, on_frame, host="127.0.0.1", port=0,
                          valid_types={FTYPE}, idle_timeout=0.6, frame_timeout=0.6)
    await server.start()
    port = server.port
    check("服务端启动取得端口", port > 0, f"port={port}")

    # 端到端：粘包场景（连续两帧一次写出）
    client = FramedClient(SECRET)
    await client.connect("127.0.0.1", port)
    await client.send(FTYPE, 1, b"frame-1")
    await client.send(FTYPE, 2, b"frame-2")
    await asyncio.sleep(0.2)
    check("端到端收到 2 帧",
          len(received) == 2 and received[0][1].text() == "frame-1"
          and received[1][1].text() == "frame-2",
          f"received={[(f.text()) for _, f in received]}")

    # 空闲超时：停止发数据，服务端应在 idle_timeout 后断开（EOF）
    t0 = time.time()
    data = await asyncio.wait_for(client._reader.read(1024), timeout=3)
    dt = time.time() - t0
    check("空闲超时被服务端断开", data == b"" and 0.3 < dt < 2.5,
          f"data={data!r} dt={dt:.2f}s")
    await client.close()

    # 半帧超时：只发 3 字节（不足帧头），应在 frame_timeout 后被断开
    client2 = FramedClient(SECRET)
    await client2.connect("127.0.0.1", port)
    client2._writer.write(b"\x07\x00\x00")
    await client2._writer.drain()
    t0 = time.time()
    data = await asyncio.wait_for(client2._reader.read(1024), timeout=3)
    dt = time.time() - t0
    check("半帧超时被服务端断开", data == b"" and 0.3 < dt < 2.5,
          f"data={data!r} dt={dt:.2f}s")
    await client2.close()

    # 伪造帧（旧式仅签 ts 的 token）被服务端丢弃，且不断连、合法帧仍可通
    client3 = FramedClient(SECRET)
    await client3.connect("127.0.0.1", port)
    now = int(time.time())
    old_token = hmac.new(SECRET, struct.pack(">I", now), hashlib.sha256).digest()[:TOKEN_LEN]
    header = bytearray(HEADER_LEN)
    header[0] = FTYPE
    struct.pack_into(">I", header, TS_OFF, now)
    header[TOKEN_OFF:TOKEN_OFF + TOKEN_LEN] = old_token
    struct.pack_into(">I", header, PLEN_OFF, 4)
    struct.pack_into(">I", header, SEQ_OFF, 100)
    client3._writer.write(bytes(header) + b"evil")
    await client3._writer.drain()
    await asyncio.sleep(0.2)
    check("伪造帧未进入 on_frame", len(received) == 2, f"received={len(received)}")
    await client3.send(FTYPE, 3, b"still-ok")
    await asyncio.sleep(0.2)
    check("拒帧后合法帧仍可通",
          len(received) == 3 and received[2][1].text() == "still-ok",
          f"received={len(received)}")
    await client3.close()

    # 客户端连接超时：patch open_connection 为慢协程，timeout 应触发 TimeoutError
    orig_open = asyncio.open_connection

    async def slow_open(*a, **k):
        await asyncio.sleep(5)
        raise OSError("unreachable")

    try:
        asyncio.open_connection = slow_open
        c4 = FramedClient(SECRET)
        try:
            await c4.connect("127.0.0.1", port, timeout=0.3)
            check("connect 超时抛 TimeoutError", False, "未触发超时")
        except asyncio.TimeoutError:
            check("connect 超时抛 TimeoutError", True)
        except Exception as e:  # noqa: BLE001
            check("connect 超时抛 TimeoutError", False, f"异常类型 {type(e).__name__}: {e}")
    finally:
        asyncio.open_connection = orig_open

    await server.stop()
    check("服务端停止后 port 归零", server.port == 0, f"port={server.port}")


def main():
    sync_cases()
    asyncio.run(async_cases())
    print(f"\n结果: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
