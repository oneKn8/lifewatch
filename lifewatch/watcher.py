"""The policy engine: whether to interrupt someone, and how hard.

The watcher is the unit where this project either becomes useful or becomes
cruelty, so two rules are load-bearing and both are enforced structurally rather
than by care.

**No escalation without a resolvable next action** (spec section 9.2). Every
intervention names a concrete thing to do now, resolved from the contract and
the commitment the block was declared against. When nothing resolves, the ladder
stays silent. It does not fall back to "get back to work", because that sentence
is the failure mode wearing the costume of a fix: shame produces avoidance, and
avoidance is the disease being treated. An interruption that reports a loss with
no recovery in the same frame makes not-starting more attractive, not less. The
``Intervention`` type refuses to be constructed without a next action, so the
worst a bug here can do is stay quiet.

**Discretion runs toward mercy only** (spec section 9.3). A judge may lower the
rung the ladder computed and may never raise it, so the harshest possible
behaviour of this system is fully determined by the configuration the user wrote
themselves. A judge that fails, or answers nonsense, changes nothing: it can
neither escalate nor excuse, because both of those are ways to lose the
instrument, one by harassment and one by a trivially disabled watchdog.

What the judge is given is a dictionary of counts and durations. Not a filtered
one, an exhaustively typed one: every key is on an allowlist and every value is a
number or ``None``, checked before the call. A payload that cannot hold a string
cannot hold a document name or a URL, which is the whole reason the optional
cloud judge in spec section 7 is safe to offer while Tier 2 must stay local.

``evaluate`` decides and does not deliver. It writes nothing, mutates nothing,
and answers the same question the same way however often it is asked, so the
sensor loop and the wall view can both call it without changing each other's
answer. ``escalate`` is the delivery-side wrapper that fires each rung once.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Callable

from lifewatch.clock import Clock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.models import Block, BlockState, Intervention, Klass
from lifewatch.store import Store

logger = logging.getLogger(__name__)

# States that mean the obligation is already answered. A RUNNING block is being
# kept, a COMPLETED one was kept, a MOVED one was renegotiated, and an EXCUSED
# one was forgiven. Everything else is silence, which is the only thing this
# system is ever harsh toward (spec section 3.3).
SETTLED_STATES = (
    BlockState.RUNNING,
    BlockState.COMPLETED,
    BlockState.MOVED,
    BlockState.EXCUSED,
)

# Keys the judge may be given. The allowlist is the enforcement, not the
# documentation: anything not named here raises rather than being sent.
DERIVED_FIELDS = frozenset(
    {
        "ladder_rung",
        "minutes_elapsed",
        "block_planned_minutes",
        "dead_blocks_today",
        "idle_minutes",
        "passes_remaining",
        "integrity",
        "debt_minutes_today",
        "hour_of_day",
        "weekday",
    }
)

# How far back to look for the last idle reading. Bounded so the query stays
# cheap over a term-long log, and long enough that "no input since last night"
# is still visible as one number this morning.
IDLE_LOOKBACK = timedelta(hours=24)

# Fields a next-action item may carry its text in, in the order they are tried.
TASK_TEXT_KEYS = ("text", "action", "label", "name", "task")


class Watcher:
    """Compares what was promised against what is happening, and decides.

    ``judge`` is an optional callable taking the derived-state dictionary and
    returning either an integer rung or a mapping with ``rung`` and a
    justification. It is a plain attribute so it can be attached, swapped or
    removed at runtime: the user turning discretion off must not require
    rebuilding the watcher.
    """

    def __init__(
        self,
        contract: Contract,
        store: Store,
        config: Config,
        clock: Clock,
        judge: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.contract = contract
        self.store = store
        self.config = config
        self.clock = clock
        self.judge = judge

        # Delivery bookkeeping for `escalate`. Deliberately not consulted by
        # `evaluate`, which stays a pure function of the world at `now`.
        self._delivered: set[tuple[str, int]] = set()
        self._lock = threading.Lock()

    # -- the decision ------------------------------------------------------

    def evaluate(self, now: datetime) -> Intervention | None:
        """What escalation, if any, the current moment calls for.

        Returns ``None`` for silence. Silence is the answer when the user
        declared sick mode, when no block covers ``now``, when the block is
        already settled, when a pass was spent against it, when no concrete next
        action resolves, and when the ladder has not yet reached its first rung.

        Calling this repeatedly for the same moment returns the same answer and
        has no side effects.
        """
        if self.contract.is_silenced(now):
            return None

        block = self.contract.current_block(now)
        if block is None:
            return None
        if block.state in SETTLED_STATES:
            return None
        if self._pass_spent_against(block, now):
            return None

        next_action = self._resolve_next_action(block)
        if next_action is None:
            # Spec section 9.2. Nothing to do about it means nothing to say
            # about it. Logged at debug so a config missing its next actions is
            # diagnosable, and never louder, because this path is normal.
            logger.debug(
                "no next action resolves for block %s (%s); staying silent",
                block.id,
                block.commitment_id,
            )
            return None

        ladder = self._ladder()
        entry = _due_entry(ladder, _minutes_between(block.planned_start, now))
        if entry is None:
            return None

        entry = self._apply_discretion(entry, ladder, block, now)
        if entry is None:
            return None

        return Intervention(
            rung=int(entry["rung"]),
            block_id=block.id,
            message=self._message(block, now),
            next_action=next_action,
            requires_response=bool(entry["requires_response"]),
        )

    def escalate(self, now: datetime) -> Intervention | None:
        """``evaluate``, but only when the answer is one that has not been sent.

        A loop that owns a phone needs "is there something new" rather than "what
        is the state", and the difference between the two is a notification every
        fifteen seconds. Each rung is delivered once per block; a fresh block is
        a fresh obligation and gets the whole ladder again.
        """
        iv = self.evaluate(now)
        if iv is None:
            return None
        key = (iv.block_id, iv.rung)
        with self._lock:
            if key in self._delivered:
                return None
            self._delivered.add(key)
        return iv

    # -- the next action, which is the precondition for all of the above ---

    def _resolve_next_action(self, block: Block) -> str | None:
        """The concrete thing to do now, or ``None`` if there is not one.

        Resolution is deliberately narrow. The engine holds no domain knowledge,
        so it does not know what a problem set is; it knows that a commitment may
        carry a queue of things to do and that the first one still open is the
        answer. A pack fills that queue with whatever its domain calls work.

        The commitment name is prefixed so the intervention is legible on a wall
        across a room, and skipped when the task already names it, so nothing
        reads "COURSE-101: COURSE-101 problem set 2".
        """
        commitment = self._commitment_for(block.commitment_id)
        if commitment is None:
            return None

        task = _first_open_task(commitment)
        if task is None:
            return None

        name = _text_of(commitment.get("label")) or _text_of(commitment.get("id"))
        if not name or name.lower() in task.lower():
            return task
        return f"{name}: {task}"

    def _commitment_for(self, commitment_id: str) -> Mapping[str, Any] | None:
        for commitment in self.config.commitments or ():
            if not isinstance(commitment, Mapping):
                continue
            if _text_of(commitment.get("id")) == commitment_id:
                return commitment
        return None

    def _message(self, block: Block, now: datetime) -> str:
        """Plain statement of the discrepancy. No adjectives, no reproach.

        The recovery half of the frame is the ``next_action`` the effector
        renders beside this, so the copy here only has to be accurate.
        """
        commitment = self._commitment_for(block.commitment_id)
        name = block.commitment_id
        if commitment is not None:
            name = _text_of(commitment.get("label")) or name

        elapsed = int(_minutes_between(block.planned_start, now))
        if elapsed <= 0:
            return f"{name} starts now. Nothing is running."
        return (
            f"{name} is {elapsed} minutes into a {block.planned_minutes} minute "
            "block with nothing running."
        )

    def _pass_spent_against(self, block: Block, now: datetime) -> bool:
        """Was one of this week's passes spent inside this block's window.

        Spec section 9.1 cancels escalation on a pass, and the contract counts
        passes by week rather than binding them to a block, so the link is made
        here by time: a pass spent while this block was open was spent on it.
        """
        for spent_at in self.contract.passes_used_at(now):
            if block.planned_start <= spent_at <= now:
                return True
        return False

    # -- the ladder --------------------------------------------------------

    def _ladder(self) -> list[dict[str, Any]]:
        """The configured rungs, normalised and sorted by when they come due.

        Only what the configuration contains is ever returned, so no rung above
        the user's own ceiling can be emitted. In Stage 1 that ceiling is rung 2:
        rungs 3 and 4 have no effector yet, and an escalation that is decided but
        undeliverable is worse than none, because the instrument's credibility
        rests on it always noticing.
        """
        rungs: list[dict[str, Any]] = []
        for raw in self.config.ladder or ():
            if not isinstance(raw, Mapping):
                continue
            try:
                rung = int(raw["rung"])
                after_minutes = float(raw.get("after_minutes", 0))
            except (KeyError, TypeError, ValueError):
                logger.warning("skipping malformed ladder entry: %r", raw)
                continue
            rungs.append(
                {
                    "rung": rung,
                    "after_minutes": after_minutes,
                    "effector": raw.get("effector"),
                    "requires_response": bool(raw.get("requires_response", False)),
                }
            )
        rungs.sort(key=lambda entry: (entry["after_minutes"], entry["rung"]))
        return rungs

    # -- discretion --------------------------------------------------------

    def _apply_discretion(
        self,
        entry: dict[str, Any],
        ladder: list[dict[str, Any]],
        block: Block,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Let the judge soften the ladder's verdict. Never let it sharpen one.

        Returns the entry to deliver, or ``None`` when the judge chose a rung
        below the whole ladder, which is mercy taken to its limit and is still a
        legal answer.
        """
        if self.judge is None:
            return entry

        ladder_rung = int(entry["rung"])
        payload = self._derived_state(block, now, ladder_rung)

        try:
            raw = self.judge(payload)
        except Exception as exc:
            # A judge that cannot be reached is not a reason to escalate and not
            # a reason to excuse. The ladder the user configured stands.
            logger.warning("judge unavailable, ladder stands at rung %s: %s",
                           ladder_rung, exc)
            return entry

        verdict, justification = _parse_verdict(raw)
        if verdict is None:
            if raw is not None:
                logger.warning(
                    "judge returned no usable rung (%r), ladder stands at rung %s",
                    raw,
                    ladder_rung,
                )
            return entry

        # The one-way valve. min() is the whole of "mercy only".
        chosen = min(ladder_rung, verdict)
        if chosen >= ladder_rung:
            return entry

        softened = _entry_at_or_below(ladder, chosen)
        if softened is None:
            logger.info(
                "judge lowered block %s below rung %s to silence: %s",
                block.id,
                ladder_rung,
                justification or "no justification given",
            )
            return None

        logger.info(
            "judge lowered block %s from rung %s to rung %s: %s",
            block.id,
            ladder_rung,
            softened["rung"],
            justification or "no justification given",
        )
        return softened

    def _derived_state(
        self, block: Block, now: datetime, ladder_rung: int
    ) -> dict[str, Any]:
        """Counts and durations. Never a title, a URL, or a raw observation.

        Spec section 7 draws the privacy boundary at what leaves the machine, so
        this is the one payload in the system that may be sent to a model the
        user does not own. It is validated against an allowlist before it is
        handed over, which turns "we are careful about what we send" into
        something that fails loudly instead of leaking quietly.

        ``dead_blocks_today`` counts the current block too. A judge asking "is
        this the first one or the third" wants today's total, and excluding the
        one being decided would answer a subtly different question.
        """
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        aligned, claimed = self._minutes_today(day_start, now)

        payload: dict[str, Any] = {
            "ladder_rung": ladder_rung,
            "minutes_elapsed": round(_minutes_between(block.planned_start, now), 1),
            "block_planned_minutes": block.planned_minutes,
            "dead_blocks_today": self._dead_blocks_today(now),
            "idle_minutes": self._idle_minutes(now),
            "passes_remaining": self.contract.passes_remaining(now),
            "integrity": round(aligned / claimed, 3) if claimed > 0 else None,
            "debt_minutes_today": self.contract.debt_minutes(now.date()),
            "hour_of_day": now.hour,
            "weekday": now.weekday(),
        }
        _guard_derived(payload)
        return payload

    def _dead_blocks_today(self, now: datetime) -> int:
        """Blocks that came due today and were neither started nor renegotiated."""
        return sum(
            1
            for block in self.contract.blocks()
            if block.planned_start.date() == now.date()
            and block.planned_start <= now
            and block.state not in SETTLED_STATES
        )

    def _idle_minutes(self, now: datetime) -> float | None:
        """Minutes since the last input, from the most recent idle reading.

        ``None`` rather than zero when there is no reading. "No idle sensor on
        this machine" and "the user typed a moment ago" are different facts, and
        a judge told the second when the first is true would treat an empty room
        as an occupied one.
        """
        readings = self.store.observations(
            now - IDLE_LOOKBACK, now, sensor="idle"
        )
        for obs in reversed(readings):
            if obs.kind != "ms":
                continue
            try:
                return round(float(obs.value) / 60000.0, 1)
            except (TypeError, ValueError):
                # A sensor emitting nonsense is a broken sensor, not evidence.
                return None
        return None

    def _minutes_today(
        self, day_start: datetime, now: datetime
    ) -> tuple[float, float]:
        """Aligned minutes and claimed minutes so far today (spec section 3.1).

        Claimed counts only the part of each block that has already come due, so
        the ratio is not dragged down all morning by an evening block nobody has
        had the chance to keep yet.
        """
        aligned = 0.0
        for interval in self.store.intervals(day_start, now):
            if interval.klass is not Klass.ALIGNED:
                continue
            aligned += _overlap_minutes(
                interval.start, interval.end, day_start, now
            )

        claimed = 0.0
        for block in self.contract.blocks():
            if block.state is BlockState.MOVED:
                continue
            claimed += _overlap_minutes(
                block.planned_start, block.planned_end, day_start, now
            )
        return aligned, claimed


# -- module-level helpers ----------------------------------------------------


def _minutes_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60.0


def _overlap_minutes(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> float:
    first = max(start, window_start)
    last = min(end, window_end)
    if last <= first:
        return 0.0
    return (last - first).total_seconds() / 60.0


def _due_entry(
    ladder: list[dict[str, Any]], elapsed_minutes: float
) -> dict[str, Any] | None:
    """The highest rung whose trigger time has passed, or ``None`` if none has."""
    due: dict[str, Any] | None = None
    for entry in ladder:
        if entry["after_minutes"] <= elapsed_minutes:
            if due is None or entry["rung"] >= due["rung"]:
                due = entry
    return due


def _entry_at_or_below(
    ladder: list[dict[str, Any]], rung: int
) -> dict[str, Any] | None:
    """The ladder entry closest to ``rung`` without exceeding it.

    A judge naming a rung the ladder does not define resolves downward, which
    keeps the mercy-only direction intact even when the config is sparse.
    """
    candidates = [entry for entry in ladder if entry["rung"] <= rung]
    if not candidates:
        return None
    return max(candidates, key=lambda entry: entry["rung"])


def _parse_verdict(raw: Any) -> tuple[int | None, str]:
    """Read a judge's answer as (rung, justification), tolerating its shape.

    A verdict that cannot be read returns ``None``, never a guessed number. The
    same rule Tier 2 follows: a model that answered nonsense has told you
    nothing, and acting on nothing is worse than acting on the ladder.
    """
    justification = ""
    if isinstance(raw, Mapping):
        for key in ("justification", "reason", "why"):
            text = _text_of(raw.get(key))
            if text:
                justification = text
                break
        raw = raw.get("rung")

    if isinstance(raw, bool):
        # True is not rung 1. A judge answering a boolean has misunderstood the
        # question, and int(True) would silently turn that into an escalation.
        return None, justification
    if isinstance(raw, (int, float)):
        return int(raw), justification
    if isinstance(raw, str):
        try:
            return int(raw.strip()), justification
        except ValueError:
            return None, justification
    return None, justification


def _guard_derived(payload: dict[str, Any]) -> None:
    """Refuse to hand the judge anything that is not a number.

    This is the privacy boundary of spec section 7 written as a control rather
    than an intention. A future field carrying a window title, a commitment
    label or a URL fails here, in the process, before the call is made.
    """
    unexpected = set(payload) - DERIVED_FIELDS
    if unexpected:
        raise ValueError(
            f"derived state carries fields that are not on the allowlist: "
            f"{sorted(unexpected)}"
        )
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"derived state field {key!r} is {type(value).__name__}; the "
                "judge receives counts and durations only"
            )


def _text_of(value: Any) -> str:
    """A trimmed string, or empty for anything that is not usable text."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _first_open_task(commitment: Mapping[str, Any]) -> str | None:
    """The first thing on the commitment that is still to be done.

    Accepts a queue under ``next_actions`` and a single ``next_action``, and
    accepts queue items as bare strings or as mappings carrying a ``done`` flag,
    because the wizard writes one shape and a hand-edited config writes the
    other.
    """
    queue = commitment.get("next_actions")
    if isinstance(queue, (list, tuple)):
        for item in queue:
            text = _open_task_text(item)
            if text:
                return text
    elif isinstance(queue, str):
        text = queue.strip()
        if text:
            return text

    return _text_of(commitment.get("next_action")) or None


def _open_task_text(item: Any) -> str | None:
    if isinstance(item, str):
        return item.strip() or None
    if isinstance(item, Mapping):
        if item.get("done") or item.get("completed"):
            return None
        for key in TASK_TEXT_KEYS:
            text = _text_of(item.get(key))
            if text:
                return text
    return None
