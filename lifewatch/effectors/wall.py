"""The wall display's channel: escalation state written where a screen can read it.

Rung 1 is deliberately the passive rung. A red wall cannot be swiped away, only
not-looked-at, and the whole ladder is built on each rung defeating the previous
one's escape. So this effector does not push anything anywhere. It records the
current escalation state in the store, and the wall view - which repaints on its
own timer whether or not anyone is operating it - picks it up.

Writes are append-only, like everything else in the store. ``current_state``
reads the most recent row rather than a mutable "current" cell, so the sequence
of escalations survives as a record of what the term actually looked like.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from lifewatch.effectors import Delivery
from lifewatch.models import Intervention
from lifewatch.store import Store


class WallEffector:
    name = "wall"

    def __init__(self, store: Store) -> None:
        self.store = store

    def available(self) -> bool:
        """Always true: the wall is a local table, not a service that can be down.

        Whether a physical panel is plugged into the machine is not this unit's
        business. The state is recorded either way, and the phone view renders
        the same rows, so an unplugged TV costs the user nothing.
        """
        return True

    def deliver(self, iv: Intervention) -> Delivery:
        try:
            # The timestamp comes from the store's injected clock, so a replayed
            # week stamps the escalation with the simulated moment it happened.
            now = self.store.clock.now()
            self.store.conn.execute(
                "INSERT INTO escalation (ts, rung, block_id, message, next_action) "
                "VALUES (?,?,?,?,?)",
                (now.isoformat(), iv.rung, iv.block_id, iv.message, iv.next_action),
            )
            self.store.conn.commit()
        except Exception as exc:
            return Delivery(effector=self.name, ok=False, detail=str(exc))
        return Delivery(effector=self.name, ok=True)

    def current_state(self) -> dict[str, Any] | None:
        """The most recent escalation, or ``None`` if nothing has escalated yet.

        ``None`` rather than an empty dict or a rung of zero, because "no
        escalation" is a different thing from "escalation at the lowest level"
        and the wall view renders them differently: one is a calm screen, the
        other is a red one.
        """
        row = self.store.conn.execute(
            "SELECT ts, rung, block_id, message, next_action FROM escalation "
            "ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "ts": datetime.fromisoformat(row["ts"]),
            "rung": row["rung"],
            "block_id": row["block_id"],
            "message": row["message"],
            "next_action": row["next_action"],
        }
