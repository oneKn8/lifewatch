"""Tier 3: the questions only the person can settle.

Genuine ambiguity produces one question, answered with one tap. It is the last
tier, so it is also the floor the whole classifier stands on: with no model
installed - which is the state of the target machine, spec section 5 - every
ambiguous moment arrives here, and this is what makes the system work anyway.

Two properties matter more than anything else in this module.

**One ambiguity is one question.** The sensor loop revisits the same moment every
fifteen seconds, so an ask queue that enqueued per poll would produce hundreds of
identical cards and teach the user to ignore all of them. Enqueue is idempotent
while a question with the same title is pending, and that is enforced by a unique
index rather than by the check above it, because the web layer and the sensor
loop run in different threads and a check-then-insert between them is a race.

**An answer is a record.** Answering the same way twice is harmless, because a
phone gets double-tapped. Answering a second, different way is refused, because
that is not an answer arriving, it is an answer being rewritten.

Learning is stage 2 (spec section 7): an answer classifies its own interval and
nothing else, so the same title asked again tomorrow is a new question. That is
the honest behaviour until a ruleset exists, and the ruleset is user data rather
than code when it does.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lifewatch.models import Klass
from lifewatch.store import Store

TIER = 3

# The invariant "one pending question per title" expressed where it cannot be
# skipped. Partial index, so answered rows do not collide and the same title can
# legitimately be asked again once the previous answer is in.
PENDING_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_questions_pending_title
ON questions(title) WHERE answered_klass IS NULL
"""


@dataclass(frozen=True)
class Question:
    """One thing the classifier could not settle, waiting for a person.

    The web layer renders and answers these, so the shape is a contract:

    ``id``              stable integer, the address for ``answer``
    ``title``           the window title that could not be classified
    ``block_id``        the block it was observed during, or ``None``
    ``asked_at``        when the question was first raised
    ``answered_klass``  ``None`` while pending, the chosen ``Klass`` after

    Frozen, because a question that has been asked is part of the record.
    """

    id: int
    title: str
    block_id: str | None
    asked_at: datetime
    answered_klass: Klass | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form, for the phone view."""
        return {
            "id": self.id,
            "title": self.title,
            "block_id": self.block_id,
            "asked_at": self.asked_at.isoformat(),
            "answered_klass": (
                self.answered_klass.value if self.answered_klass else None
            ),
        }


class AskQueue:
    """The pending questions, held in the store so they survive a restart.

    A question that vanished when the daemon restarted would be an ambiguity the
    system quietly forgot, and forgetting is the failure mode this project
    exists to treat.
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        self._conn = store.conn
        # The web layer serves synchronous handlers from a thread pool while the
        # classifier runs alongside it. The lock covers this process; the unique
        # index below covers everything the lock cannot see.
        self._lock = threading.RLock()
        self._conn.execute(PENDING_UNIQUE_INDEX)
        self._conn.commit()

    # -- asking -----------------------------------------------------------

    def enqueue(self, title: str, block_id: str | None, now: datetime) -> Question:
        """Raise a question about ``title``, or return the one already pending.

        Returns the existing question unchanged when one is pending for this
        title, including its original ``asked_at``. When the ambiguity started
        is the fact worth keeping; the fact that it is still going is visible
        from the clock.
        """
        clean = str(title or "").strip()
        if not clean:
            raise ValueError("a question needs a title to ask about")

        with self._lock:
            existing = self._pending_by_title(clean)
            if existing is not None:
                return existing
            try:
                cursor = self._conn.execute(
                    "INSERT INTO questions (ts, title, block_id, answered_klass) "
                    "VALUES (?, ?, ?, NULL)",
                    (now.isoformat(), clean, block_id),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Another writer got there first. The index did its job; the
                # caller still wants the question, so read theirs back.
                self._conn.rollback()
                existing = self._pending_by_title(clean)
                if existing is None:
                    raise
                return existing
            return Question(
                id=int(cursor.lastrowid),
                title=clean,
                block_id=block_id,
                asked_at=now,
            )

    # -- reading ----------------------------------------------------------

    def pending(self) -> list[Question]:
        """Unanswered questions, oldest first, which is the order to ask them in."""
        rows = self._conn.execute(
            "SELECT * FROM questions WHERE answered_klass IS NULL "
            "ORDER BY ts ASC, id ASC"
        )
        return [self._to_question(row) for row in rows]

    def question(self, question_id: int) -> Question | None:
        row = self._row(question_id)
        return self._to_question(row) if row is not None else None

    # -- answering --------------------------------------------------------

    def answer(self, question_id: int, klass: Klass | str) -> Question:
        """Record what the person said this title was.

        Accepts a ``Klass`` or the string the phone posted. Refuses ``UNKNOWN``:
        that is the state the question is already in, and accepting it would
        close a question nobody decided.
        """
        resolved = self._require_decision(klass)
        with self._lock:
            row = self._row(question_id)
            if row is None:
                raise KeyError(f"no such question: {question_id}")

            already = row["answered_klass"]
            if already is not None:
                if Klass(already) is resolved:
                    return self._to_question(row)  # a second tap on the phone
                raise ValueError(
                    f"question {question_id} was already answered "
                    f"{already!r}; an answer is a record and is not revised"
                )

            self._conn.execute(
                "UPDATE questions SET answered_klass = ? "
                "WHERE id = ? AND answered_klass IS NULL",
                (resolved.value, question_id),
            )
            self._conn.commit()
            return self._to_question(self._row(question_id))

    # -- internals --------------------------------------------------------

    @staticmethod
    def _require_decision(klass: Klass | str) -> Klass:
        resolved = Klass(klass)
        if resolved is Klass.UNKNOWN:
            raise ValueError(
                "'unknown' is not an answer; the question stays open until it "
                "is decided"
            )
        return resolved

    def _row(self, question_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM questions WHERE id = ?", (question_id,)
        ).fetchone()

    def _pending_by_title(self, title: str) -> Question | None:
        row = self._conn.execute(
            "SELECT * FROM questions WHERE title = ? AND answered_klass IS NULL "
            "ORDER BY ts ASC, id ASC LIMIT 1",
            (title,),
        ).fetchone()
        return self._to_question(row) if row is not None else None

    @staticmethod
    def _to_question(row: sqlite3.Row) -> Question:
        answered = row["answered_klass"]
        return Question(
            id=int(row["id"]),
            title=row["title"],
            block_id=row["block_id"],
            asked_at=datetime.fromisoformat(row["ts"]),
            answered_klass=Klass(answered) if answered else None,
        )
