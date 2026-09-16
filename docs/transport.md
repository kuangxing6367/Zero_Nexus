# 安全传输（service/transport）

`service/transport` 提供**协议无关的安全二进制帧传输原语**，可复用于任意「裸 TCP 上的认证通道」场景：远程 agent、节点间控制面、内网穿透控制通道（无 TLS 时靠 HMAC 兜底）等。

> 源自 ZCBOT / minecraftconsole 的 MC agent 协议，已**剥离 MC 专属字段**（`tps` / `players` / 命令语义），仅保留通用传输层能力。纯标准库实现，零第三方依赖。

## 帧格式

大端序，所有帧共用 **29 字节定长头**，载荷变长：

| offset | 字段 | 长度 | 说明 |
| --- | --- | --- | --- |
| 0 | `type` | 1B | 帧类型，调用方自定义语义 |
| 1–4 | `ts` | 4B | 时间戳（秒，uint32） |
| 5–20 | `token` | 16B | `HMAC-SHA256(secret, ts4)` 前 16 字节 |
| 21–24 | `plen` | 4B | 载荷字节数 |
| 25–28 | `seq` | 4B | 单调序号（防重放） |
| 29+ | `payload` | 变长 | 任意字节，约定 UTF-8 文本 |

`HEADER_LEN = 29`。

## 安全模型（无 TLS 时）

- 固定对称密钥 + HMAC 令牌 + 时间戳窗口；
- 单调序号（`ts` 或 `seq`）判重放；
- 恒定时间令牌比对（`hmac.compare_digest`），抗时序侧信道。

## 公共 API

| 名称 | 说明 |
| --- | --- |
| `Frame` | 解码后的单帧（`type / ts / token / plen / seq / payload`） |
| `FrameCodec` | 编解码：`pack(type, payload, secret, seq)` / `unpack(buf)` / `verify(frame, secret, mode)` |
| `FramedServer` | 基于 `asyncio` 的认证 TCP 服务端，`start()` / `stop()`，收到完整帧后回调 `on_frame` |
| `FramedClient` | 配套客户端：`connect()` / `send(type, payload)` |

默认参数：时间戳窗口 `DEFAULT_REPLAY_WINDOW = 3.0s`、载荷上限 `DEFAULT_MAX_PAYLOAD = 16MB`、单次读 `DEFAULT_BUF_SIZE = 8192`。

## 服务端自动处理

`FramedServer` 在字节流上自动处理：

- **粘包**：一次 `read` 到多帧时逐帧抽取；
- **拆包**：帧未到齐时等待更多数据再解析；
- **失步重同步**：遇到未知帧类型时跳 1 字节重新对齐，避免整条流失步死锁；
- **超大载荷防护**：`plen` 超过 16MB 直接丢弃，防止 DoS 内存撑爆；
- **防重放**：超时间戳窗口或重复序号的帧被丢弃。

## 示例（概念）

```python
from service.transport import FramedServer, FrameCodec

SECRET = b"shared-secret-32-bytes-long-xxxx"

async def on_frame(frame: "Frame", writer):
    # frame.payload 为字节，约定 UTF-8 文本
    print("recv:", frame.payload.decode("utf-8", "replace"))

server = FramedServer(secret=SECRET, on_frame=on_frame, host="127.0.0.1", port=9000)
# await server.start()
```

> 精确构造函数签名以源码 `service/transport/framed.py` 为准。
