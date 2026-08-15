"""Channels an intervention can arrive through.

An effector is the last unit in the chain and the only one that touches the
outside world, which makes it the most likely to fail: a phone is off, a topic
has moved, the network is gone. So the contract here is narrower than it looks.

``deliver`` returns a ``Delivery`` and never raises. That is not politeness, it
is the design: the watcher is the thing keeping the instrument honest, and a
dead push channel must not be able to take it down. A silent instrument is worse
than no instrument, because the user believes it is still watching.

Each effector reports its own ``available()`` so an unconfigured channel is
skipped rather than failing, which is what lets someone clone this project and
run it with no push service at all - the wall effector alone is a working
system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from lifewatch.models import Intervention


@dataclass(frozen=True)
class Delivery:
    """What actually happened when an intervention was handed to a channel.

    The record exists because "we sent a notification" and "a notification
    arrived" are different claims, and only the second one is accountability.
    A failed delivery is data the watcher can act on - escalate elsewhere, or
    tell the user their channel is broken - which it could not do if the
    failure had been raised and swallowed somewhere up the stack.
    """

    effector: str
    ok: bool
    detail: str = ""


@runtime_checkable
class Effector(Protocol):
    """A way of reaching the user."""

    name: str

    def available(self) -> bool:  # pragma: no cover - protocol definition
        ...

    def deliver(self, iv: Intervention) -> Delivery:  # pragma: no cover
        ...


def deliver_all(effectors: Iterable[Effector], iv: Intervention) -> list[Delivery]:
    """Hand one intervention to every channel that is currently usable.

    Fault isolation is per effector, twice over: an availability check that
    throws disqualifies only that channel, and a ``deliver`` that throws - which
    a well-behaved effector never does, but a third-party one might - is caught
    here as well. One broken channel must never cost the user the others.
    """
    results: list[Delivery] = []
    for effector in effectors:
        name = getattr(effector, "name", effector.__class__.__name__)
        try:
            if not effector.available():
                continue
        except Exception:
            continue
        try:
            results.append(effector.deliver(iv))
        except Exception as exc:  # an effector that breaks its own contract
            results.append(Delivery(effector=name, ok=False, detail=str(exc)))
    return results
