"""The school pack: course fields, the grade model, and campus mode.

The engine holds no domain knowledge, so everything that is true about being a
student rather than true about accountability lives here. ``pack.yaml`` declares
the shape of a course commitment; this module holds the two calculations that
only make sense once that shape exists.

Two properties are worth stating because they are the point rather than an
implementation detail:

* Nothing here reads the clock. ``campus_gaps`` is handed the day it should
  reason about, which is what lets a whole simulated term replay in
  milliseconds.
* ``grade_needed`` is allowed to answer above 1.0. See its docstring; the short
  version is that an honest impossible answer is worth more than a comfortable
  wrong one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "load_pack",
    "commitment_fields",
    "grade_needed",
    "running_grade",
    "campus_gaps",
]

PACK_PATH = Path(__file__).parent / "pack.yaml"

_TIME_FORMATS = ("%H:%M", "%H:%M:%S")


def load_pack() -> dict[str, Any]:
    """Read ``pack.yaml``.

    The declaration is loaded rather than mirrored in Python so there is exactly
    one place where "a course has a section and an instructor" is written down.
    A second copy in code would drift from the one the wizard renders.
    """
    return yaml.safe_load(PACK_PATH.read_text()) or {}


_PACK = load_pack()


def commitment_fields() -> list[dict[str, Any]]:
    """The extra fields the wizard collects for a school commitment."""
    return list(_PACK.get("commitment_fields") or [])


# -- grade model ----------------------------------------------------------


def _weight_of(item: Mapping[str, Any]) -> float:
    if "weight" not in item:
        raise ValueError(f"grade item {item.get('name', item)!r} has no weight")
    weight = float(item["weight"])
    if weight < 0:
        raise ValueError(f"grade item {item.get('name', item)!r} has a negative weight")
    return weight


def grade_needed(items: Iterable[Mapping[str, Any]], target_fraction: float) -> float:
    """What every remaining item must score, uniformly, to reach the target.

    ``items`` are ``{name, weight, score}``; ``score`` is ``None`` (or absent)
    until the item is graded. Weights may be fractions that sum to 1.0 or
    percentage points that sum to 100 -- the answer is scaled by the total
    weight either way, so a syllabus can be entered in whichever form it was
    published.

    The return value is deliberately unclamped:

    * **Above 1.0** means the target cannot be reached even with perfect scores
      on everything left. That is the answer, and it is the one worth having.
      Clamping to 1.0 would report "you need 100%" to someone whose target is
      already gone, which keeps them spending hours on an outcome that no longer
      exists instead of reallocating them to one that does.
    * **At or below 0.0** means the target is already secured; the magnitude is
      the slack.
    * **Infinite** means nothing is left to be graded and the target was missed.

    Raises ``ValueError`` for a gradebook with no weight in it, because there is
    no honest number to return for one.
    """
    total_weight = 0.0
    earned = 0.0
    remaining_weight = 0.0

    for item in items:
        weight = _weight_of(item)
        total_weight += weight
        score = item.get("score")
        if score is None:
            remaining_weight += weight
        else:
            earned += weight * float(score)

    if total_weight <= 0:
        raise ValueError("a gradebook with no weighted items has no answer")

    shortfall = float(target_fraction) * total_weight - earned

    if remaining_weight <= 0:
        # Nothing left to score. The grade is whatever it is.
        return 0.0 if shortfall <= 0 else float("inf")

    return shortfall / remaining_weight


def running_grade(items: Iterable[Mapping[str, Any]]) -> float | None:
    """The grade so far, over graded items only.

    Ungraded items are excluded rather than counted as zero. Dividing by the
    whole term's weight in week three reports a student who has done well on
    everything so far as failing, which is both false and precisely the kind of
    discouragement the design spec section 9.2 exists to prevent.

    Returns ``None`` when nothing has been graded yet, because "no data" and
    "zero" are different statements.
    """
    scored_weight = 0.0
    earned = 0.0
    for item in items:
        score = item.get("score")
        if score is None:
            continue
        weight = _weight_of(item)
        scored_weight += weight
        earned += weight * float(score)

    if scored_weight <= 0:
        return None
    return earned / scored_weight


# -- campus mode ----------------------------------------------------------


def _as_time(value: Any, field: str) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        for fmt in _TIME_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).time()
            except ValueError:
                continue
    raise ValueError(f"meeting {field} must be HH:MM, got {value!r}")


def _matches_day(declared: Any, day: date) -> bool:
    """Does a meeting's declared day fall on this date.

    Accepts the full weekday name or its three-letter abbreviation, in any case,
    because a timetable is copied by hand and "Mon" is what people write.
    """
    wanted = day.strftime("%A").lower()
    got = str(declared).strip().lower()
    return got == wanted or (len(got) == 3 and wanted.startswith(got))


def campus_gaps(
    meetings: Sequence[Mapping[str, Any]],
    day: date,
    min_minutes: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """The usable gaps between a day's meetings.

    Time between two classes on campus is the cheapest study time in the week:
    already on site, already in the mode, with a hard stop that makes the block
    finite. This surfaces those windows so the contract can propose them.

    Only the space *between* meetings is returned. Before the first and after
    the last is not a gap, it is the rest of the day, and it belongs to whatever
    the weekly template says rather than to campus mode.

    ``min_minutes`` defaults to the pack's ``campus_mode.min_gap_minutes``: a
    ten-minute passing period is not a study block, and suggesting it teaches
    the user to ignore suggestions. Overlapping meetings are merged, so a
    double-booked timetable produces no backwards gap.
    """
    if min_minutes is None:
        min_minutes = int((_PACK.get("campus_mode") or {}).get("min_gap_minutes", 30))
    if min_minutes < 0:
        raise ValueError("min_minutes cannot be negative")

    if isinstance(day, datetime):
        day = day.date()

    spans: list[tuple[datetime, datetime]] = []
    for meeting in meetings:
        if "day" not in meeting:
            raise ValueError(f"meeting {meeting!r} does not say which day it is on")
        if not _matches_day(meeting["day"], day):
            continue
        if "start" not in meeting or "end" not in meeting:
            raise ValueError(f"meeting {meeting!r} needs a start and an end")
        start = datetime.combine(day, _as_time(meeting["start"], "start"))
        end = datetime.combine(day, _as_time(meeting["end"], "end"))
        if end <= start:
            raise ValueError(f"meeting {meeting!r} ends before it starts")
        spans.append((start, end))

    spans.sort()

    threshold = timedelta(minutes=min_minutes)
    gaps: list[tuple[datetime, datetime]] = []
    covered_until: datetime | None = None
    for start, end in spans:
        if (
            covered_until is not None
            and start > covered_until
            and start - covered_until >= threshold
        ):
            gaps.append((covered_until, start))
        if covered_until is None or end > covered_until:
            covered_until = end
    return gaps
