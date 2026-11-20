from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator


class OfflineNetworkError(RuntimeError):
    pass


@contextmanager
def offline_network_guard() -> Iterator[None]:
    """Reject outbound socket connections for the duration of a comparison."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def blocked_connect(self, address):
        raise OfflineNetworkError(
            f"Network access is disabled during comparison: {address!r}"
        )

    def blocked_connect_ex(self, address):
        raise OfflineNetworkError(
            f"Network access is disabled during comparison: {address!r}"
        )

    def blocked_create_connection(address, *args, **kwargs):
        raise OfflineNetworkError(
            f"Network access is disabled during comparison: {address!r}"
        )

    socket.socket.connect = blocked_connect
    socket.socket.connect_ex = blocked_connect_ex
    socket.create_connection = blocked_create_connection
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.create_connection = original_create_connection
