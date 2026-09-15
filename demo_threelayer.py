#!/usr/bin/env python3
"""
ZER NUS 三层架构演示（替代原 demo_mesh.py）

对应手写笔记启动流程：
    main.py → 启动 core → 监听本地端口
    ↓ 拉起 sys 服务（初始化）
    ↓ 拉起 user 服务（均监听本地端口）
    ↓ 是否加密通讯？否→读 Token / 是→RSA + 回调端

本脚本仅演示三层装配与启动顺序，监听端口为 best-effort（沙箱/无网环境下失败不致命）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def demo():
    from core.config import load_config
    from core.engine import Framework
    from service.startup import start_sys_service, start_user_service, open_local_port

    cfg_path = sys.argv[1] if len(sys.argv) > 1 else None
    fw = Framework(cfg_path)
    core_cfg = (fw.config.get('core', {}) or {})
    try:
        open_local_port(core_cfg.get('host', '127.0.0.1'),
                        int(core_cfg.get('port', 37001)), 'core')
    except Exception as e:
        print(f"[demo] core 端口监听跳过: {e}")
    print("[demo] ① 内核级(core) 已启动")

    sys_svc = await start_sys_service(fw, fw.config)
    print(f"[demo] ② 服务级(sys) 已拉起，监听 127.0.0.1:{sys_svc['port']}")

    user_svc = await start_user_service(fw, fw.config)
    print(f"[demo] ③ 软件级(user) 已拉起，监听 127.0.0.1:{user_svc['port']}")

    sec = (fw.config.get('security', {}) or {})
    if sec.get('encrypted'):
        print(f"[demo] ④ 加密通讯：RSA 握手完成 → 回调端 {sec.get('rsa_callback') or '(未配置)'}")
    else:
        print("[demo] ④ 非加密通讯：读 Token 校验"
              + ("（已配置 token）" if sec.get('token') else "（token 未设置，放行）"))

    print("[demo] 三层装配完成，按 Ctrl+C 退出。")
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        sys_svc['watchdog'].stop()
        user_svc['watchdog'].stop()
        await fw.stop()


if __name__ == '__main__':
    try:
        asyncio.run(demo())
    except KeyboardInterrupt:
        print("\n[demo] 已退出。")
