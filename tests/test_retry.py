import time

import pytest

from Ammeters.client import AmmeterConnectionError, AmmeterError, AmmeterProtocolError, AmmeterTimeoutError
from src.testing.ammeter import Retrying


class Flaky:
    """A measure callable that raises `error` for its first `failures` calls, then returns 1.0."""

    def __init__(self, failures: int, error: AmmeterError) -> None:
        self.calls = 0
        self._failures = failures
        self._error = error

    def __call__(self) -> float:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return 1.0


@pytest.mark.parametrize("error", [AmmeterConnectionError("refused"), AmmeterTimeoutError("no reply")])
def test_a_transient_failure_is_retried(error: AmmeterError) -> None:
    flaky = Flaky(failures=1, error=error)
    assert Retrying(flaky, attempts=2, backoff_s=0.0)() == 1.0
    assert flaky.calls == 2


def test_a_protocol_error_is_not_retried() -> None:
    """The one that matters: an unanswered or non-numeric reply is a wrong command or port, so it is
    identical on every attempt, and retrying only delays the report of a configuration fault."""
    flaky = Flaky(failures=1, error=AmmeterProtocolError("closed the connection without replying"))
    with pytest.raises(AmmeterProtocolError):
        Retrying(flaky, attempts=5, backoff_s=0.0)()
    assert flaky.calls == 1


def test_a_bare_ammeter_error_is_not_retried() -> None:
    """FaultInjector raises bare AmmeterError; keeping it out of TRANSIENT_ERRORS is what stops a
    simulated failure rate from being partly retried away and lying in the archived metadata."""
    flaky = Flaky(failures=1, error=AmmeterError("simulated fault"))
    with pytest.raises(AmmeterError):
        Retrying(flaky, attempts=3, backoff_s=0.0)()
    assert flaky.calls == 1


def test_exhausting_the_attempts_reraises_the_last_error_unchanged() -> None:
    flaky = Flaky(failures=99, error=AmmeterTimeoutError("no reply within 2.0s"))
    with pytest.raises(AmmeterTimeoutError, match="no reply within 2.0s"):
        Retrying(flaky, attempts=3, backoff_s=0.0)()
    assert flaky.calls == 3


def test_one_attempt_calls_through_exactly_once() -> None:
    flaky = Flaky(failures=1, error=AmmeterConnectionError("refused"))
    with pytest.raises(AmmeterConnectionError):
        Retrying(flaky, attempts=1)()
    assert flaky.calls == 1


def test_zero_attempts_is_rejected() -> None:
    with pytest.raises(ValueError, match="attempts must be at least 1, got 0"):
        Retrying(lambda: 1.0, attempts=0)


def test_a_negative_backoff_is_rejected() -> None:
    with pytest.raises(ValueError, match="backoff must not be negative"):
        Retrying(lambda: 1.0, backoff_s=-0.1)


def test_backoff_grows_linearly_with_the_attempt() -> None:
    """Lower bound only: an upper bound on wall-clock timing is how a suite becomes flaky."""
    flaky = Flaky(failures=2, error=AmmeterConnectionError("refused"))
    started = time.perf_counter()
    assert Retrying(flaky, attempts=3, backoff_s=0.01)() == 1.0
    assert time.perf_counter() - started >= 0.01 + 0.02  # attempt 1 waits 1x, attempt 2 waits 2x
    assert flaky.calls == 3
