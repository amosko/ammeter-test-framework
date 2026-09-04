"""TCP client for the ammeter emulators.

Protocol: connect, send the ammeter's command, receive one measurement as text.
An unknown command makes the emulator close the connection without a reply.
"""
import math
import socket
import time


class AmmeterError(Exception):
    """Base class for all ammeter communication errors."""


class AmmeterConnectionError(AmmeterError):
    """The ammeter is unreachable."""


class AmmeterTimeoutError(AmmeterError):
    """The ammeter did not answer in time."""


class AmmeterProtocolError(AmmeterError):
    """The ammeter answered with something that is not a measurement."""


def read_current(host: str, port: int, command: bytes, timeout_s: float = 2.0) -> float:
    """Send one measurement command and return the current in amperes."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall(command)
            reply = sock.recv(1024)
    except socket.timeout as exc:
        raise AmmeterTimeoutError(f"{host}:{port} did not reply within {timeout_s}s") from exc
    except OSError as exc:
        raise AmmeterConnectionError(f"cannot connect to {host}:{port}: {exc}") from exc

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


def wait_for_ammeter(host: str, port: int, timeout_s: float = 5.0) -> None:
    """Block until the ammeter accepts connections, or raise AmmeterConnectionError."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise AmmeterConnectionError(f"{host}:{port} not reachable after {timeout_s}s: {exc}") from exc
            time.sleep(0.05)


def request_current_from_ammeter(port: int, command: bytes) -> float:
    """Read one value from a local emulator and print it (kept for main.py)."""
    current = read_current("localhost", port, command)
    print(f"Received current measurement from port {port}: {current} A")
    return current
