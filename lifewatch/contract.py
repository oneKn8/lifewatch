"""What was promised.

The contract is the half of the discrepancy that the person supplies: declared
blocks, a finite weekly allowance of passes, a sick-mode window, and the ledger
of hours that were moved rather than kept. Sensors supply the other half. The
gap between the two is the product (design spec section 3.1).

The shape of this module is an argument, so it is worth stating plainly.

**There is no dismiss.** A block can be started, completed, or moved, and moving
it requires naming a slot that has not already passed. There is no method here
that makes an obligation disappear, and there must never be one. That single
absence is what separates an accountability engine from a notification that gets
swiped away, and it is asserted by test rather than left to discipline.

**Renegotiation is cheap and honest; silence is not.** Moving costs nothing
except a named replacement slot, and the hours reappear as debt on the day they
landed on. So the system is only ever harsh toward silence, which is what makes
"go hard on me" safe to implement (spec section 3.3).

Nothing here reads the wall clock. Every method that needs the time is given it,
which is what lets a simulated term replay in milliseconds.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

from lifewatch.clock import Clock
from lifewatch.config import Config
from lifewatch.models import Block, BlockState


# A block in one of these has already reached an outcome; a pass cannot change
# what already happened to it.
_SETTLED_BLOCK_STATES = frozenset(
    {BlockState.COMPLETED, BlockState.MOVED, BlockState.EXCUSED}
)


class Contract:
    """The declared commitments and the exceptions that may be taken against them.

    Blocks live in memory for Stage 1, keyed by a generated id. The id comes from
    a counter rather than a uuid or a timestamp because a replayed week must
    produce identical ids every run; an escalation sequence that is asserted
    exactly cannot rest on a random source.
    """

    def __init__(self, config: Config, clock: Clock) -> None:
        self.config = config
        # Exposed deliberately: callers ask the contract what time it thinks it
        # is rather than carrying a second clock that could disagree with it.
        self.clock = clock

        self._blocks: dict[str, Block] = {}
        self._next_id = 1
        self._passes_used: dict[tuple[int, int], list[datetime]] = {}
        self._debt: dict[date, int] = {}
        # successor block id -> (day it is owed on, minutes owed). Kept so that
        # moving an already-moved block relocates the same debt instead of
        # minting a second copy of it.
        self._carried: dict[str, tuple[date, int]] = {}
        self._silenced_until: datetime | None = None
        # The web layer serves synchronous handlers from a thread pool and the
        # sensor loop runs alongside it, so the check-then-decrement inside
        # use_pass is genuinely racy. A finite allowance has to actually be
        # finite under concurrency or it is not a constraint at all.
        self._lock = threading.RLock()

    # -- blocks -----------------------------------------------------------

    def add_block(
        self, commitment_id: str, planned_start: datetime, planned_end: datetime
    ) -> Block:
        """Declare a block. It starts life PLANNED and owes nothing yet."""
        self._require_span(planned_start, planned_end)
        with self._lock:
            block = Block(
                id=self._mint_id(),
                commitment_id=commitment_id,
                planned_start=planned_start,
                planned_end=planned_end,
            )
            self._blocks[block.id] = block
            return block

    def blocks(self) -> list[Block]:
        """Every declared block, in the order it was declared."""
        with self._lock:
            return list(self._blocks.values())

    def block(self, block_id: str) -> Block | None:
        with self._lock:
            return self._blocks.get(block_id)

    def current_block(self, now: datetime) -> Block | None:
        """The block whose window covers ``now``, if there is one.

        The window is inclusive of its start and exclusive of its end, so a block
        ending at 08:30 and one beginning at 08:30 never both claim that minute.

        A MOVED block is not returned: its hours were relocated, so it no longer
        holds the slot. Every other state does, including COMPLETED, because a
        view showing the current hour should still show what was promised for it.
        """
        # Read under the lock. The web layer serves synchronous handlers from a
        # thread pool while the sensor loop runs alongside it, so a move_block
        # landing mid-iteration would raise RuntimeError: dictionary changed
        # size during iteration, inside the one call the wall view makes every
        # fifteen seconds.
        with self._lock:
            candidates = [
                block
                for block in self._blocks.values()
                if block.state is not BlockState.MOVED
                and block.planned_start <= now < block.planned_end
            ]
        if not candidates:
            return None
        # When declared windows overlap, the one actually under way is the
        # honest answer to "what is running now". Otherwise the earliest.
        candidates.sort(
            key=lambda b: (b.state is not BlockState.RUNNING, b.planned_start)
        )
        return candidates[0]

    def start_block(self, block_id: str, now: datetime) -> Block:
        """Mark a block as under way, recording when it actually began.

        Starting a block that is already running leaves the original
        ``actual_start`` alone. When the work began is a matter of record and a
        second tap on the button must not be able to revise it.
        """
        with self._lock:
            block = self._require_block(block_id)
            if block.state in (BlockState.MOVED, BlockState.COMPLETED):
                raise ValueError(
                    f"cannot start block {block_id}: it is {block.state.value}"
                )
            if block.actual_start is None:
                block.actual_start = now
            block.state = BlockState.RUNNING
            return block

    def complete_block(self, block_id: str, now: datetime) -> Block:
        """Mark a block as finished.

        A block that was completed without ever being started keeps
        ``actual_start = None``. Backfilling it with the planned start would
        manufacture evidence for an unobserved claim, which is precisely the
        rounding-up in one's own favour that this system exists to expose.
        """
        with self._lock:
            block = self._require_block(block_id)
            if block.state is BlockState.MOVED:
                raise ValueError(
                    f"cannot complete block {block_id}: it was moved to "
                    f"{block.moved_to}"
                )
            block.actual_end = now
            block.state = BlockState.COMPLETED
            return block

    # -- moving, which is the only routine exit ---------------------------

    def move_block(
        self, block_id: str, new_start: datetime, new_end: datetime, now: datetime
    ) -> Block:
        """Relocate a block's hours to a named slot and return the successor.

        The original becomes MOVED, a fresh PLANNED successor is created, and the
        two are linked in both directions so the chain stays auditable. The moved
        minutes are recorded as debt on the successor's date.

        Two rules keep this from becoming a dismiss button wearing a costume:

        The replacement slot must not have already ended. Moving a block into
        yesterday satisfies the letter of "name where the hours land" while
        landing them nowhere.

        Moving a successor carries the *original* debt forward rather than
        recomputing it from the new span. Otherwise a 90-minute obligation could
        be whittled down to nothing by moving it repeatedly into shorter slots,
        and the ledger would leak an hour at a time.
        """
        self._require_span(new_start, new_end)
        if new_end <= now:
            raise ValueError(
                "cannot move a block into a slot that has already passed; "
                "name one that has not"
            )
        with self._lock:
            original = self._require_block(block_id)
            if original.state is BlockState.MOVED:
                raise ValueError(
                    f"block {block_id} was already moved to {original.moved_to}; "
                    "move its successor instead"
                )
            if original.state is BlockState.COMPLETED:
                raise ValueError(
                    f"block {block_id} is completed and has nothing left to move"
                )

            successor = Block(
                id=self._mint_id(),
                commitment_id=original.commitment_id,
                planned_start=new_start,
                planned_end=new_end,
                state=BlockState.PLANNED,
                moved_from=original.id,
            )
            self._blocks[successor.id] = successor
            original.state = BlockState.MOVED
            original.moved_to = successor.id

            owed = self._release_debt(original.id)
            if owed is None:
                owed = original.planned_minutes
            landing = new_start.date()
            self._debt[landing] = self._debt.get(landing, 0) + owed
            self._carried[successor.id] = (landing, owed)
            return successor

    def debt_minutes(self, day: date) -> int:
        """Minutes that landed on ``day`` because they were moved off another one."""
        # A datetime is a date, but not one that would ever match a key here.
        # Answering zero for it would be a quiet lie, so convert instead.
        if isinstance(day, datetime):
            day = day.date()
        return self._debt.get(day, 0)

    # -- passes -----------------------------------------------------------

    def passes_remaining(self, now: datetime) -> int:
        """Passes left in the ISO week containing ``now``.

        Keyed on the ISO week rather than counted down from a running total, so
        an unused pass expires with its week. Unlimited passes are no system;
        passes that bank up become unlimited a month into a term.
        """
        used = len(self._passes_used.get(self._week_key(now), ()))
        return max(0, self.config.passes_per_week - used)

    def use_pass(self, now: datetime, block_id: str | None = None) -> bool:
        """Spend one pass, optionally excusing a named block.

        Returns False when the week's allowance is gone. Refusal is a return
        value rather than an exception because running out of passes is an
        ordinary Tuesday, not an error.

        The allowance is counted per ISO week, which is right: it refills weekly.
        But whether a *block* was excused must be recorded on the block, not
        inferred from the week. A block can straddle the week boundary, and
        inferring left a real hole: a pass spent at 23:30 on Sunday was invisible
        at 00:15 on Monday, so the watcher escalated on a block the user had
        already, legitimately, bought out of. That is precisely the wrongful
        interruption that gets an instrument like this uninstalled.
        """
        with self._lock:
            key = self._week_key(now)
            spent = self._passes_used.setdefault(key, [])
            if len(spent) >= self.config.passes_per_week:
                return False
            spent.append(now)
            block = self._blocks.get(block_id) if block_id else None
            if block is not None and block.state not in _SETTLED_BLOCK_STATES:
                block.state = BlockState.EXCUSED
            return True

    def passes_used_at(self, now: datetime) -> list[datetime]:
        """When this week's passes were spent, oldest first."""
        return list(self._passes_used.get(self._week_key(now), ()))

    # -- sick mode --------------------------------------------------------

    def declare_sick(self, now: datetime, hours: float = 24) -> datetime:
        """Silence all escalation for a window, and return when it ends.

        A later declaration extends the window and can never shorten it. The two
        failure modes are not symmetric: silencing someone who has recovered
        costs a day of accountability, while un-silencing someone who is still
        ill harasses them. The merciful failure is the correct default, and the
        user will get sick, and a system that punishes influenza gets uninstalled.
        """
        if hours <= 0:
            raise ValueError("a sick window must have a positive length")
        with self._lock:
            expiry = now + timedelta(hours=hours)
            if self._silenced_until is None or expiry > self._silenced_until:
                self._silenced_until = expiry
            return self._silenced_until

    def is_silenced(self, now: datetime) -> bool:
        return self._silenced_until is not None and now < self._silenced_until

    # -- internals --------------------------------------------------------

    def _mint_id(self) -> str:
        block_id = f"blk-{self._next_id}"
        self._next_id += 1
        return block_id

    def _require_block(self, block_id: str) -> Block:
        try:
            return self._blocks[block_id]
        except KeyError:
            raise KeyError(f"no such block: {block_id}") from None

    @staticmethod
    def _require_span(start: datetime, end: datetime) -> None:
        if end <= start:
            raise ValueError("a block must end after it starts")

    @staticmethod
    def _week_key(now: datetime) -> tuple[int, int]:
        iso = now.isocalendar()
        return (iso[0], iso[1])

    def _release_debt(self, block_id: str) -> int | None:
        """Take a block's debt off the day it was owed on. Returns the amount."""
        previous = self._carried.pop(block_id, None)
        if previous is None:
            return None
        owed_day, owed = previous
        remaining = self._debt.get(owed_day, 0) - owed
        if remaining > 0:
            self._debt[owed_day] = remaining
        else:
            self._debt.pop(owed_day, None)
        return owed
