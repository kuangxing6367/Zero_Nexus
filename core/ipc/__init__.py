# -*- coding: utf-8 -*-
"""双进程 IPC 层（实现已迁至 core.ipc，原 framework.ipc）

负责单/双进程模式下跨进程通信：JsonRpc 协议、IPC 服务端/客户端、
远程 API 调用、远程 DB 代理、远程事务、远程路由、主机侧日志。
"""
from .protocol import JsonRpcConnection, IpcClosed, RemoteError
from .ipc_server import IpcServer
from .ipc_client import IpcClient
from .ipc_adapter import IpcAdapter
from .remote_api_caller import RemoteApiCaller, _NullConnection
from .core_runtime import CoreRuntime
from .remote_db import RemoteDatabase
from .remote_tx import RemoteTxManager
from .remote_route import RemoteRouteRegistry
from .host_log import IpcLogHandler
from .host_entry import host_entry

__all__ = [
    'JsonRpcConnection', 'IpcClosed', 'RemoteError', 'IpcServer', 'IpcClient',
    'IpcAdapter', 'RemoteApiCaller', '_NullConnection', 'CoreRuntime',
    'RemoteDatabase', 'RemoteTxManager', 'RemoteRouteRegistry', 'IpcLogHandler',
    'host_entry',
]
