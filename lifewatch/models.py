"""The values that move between units.

Sensors emit ``Observation``. The classifier turns spans of them into
``Interval``. The contract holds ``Block``. The watcher produces
``Intervention``, which is the only one of these that can refuse to exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Klass(str, Enum):
    """What a span of time turned out to be."""

    ALIGNED = "aligned"      # doing the declared thing
    AMBIENT = "ambient"      # media in the background while doing it
    DRIFT = "drift"          # awake, at the machine, doing something else
    ABSENT = "absent"        # not at the machine during a declared block
    ACCOUNTED = "accounted"  # class, work, sleep - spoken for, neither banked nor lost
    UNKNOWN = "unknown"      # no tier could decide; a question is pending


@dataclass(frozen=True)
class Observation:
    """One narrow fact, from one sensor, at one instant.

    Sensors report; they never judge. ``value`` is whatever the sensor saw, not
    what it means. All interpretation happens in the classifier, where it can be
    tested and corrected without touching capture.
    """

    ts: datetime
    sensor: str
    kind: str
    value: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Interval:
    """A classified span, and which tier decided it.

    ``tier`` is kept so the user can always ask why a verdict was reached, and
    so a wrong one can be traced to the rule that produced it.
    """

    start: datetime
    end: datetime
    klass: Klass
    tier: int
    reason: str


class BlockState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    MOVED = "moved"
    MISSED = "missed"
    EXCUSED = "excused"


@dataclass
class Block:
    """A declared span of time against a commitment.

    Note what is missing: there is no DISMISSED state. A block is started,
    completed, or moved somewhere else. Renegotiation is the only routine exit,
    and it costs a named replacement slot.
    """

    id: str
    commitment_id: str
    planned_start: datetime
    planned_end: datetime
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    state: BlockState = BlockState.PLANNED
    moved_to: str | None = None
    moved_from: str | None = None

    @property
    def planned_minutes(self) -> int:
        return int((self.planned_end - self.planned_start).total_seconds() // 60)


@dataclass(frozen=True)
class Intervention:
    """A decision to interrupt someone.

    An intervention cannot be constructed without a concrete next action. That
    is not a policy a caller can forget: it is a precondition of the type, so no
    code path anywhere in the system is capable of delivering a bare reproach.

    The reasoning, from the design spec section 9.2: shame produces avoidance,
    and avoidance is the failure this system exists to treat. Telling someone
    they are behind, with nothing to do about it, makes the bed more attractive
    rather than less. Loss and recovery appear in the same frame or not at all.
    """

    rung: int
    block_id: str
    message: str
    next_action: str
    requires_response: bool = False

    def __post_init__(self) -> None:
        if not self.next_action or not self.next_action.strip():
            raise ValueError(
                "an Intervention requires a concrete next_action; "
                "see design spec section 9.2"
            )
