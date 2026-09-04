import socket
import threading
import time

import pytest

from Ammeters.client import (
    AmmeterConnectionError,
    AmmeterProtocolError,
    AmmeterTimeoutError,
    read_current,
    wait_for_ammeter,
)
from src.utils.config import AmmeterSpec
from tests.conftest import free_port


def one_shot_server(*chunks: bytes, hold: bool = False) -> int:
    """Accept one connection, read the command, send the chunks (or hold the connection open) and close."""
    server = socket.socket()
    server.bind(("localhost", 0))
    server.listen()

    def serve() -> None:
        conn, _ = server.accept()
        with server, conn:
            conn.recv(1024)
            if hold:
                time.sleep(1.0)
            for chunk in chunks:
                conn.sendall(chunk)
                time.sleep(0.01)

    threading.Thread(target=serve, daemon=True).start()
    return int(server.getsockname()[1])


def test_read_current_returns_a_float(greenlee: AmmeterSpec) -> None:
    current = read_current(greenlee.host, greenlee.port, greenlee.command)
    assert isinstance(current, float)
    assert 0.01 <= current <= 100


def test_wrong_command_is_a_protocol_error(greenlee: AmmeterSpec) -> None:
    with pytest.raises(AmmeterProtocolError, match="without replying"):
        read_current(greenlee.host, greenlee.port, "MEASURE_GREENLEE")


def test_unreachable_port_is_a_connection_error() -> None:
    with pytest.raises(AmmeterConnectionError, match="failed"):
        read_current("localhost", free_port(), "x")


def test_silent_server_times_out() -> None:
    port = one_shot_server(hold=True)
    started = time.perf_counter()
    with pytest.raises(AmmeterTimeoutError, match="did not reply"):
        read_current("localhost", port, "x", timeout_s=0.2)
    assert time.perf_counter() - started < 1.0


def test_split_reply_is_read_completely() -> None:
    port = one_shot_server(b"12.", b"5")
    assert read_current("localhost", port, "x") == 12.5


@pytest.mark.parametrize("reply", [b"hello", b"", b"nan", b"inf"])
def test_bad_replies_are_protocol_errors(reply: bytes) -> None:
    port = one_shot_server(reply)
    with pytest.raises(AmmeterProtocolError):
        read_current("localhost", port, "x", timeout_s=1.0)


def test_wait_for_ammeter_gives_up() -> None:
    with pytest.raises(AmmeterConnectionError, match="not reachable"):
        wait_for_ammeter("localhost", free_port(), timeout_s=0.2)
