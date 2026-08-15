"""Tier 1: the verdicts that need no interpretation.

Three mechanical rules, tried in order: time at a place the user tagged as
accounted, media playing behind some other window, and idleness past a threshold
during a declared block. Anything else returns ``None``, which is not a failure
but the signal that a judgment is required and Tier 2 should run.

Two properties of this module are deliberate and worth stating, because both are
easy to erode later.

**This tier cannot produce DRIFT.** Mechanical facts are allowed to exonerate an
hour and to notice that nobody is there; they are not allowed to accuse someone
of doing the wrong thing. Deciding that a focused window is the wrong window is a
judgment about content, and judgments belong to a tier that has actually looked
at the title and can be argued with. ABSENT is not an exception to this: it says
no input arrived, which is an observation rather than a verdict about content,
and spec section 3.2 makes absence the specific event this system exists to catch.

**This tier never reads a window title.** A focus observation is
``"<wm_class>|<title>"`` and only the class half is ever parsed or repeated. The
title half is the most sensitive data the system holds, and reason strings are
persisted and displayed on a wall in a room, so the mechanical tier stays on the
harmless side of the separator.

The background-media rule (spec section 7.1) is the one that earns this tier its
place. Media playing while a *different* window has focus is listening, not
watching, so it resolves the study-music-versus-entertainment ambiguity
structurally, without classifying a single title. The mirror case, the media
application itself focused, is handed to Tier 2 rather than condemned here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from lifewatch.models import Interval, Klass, Observation

TIER = 1

PLAYING = "playing"

# Kinds this tier understands. ``place`` and ``media`` name a fact, so any sensor
# that can establish them is accepted. ``ms`` names a unit rather than a fact, so
# it is only read as idleness when the sensor filling the role of ``idle`` says
# it - that role name is part of the Sensor protocol and is what a Wayland or
# macOS replacement would keep.
KIND_PLACE = "place"
KIND_MEDIA = "media"
KIND_IDLE = "ms"
SENSOR_IDLE = "idle"


def tier1(
    observations: Sequence[Observation],
    block: Any,
    now: datetime,
    accounted_places: Iterable[str] = frozenset(),
    idle_threshold_s: int = 900,
) -> Interval | None:
    """Classify the moment ``now`` from recent observations, or decline to.

    ``block`` is whatever the contract considers current, or ``None`` when no
    block is running. Only ``planned_start`` is ever read from it, and only when
    it is there, so a caller may pass any object that stands for a block.

    Returns ``None`` when no mechanical rule applies. That return is a decision:
    it means this tier declines to guess and the caller should escalate to
    Tier 2. Nothing here ever falls back to a plausible class.
    """
    # Observations timestamped after ``now`` are not evidence about ``now``. A
    # replay that fed the whole log to every step would otherwise classify an
    # hour using facts from later in the day and stop being a replay.
    known = [o for o in observations if o.ts <= now]

    # Order matters, and absence must outrank background media.
    #
    # "Media playing while another window has focus" means listening while
    # working only if there is evidence of working. Sustained zero input is
    # evidence of NOT working, and it dominates: a video playing to an empty
    # chair is absence, not ambience.
    #
    # Getting this order wrong inverts the system's whole purpose. The failure
    # it produces is the exact one this project exists to catch: lying in bed
    # with something playing, scored as a productive session with background
    # music. Regression-tested in tests/test_tier1.py.
    verdict = (
        _accounted_place(known, accounted_places)
        or _absent(known, block, idle_threshold_s)
        or _background_media(known)
    )
    if verdict is None:
        return None

    klass, start, reason = verdict
    return Interval(
        start=_no_earlier_than_the_block(start, block, now),
        end=now,
        klass=klass,
        tier=TIER,
        reason=reason,
    )


# -- rule 1: accounted place -------------------------------------------------


def _accounted_place(
    observations: Sequence[Observation], accounted_places: Iterable[str]
) -> tuple[Klass, datetime, str] | None:
    """Time at a place the user tagged as spoken for: class, work, a shift.

    Runs first because it outranks everything below it. Sitting idle in a
    lecture theatre is not absence from a study block, and no amount of window
    evidence should override where the person physically is.
    """
    place = _latest(observations, KIND_PLACE)
    if place is None or place.value not in _as_set(accounted_places):
        return None
    return (
        Klass.ACCOUNTED,
        place.ts,
        f"place '{place.value}' is tagged accounted",
    )


def _as_set(accounted_places: Iterable[str]) -> set[str]:
    """Normalise the tag list, tolerating a single tag written as a bare string.

    ``accounted_places: campus`` in hand-edited YAML loads as ``str``, and
    ``"cam" in "campus"`` is true, so without this a typo in the config would
    silently account for the wrong places.
    """
    if isinstance(accounted_places, str):
        return {accounted_places}
    return set(accounted_places)


# -- rule 2: background media ------------------------------------------------


def _background_media(
    observations: Sequence[Observation],
) -> tuple[Klass, datetime, str] | None:
    """Media playing while some other window has focus: listening, not watching.

    When the media sensor can name its source and that source is the focused
    window, this rule declines rather than calling it drift, because a focused
    player might be a recorded lecture and only a look at the title can tell.

    When the sensor cannot name its source, the reading is ambient anyway, and
    the reason says the source was unattributed so the guess is visible. The
    asymmetry is intentional: the cost of a wrong ambient reading is one hour
    credited too generously, while the cost of a wrong drift reading is an
    escalation aimed at someone who was working, which is how an instrument like
    this gets uninstalled.
    """
    media = _latest(observations, KIND_MEDIA)
    if media is None or media.value.strip().lower() != PLAYING:
        return None

    focus = _latest(observations, "focus")
    if focus is None:
        return None
    focused_app = _application_of(focus.value)
    if not focused_app:
        return None

    source = media.meta.get("app") or media.meta.get("wm_class")
    if source and source.strip().lower() == focused_app.strip().lower():
        return None  # the player is the focused window: a Tier 2 candidate

    # Both facts only hold together from the later of the two observations.
    start = max(media.ts, focus.ts)
    if source:
        reason = f"media playing in '{source}' while '{focused_app}' has focus"
    else:
        reason = (
            f"media playing while '{focused_app}' has focus "
            "(media source unattributed)"
        )
    return (Klass.AMBIENT, start, reason)


def _application_of(focus_value: str) -> str:
    """Take the application class from a focus observation, discarding the title.

    See the module docstring: the title half never leaves this function.
    """
    return focus_value.split("|", 1)[0].strip()


# -- rule 3: idle during a block ---------------------------------------------


def _absent(
    observations: Sequence[Observation], block: Any, idle_threshold_s: int
) -> tuple[Klass, datetime, str] | None:
    """No input for longer than the threshold, during a block that was declared.

    Requires a block. Idleness with nothing declared is not absence from
    anything, and a system that classified an unclaimed evening as ABSENT would
    be inventing a commitment nobody made.
    """
    if block is None:
        return None

    idle = _latest(observations, KIND_IDLE, sensor=SENSOR_IDLE)
    if idle is None:
        return None
    try:
        idle_ms = int(float(idle.value))
    except (TypeError, ValueError):
        # A sensor that reports something unparseable is a broken sensor, not
        # evidence of absence.
        return None
    if idle_ms < idle_threshold_s * 1000:
        return None

    # Anchored to the measurement: at ``idle.ts`` the last input was ``idle_ms``
    # ago, so that is when the absence demonstrably began.
    start = idle.ts - timedelta(milliseconds=idle_ms)
    reason = (
        f"idle {idle_ms / 60000:.0f} min, past the "
        f"{idle_threshold_s / 60:.0f} min threshold, during a declared block"
    )
    return (Klass.ABSENT, start, reason)


# -- shared ------------------------------------------------------------------


def _latest(
    observations: Sequence[Observation], kind: str, sensor: str | None = None
) -> Observation | None:
    """The most recent observation of a kind, ties going to the later write."""
    chosen: Observation | None = None
    for obs in observations:
        if obs.kind != kind:
            continue
        if sensor is not None and obs.sensor != sensor:
            continue
        if chosen is None or obs.ts >= chosen.ts:
            chosen = obs
    return chosen


def _no_earlier_than_the_block(start: datetime, block: Any, now: datetime) -> datetime:
    """Keep an interval inside the block it is judging.

    An idle reading can reach back before a block began. Letting it charge that
    time to the block would make the instrument report minutes the user never
    promised, which is the same dishonesty in the other direction.
    """
    planned_start = getattr(block, "planned_start", None)
    if isinstance(planned_start, datetime) and start < planned_start:
        start = planned_start
    return min(start, now)
