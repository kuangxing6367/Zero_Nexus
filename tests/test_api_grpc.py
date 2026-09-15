# -*- coding: utf-8 -*-
"""gRPC 功能域测试：拉起真实 gRPC server，用 channel 调 Health / ListPlugins。"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import software.extensions.webui.grpc as gr
from software.extensions.webui.grpc import _HAS_GRPC


def _free_port():
    s = socket.socket()
    s.bind(('localhost', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakePluginLoader:
    def get_loaded_plugins(self):
        return {'p1': 1}


class FakeServices:
    def all(self):
        return {'ws_server': object()}


class FakeBus:
    _subscribers = {}

    def emit(self, *args, **kwargs):
        return None


class FakeFW:
    plugin_loader = FakePluginLoader()
    services = FakeServices()
    event_bus = FakeBus()


def test_grpc_server():
    if not _HAS_GRPC:
        print("test_api_grpc: SKIP (grpcio 未安装)")
        return

    import grpc
    import zeronus_pb2
    import zeronus_pb2_grpc

    port = _free_port()
    srv = gr.start_grpc_server(FakeFW(), 'localhost', port)
    assert srv is not None, "gRPC server 启动失败"

    try:
        with grpc.insecure_channel(f"localhost:{port}") as channel:
            stub = zeronus_pb2_grpc.ZeronusStub(channel)
            health = stub.Health(zeronus_pb2.HealthRequest())
            assert health.status == 'ok', health
            print("test_api_grpc Health: PASS")

            plist = stub.ListPlugins(zeronus_pb2.ListRequest())
            assert [p.name for p in plist.plugins] == ['p1']
            print("test_api_grpc ListPlugins: PASS")

            slist = stub.ListServices(zeronus_pb2.ListRequest())
            assert [s.name for s in slist.services] == ['ws_server']
            print("test_api_grpc ListServices: PASS")
    finally:
        srv.stop(0)


if __name__ == '__main__':
    test_grpc_server()
    print("DONE")
