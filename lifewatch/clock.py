"""Time, injected.

Every unit in lifewatch is time-dependent: a block is late, an idle period is
long, an escalation is due. If any of them read the wall clock directly, none of
them could be tested without waiting in real time, and a term-long instrument
would be verifiable only by living through a term.

So nothing outside this module may call ``datetime.now()`` or ``time.time()``.
That rule is enforced by ``tests/test_guards.py``, not by convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """A source of the current time."""

    def now(self) -> datetime:  # pragma: no cover - protocol definition
        ...


class SystemClock:
    """The real clock. The only place in the package that reads wall time."""

    def now(self) -> datetime:
        return datetime.now()


class FakeClock:
    """A clock that moves only when told to.

    Lets a whole simulated week run in milliseconds, so escalation sequences are
    asserted exactly rather than sampled.
    """

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
