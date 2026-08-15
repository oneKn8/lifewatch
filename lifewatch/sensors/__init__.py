"""Sensors: one narrow factual question each.

A sensor answers what is true right now and stops there. ``window`` reports a
title; it never decides whether that title is study or drift. All judgment lives
in the classifier, where it can be tested, configured and corrected without
touching capture. A sensor that judged would bake one person's habits into the
instrument, and the engine holds no domain knowledge.

Two properties are load-bearing:

**OS access is injected.** Every sensor takes a ``reader`` callable and defaults
it to the real one. That is what lets the whole suite run with no X11 and no
wireless interface, and what lets a Wayland contributor supply one replacement
reader without touching the runner, the store or anything above them.

**Unavailability is normal.** ``available()`` answering ``False`` means this
machine cannot answer this question at all, and the runner skips that sensor
rather than failing. A machine with no wireless is not a broken installation.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

from lifewatch.models import Observation

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance, typing only
    from lifewatch.config import Config

__all__ = ["Sensor", "FakeSensor", "default_sensors"]


@runtime_checkable
class Sensor(Protocol):
    """What every sensor must be, structurally.

    ``poll`` takes ``now`` rather than reading a clock, so a recorded log can be
    replayed at any speed and a simulated week runs in milliseconds. It must not
    block longer than ``poll_interval_s``; the OS readers enforce that with
    subprocess timeouts.
    """

    name: str
    poll_interval_s: int

    def available(self) -> bool:  # pragma: no cover - protocol definition
        ...

    def poll(self, now: datetime) -> list[Observation]:  # pragma: no cover
        ...


class FakeSensor:
    """A sensor that emits a prepared script instead of touching the machine.

    One observation per ``poll``, in order, then nothing. That matches how the
    real sensors behave under the runner -- a poll yields at most one reading --
    so a unit tested against this double is tested against the real cadence.

    Scripted timestamps are preserved rather than restamped with ``now``,
    because a script is a recording: the point of replaying one is to reproduce
    the timings that produced a bug.
    """

    def __init__(
        self,
        name: str,
        scripted: Sequence[Observation],
        poll_interval_s: int = 15,
        is_available: bool = True,
    ) -> None:
        self.name = name
        self.poll_interval_s = poll_interval_s
        # Copied, so a caller can hold on to the script it passed in and still
        # trust what it says afterwards.
        self._scripted = list(scripted)
        self._is_available = is_available

    def available(self) -> bool:
        return self._is_available

    def poll(self, now: datetime) -> list[Observation]:
        if not self._scripted:
            return []
        return [self._scripted.pop(0)]


def default_sensors(config: "Config") -> list[Sensor]:
    """The three Stage 1 sensors, wired to their real OS readers.

    Imports live inside the function so that ``import lifewatch.sensors`` costs
    nothing and cannot fail on a machine missing an optional dependency of a
    sensor the caller was never going to use.

    The ``presence`` sensor is deliberately absent: it is Stage 2, and it is the
    only sensor that touches a camera.
    """
    from lifewatch.sensors.idle import IdleSensor
    from lifewatch.sensors.network import NetworkSensor
    from lifewatch.sensors.window import WindowSensor

    return [WindowSensor(), IdleSensor(), NetworkSensor(config)]
