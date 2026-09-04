"""TCP client for the ammeter emulators.

Protocol: connect, send the ammeter's command, read the reply until the emulator closes the connection.
An unknown command makes the emulator close the connection without replying.
"""

import math
import socket
import time


class AmmeterError(Exception):
    """Base class for all ammeter communication errors."""


class AmmeterConnectionError(AmmeterError):
    """The ammeter is unreachable or dropped the connection."""


class AmmeterTimeoutError(AmmeterError):
    """The ammeter did not answer in time."""


class AmmeterProtocolError(AmmeterError):
    """The ammeter answered with something that is not a measurement."""


def read_current(host: str, port: int, command: str, timeout_s: float = 2.0) -> float:
    """Send one measurement command and return the current in amperes.

    The timeout applies separately to connecting and to waiting for the reply.
    """
    chunks: list[bytes] = []
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall(command.encode())
            while chunk := sock.recv(1024):
                chunks.append(chunk)
    except socket.timeout as exc:
        raise AmmeterTimeoutError(f"{host}:{port} did not reply within {timeout_s}s") from exc
    except OSError as exc:
        raise AmmeterConnectionError(f"connection to {host}:{port} failed: {exc}") from exc

    reply = b"".join(chunks)
    if not reply:
        raise AmmeterProtocolError(
            f"{host}:{port} closed the connection without replying; is {command!r} the right command?"
        )
    try:
        current = float(reply.decode())
    except ValueError as exc:
        raise AmmeterProtocolError(f"{host}:{port} sent a non-numeric reply: {reply!r}") from exc
    if not math.isfinite(current):
        raise AmmeterProtocolError(f"{host}:{port} sent a non-finite value: {current}")
    return current


def is_listening(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=0.5).close()
    except OSError:
        return False
    return True


def wait_for_ammeter(host: str, port: int, timeout_s: float = 5.0) -> None:
    """Block until the ammeter accepts connections, or raise AmmeterConnectionError."""
    deadline = time.monotonic() + timeout_s
    while not is_listening(host, port):
        if time.monotonic() >= deadline:
            raise AmmeterConnectionError(f"{host}:{port} not reachable after {timeout_s}s")
        time.sleep(0.05)
