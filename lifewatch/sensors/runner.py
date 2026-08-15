"""The poll loop: ask every sensor, write what changed.

Three properties matter here, and each one is a decision about how the
instrument fails.

**Nothing is written twice.** A value identical to the last one recorded for
the same sensor and kind is suppressed until the heartbeat elapses. A term of
15-second polling would otherwise be a few million rows saying the same thing,
and the heartbeat is what keeps the log honest anyway: a gap in it then means
the daemon was down, not that nothing changed.

**A broken sensor is skipped, never fatal.** One instrument throwing must not
take the loop down, because a system whose subject is elapsed time cannot
recover the hours it was not running for. Failures are logged loudly for the
same reason: an instrument that dies quietly is worse than no instrument.

**Availability is asked once, not every tick.** ``available()`` is a real
probe, not a flag read: the window sensor's costs two ``xprop`` subprocesses.
Asking it before every poll would roughly double an already substantial
process count on the laptop the user is trying to study on, which spec
sections 5 and 7 both treat as a design constraint rather than a nicety.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Sequence

from lifewatch.clock import Clock
from lifewatch.models import Observation
from lifewatch.sensors import Sensor
from lifewatch.store import Store

log = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_S = 600


class _SensorState:
    """One sensor plus what the runner has learned about it.

    ``available`` is ``None`` for "not asked yet", which is also the state a
    failure resets it to, so the next tick re-probes rather than assuming.
    """

    __slots__ = ("sensor", "available")

    def __init__(self, sensor: Sensor) -> None:
        self.sensor = sensor
        self.available: Optional[bool] = None


class _LastWrite:
    """The most recent row written for one (sensor, kind) pair."""

    __slots__ = ("value", "meta", "ts")

    def __init__(self, obs: Observation) -> None:
        self.value = obs.value
        # Copied: an observation's meta is a plain dict and a sensor is free to
        # reuse the one it handed over.
        self.meta = dict(obs.meta)
        self.ts = obs.ts


class Runner:
    """Polls a set of sensors and appends what is new to the store."""

    def __init__(
        self,
        sensors: Sequence[Sensor],
        store: Store,
        clock: Clock,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
    ) -> None:
        self.store = store
        self.clock = clock
        self.heartbeat_s = heartbeat_s
        self._states = [_SensorState(sensor) for sensor in sensors]
        self._last: dict[tuple[str, str], _LastWrite] = {}

    @property
    def sensors(self) -> list[Sensor]:
        return [state.sensor for state in self._states]

    def tick(self) -> int:
        """Poll every available sensor once. Returns rows written.

        Deliberately not gated on each sensor's ``poll_interval_s``: the caller
        owns the cadence, and a runner that skipped sensors on its own would
        make a replay depend on how often the harness happened to tick.
        """
        written = 0
        for state in self._states:
            if not self._is_available(state):
                continue
            written += self._service(state)
        return written

    def _is_available(self, state: _SensorState) -> bool:
        """Probe availability at most once, and remember the answer.

        A sensor that reports unavailable is not probed again for the life of
        this runner. That is the trade the process count buys: this machine
        cannot answer that question, which is a fact about the machine and not
        a transient, and the daemon is restarted with the session anyway. A
        sensor that fails *while working* is a different case and is re-probed
        below.
        """
        if state.available is None:
            try:
                state.available = bool(state.sensor.available())
            except Exception:
                # A sensor is supposed to answer this without raising. One that
                # raises is broken in a way polling will not fix.
                log.exception(
                    "sensor %s raised while reporting availability; skipping it",
                    state.sensor.name,
                )
                state.available = False
            if not state.available:
                log.info(
                    "sensor %s is unavailable on this machine; skipping it",
                    state.sensor.name,
                )
        return state.available

    def _service(self, state: _SensorState) -> int:
        """Poll one sensor and write what it says, absorbing any failure."""
        written = 0
        try:
            for obs in state.sensor.poll(self.clock.now()):
                if self._should_write(obs):
                    self.store.append(obs)
                    self._last[(obs.sensor, obs.kind)] = _LastWrite(obs)
                    written += 1
        except Exception:
            log.exception("sensor %s failed this tick", state.sensor.name)
            # Forgotten rather than set False, so the next tick asks again. A
            # sensor whose failure was the display server going away should be
            # skipped once its own available() says so, not on this runner's
            # guess about what the exception meant.
            state.available = None
        return written

    def _should_write(self, obs: Observation) -> bool:
        """Write on change, or once the heartbeat has elapsed.

        Change means the whole payload, meta included, not the value alone. The
        media sensor reports a state word plus which application produced it,
        and ``playing`` behind a text editor versus ``playing`` in the focused
        window are opposite verdicts in Tier 1 carrying the same value. Keyed
        on the value alone, the second would be suppressed as a repeat and the
        log would keep asserting the first.
        """
        last = self._last.get((obs.sensor, obs.kind))
        if last is None:
            return True
        if last.value != obs.value or last.meta != obs.meta:
            return True
        return self._heartbeat_elapsed(last.ts, obs.ts)

    def _heartbeat_elapsed(self, last_ts: datetime, now: datetime) -> bool:
        """Measured in observation time, not wall time.

        A replayed log has to produce the same rows it produced live, and its
        observations carry the timestamps they were recorded with.
        """
        return now - last_ts >= timedelta(seconds=self.heartbeat_s)
