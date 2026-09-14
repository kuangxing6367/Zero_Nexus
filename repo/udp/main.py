"""udp —— UDP 传输接口（机制包）。

框架提供的统一传输机制，下游不再自己写 socket。

稳定 API 表面：
    open(*, host='0.0.0.0', port=0, timeout=5) -> UdpSocket
    UdpSocket.send(data, addr) / .recv(bufsize) / .close()
"""
from __future__ import annotations

import socket


class UdpSocket:
    def __init__(self, host: str = "0.0.0.0", port: int = 0,
                 timeout: float = 5.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        if port:
            self.sock.bind((host, port))

    def send(self, data: bytes, addr):
        return self.sock.sendto(data, addr)

    def recv(self, bufsize: int = 65535):
        return self.sock.recvfrom(bufsize)

    def close(self):
        self.sock.close()


def open(*, host: str = "0.0.0.0", port: int = 0,
        timeout: float = 5.0) -> UdpSocket:
    """打开一个 UDP 套接字（port=0 表示由系统分配）。"""
    return UdpSocket(host, port, timeout)
