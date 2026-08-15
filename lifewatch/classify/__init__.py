"""Turning observations into a verdict about an hour.

Three tiers, cheapest first. Tier 1 is mechanical, Tier 2 asks a local model to
judge an ambiguous window title, Tier 3 asks the person. Each tier that cannot
decide returns nothing and the next one runs; the tier that did decide is
recorded on every interval, so any verdict can be traced back to the rule, the
judgment, or the answer that reached it - and corrected at the right level.

``Classifier`` is the dispatch between them, and it owns one thing neither tier
can: deciding what still counts as evidence.

**Evidence has an age.** The window sensor emits nothing while no window holds
focus, and the runner writes only on change plus a heartbeat, so the newest
focus observation in the store can be hours old and still be the newest. Read
naively, this morning's title would keep classifying this afternoon, and a
laptop asleep since yesterday would keep reporting whatever it last saw. So the
classifier compares every observation against ``now`` and drops anything older
than the staleness bound before any tier sees it. Stale evidence is not weaker
evidence about the present; it is evidence about a different time.

The bound has to sit above the sensor runner's heartbeat, because an unchanged
value is only re-stamped once per heartbeat and a tighter bound would discard
readings that are merely steady. The default allows one missed heartbeat on top
of that. Dropping everything leaves the moment undecided rather than accused:
the watcher escalates from the block's state, not from a classification, so a
silent sensor loop costs the record its detail and costs the user nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from lifewatch.classify.tier1 import tier1
from lifewatch.classify.tier2 import Judge, local_judge, tier2
from lifewatch.classify.tier3 import TIER as TIER_ASK
from lifewatch.classify.tier3 import AskQueue
from lifewatch.config import Config
from lifewatch.models import Interval, Klass, Observation

# No tier reached a verdict and there was nothing to put to the user either.
# A real tier number would claim a judgment that never happened.
TIER_UNDECIDED = 0

# Twice the sensor runner's default 600 s heartbeat. Anything older means the
# sensor stopped reporting, not that its value held.
DEFAULT_STALENESS_S = 1200

KIND_FOCUS = "focus"
FOCUS_SEPARATOR = "|"


class Classifier:
    """Runs the tiers in order and returns exactly one verdict.

    ``judge`` defaults to the local model named in ``config.classifier``. On a
    machine with no model installed that judge raises on first use, Tier 2
    returns nothing, and the moment goes to the person - which is the documented
    fallback in spec section 17.1 and the reason this project works on a clean
    clone with no model, no account and no key.
    """

    def __init__(
        self,
        config: Config,
        ask_queue: AskQueue,
        judge: Judge | None = None,
        staleness_s: int | None = None,
    ) -> None:
        self.config = config
        self.ask_queue = ask_queue
        # End of the last interval this classifier recorded. Intervals tile, so
        # a new one may never start before it. Recovered from the store on first
        # use so a restart cannot reopen a span that was already closed.
        self._last_end: datetime | None = None
        self.judge = judge if judge is not None else local_judge(config)
        settings = config.classifier or {}
        self.staleness_s = int(
            staleness_s
            if staleness_s is not None
            else settings.get("staleness_s", DEFAULT_STALENESS_S)
        )

    def classify(
        self,
        observations: Sequence[Observation],
        block: Any,
        now: datetime,
        since: datetime | None = None,
    ) -> Interval:
        """Decide what the moment ``now`` was, using evidence that is still current.

        Always returns an ``Interval``. Where the tiers cannot decide, that
        interval is UNKNOWN and says so, because a classifier that returned
        nothing would leave the caller to invent a class of its own.

        Intervals TILE time; they must never overlap. The tiers anchor a verdict
        at the observation that justifies it, which is the right answer to "when
        did this become true" and the wrong answer to "what span does this
        record cover". Because the runner suppresses unchanged values, the same
        observation is the evidence on every tick until the next heartbeat, so
        anchoring the record there re-counts the same minutes once per tick:
        measured at 55 minutes recorded for 10 minutes elapsed. The integrity
        ratio in spec section 3.1 divides by claimed minutes, so an inflated
        numerator does not read as a bug, it reads as a good day.

        ``since`` is the end of the previously recorded interval. The caller may
        pass it; otherwise it is remembered across calls and recovered from the
        store on the first call, so a restart cannot reopen a closed span.
        """
        floor = self._tile_floor(since, now)

        current = self._current(observations, now)

        verdict = tier1(
            current,
            block,
            now,
            accounted_places=self.config.accounted_places,
            idle_threshold_s=self.config.idle_threshold_s,
        )
        if verdict is not None:
            return self._tiled(verdict, floor, now)

        if block is None:
            # Unclaimed time is already the loudest thing on the grid (spec
            # section 13.3). Asking about it would train the user to dismiss
            # questions, and there is no commitment to judge a title against.
            return self._tiled(self._undecided(
                now, "nothing was declared for this time, so there is nothing "
                "to judge a window against"
            ), floor, now)

        focused = self._focused_title(current)
        if focused is None:
            return self._tiled(self._undecided(
                now,
                f"no window title observed in the last {self.staleness_s} s",
            ), floor, now)

        label = self._commitment_label(block)
        if not label:
            return self._tiled(self._undecided(
                now, "the current block names no commitment to judge against"
            ), floor, now)

        title, seen_at = focused
        start = self._no_earlier_than_the_block(seen_at, block, now)

        verdict = tier2(title, label, self.judge, start=start, end=now)
        if verdict is not None:
            return self._tiled(verdict, floor, now)

        # Nothing mechanical applied and the model would not commit, so the only
        # honest source left is the person. One question per title while it is
        # pending; the ask queue enforces that, not this loop.
        self.ask_queue.enqueue(title, block_id=getattr(block, "id", None), now=now)
        return self._tiled(
            Interval(
                start=start,
                end=now,
                klass=Klass.UNKNOWN,
                tier=TIER_ASK,
                reason=(
                    "no rule and no model could settle the focused window; "
                    "asked the user"
                ),
            ),
            floor,
            now,
        )

    # -- evidence ---------------------------------------------------------

    def _current(
        self, observations: Sequence[Observation], now: datetime
    ) -> list[Observation]:
        """Observations that are evidence about ``now``.

        Both ends matter. Anything after ``now`` is the future leaking into a
        replay, and anything older than the bound is a fact about a time that
        has passed.
        """
        horizon = now - timedelta(seconds=self.staleness_s)
        return [obs for obs in observations if horizon <= obs.ts <= now]

    @staticmethod
    def _focused_title(
        observations: Sequence[Observation],
    ) -> tuple[str, datetime] | None:
        """The most recent focused title, and when it was seen.

        A focus observation is ``"<wm_class>|<title>"``. An empty title half is
        not a question anyone can answer, so it is treated as no title at all.
        """
        chosen: Observation | None = None
        for obs in observations:
            if obs.kind != KIND_FOCUS:
                continue
            if chosen is None or obs.ts >= chosen.ts:
                chosen = obs
        if chosen is None or FOCUS_SEPARATOR not in chosen.value:
            return None
        title = chosen.value.split(FOCUS_SEPARATOR, 1)[1].strip()
        return (title, chosen.ts) if title else None

    # -- the contract side ------------------------------------------------

    def _commitment_label(self, block: Any) -> str:
        """What to call the commitment when asking about it.

        The label if the config has one, the id otherwise. The id is already a
        user-chosen string, so falling back to it neither invents a name nor
        sends the model anything the config did not put there.
        """
        commitment_id = getattr(block, "commitment_id", None)
        if not commitment_id:
            return ""
        for commitment in self.config.commitments:
            if not isinstance(commitment, dict):
                continue
            if str(commitment.get("id")) == str(commitment_id):
                return str(commitment.get("label") or commitment_id).strip()
        return str(commitment_id)

    @staticmethod
    def _no_earlier_than_the_block(
        start: datetime, block: Any, now: datetime
    ) -> datetime:
        """Keep a verdict inside the block it is judging.

        The same rule Tier 1 applies to its own spans: an observation can
        predate the block that is being judged, and charging that time to the
        block would report minutes the user never promised.
        """
        planned_start = getattr(block, "planned_start", None)
        if isinstance(planned_start, datetime) and start < planned_start:
            start = planned_start
        return min(start, now)

    @staticmethod
    def _undecided(now: datetime, reason: str) -> Interval:
        """An UNKNOWN verdict that claims no minutes.

        Zero length on purpose. Time nobody classified must not appear in the
        ledger as time somebody did.
        """
        return Interval(
            start=now,
            end=now,
            klass=Klass.UNKNOWN,
            tier=TIER_UNDECIDED,
            reason=reason,
        )


    # -- tiling -------------------------------------------------------------

    def _tile_floor(self, since: datetime | None, now: datetime) -> datetime | None:
        """The earliest instant a new interval is allowed to start."""
        if since is not None:
            self._last_end = max(self._last_end or since, since)
        elif self._last_end is None:
            self._last_end = self._recover_last_end()
        floor = self._last_end
        if floor is not None and floor > now:
            # The clock went backwards, or a caller replayed an older moment.
            # Trust the record rather than reopening a closed span.
            return now
        return floor

    def _recover_last_end(self) -> datetime | None:
        """Where the stored record leaves off, if the store can say."""
        store = getattr(self.ask_queue, "store", None)
        conn = getattr(store, "conn", None)
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT MAX(end) FROM intervals").fetchone()
        except Exception:
            return None
        if not row or not row[0]:
            return None
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None

    def _tiled(self, interval: Interval, floor: datetime | None, now: datetime) -> Interval:
        """Clamp an interval so it starts no earlier than the last one ended.

        The tier's own start is kept when it is later than the floor, because a
        verdict that only became true part-way through the gap should not claim
        the whole gap.
        """
        start = interval.start
        if floor is not None and start < floor:
            start = floor
        if start > interval.end:
            start = interval.end
        self._last_end = max(self._last_end or interval.end, interval.end, now)
        if start == interval.start:
            return interval
        return replace(interval, start=start)

__all__ = [
    "Classifier",
    "AskQueue",
    "DEFAULT_STALENESS_S",
    "TIER_ASK",
    "TIER_UNDECIDED",
    "tier1",
    "tier2",
    "local_judge",
]
