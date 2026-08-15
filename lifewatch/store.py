"""The record of what happened.

Append-only, deliberately. There is no update path and no delete path, and a
test asserts their absence. An instrument whose entire purpose is honesty about
how time was spent must not offer a way to revise the answer after the fact.

Timestamps are stored as ISO-8601 text so lexicographic ordering is chronological
ordering, which keeps range queries simple and index-friendly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from lifewatch.clock import Clock
from lifewatch.models import Interval, Klass, Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  sensor TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS ix_obs_sensor ON observations(sensor, kind, ts);

CREATE TABLE IF NOT EXISTS intervals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start TEXT NOT NULL,
  end TEXT NOT NULL,
  klass TEXT NOT NULL,
  tier INTEGER NOT NULL,
  reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_iv_start ON intervals(start);

CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  title TEXT NOT NULL,
  block_id TEXT,
  answered_klass TEXT
);

CREATE TABLE IF NOT EXISTS escalation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  rung INTEGER NOT NULL,
  block_id TEXT NOT NULL,
  message TEXT NOT NULL,
  next_action TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str, clock: Clock) -> None:
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- observations -----------------------------------------------------

    def append(self, obs: Observation) -> None:
        self._conn.execute(
            "INSERT INTO observations (ts, sensor, kind, value, meta) VALUES (?,?,?,?,?)",
            (obs.ts.isoformat(), obs.sensor, obs.kind, obs.value, json.dumps(obs.meta)),
        )
        self._conn.commit()

    def observations(
        self, start: datetime, end: datetime, sensor: str | None = None
    ) -> list[Observation]:
        sql = "SELECT * FROM observations WHERE ts >= ? AND ts <= ?"
        args: list[str] = [start.isoformat(), end.isoformat()]
        if sensor is not None:
            sql += " AND sensor = ?"
            args.append(sensor)
        sql += " ORDER BY ts ASC, id ASC"
        return [self._row_to_observation(r) for r in self._conn.execute(sql, args)]

    def latest(self, sensor: str, kind: str) -> Observation | None:
        row = self._conn.execute(
            "SELECT * FROM observations WHERE sensor = ? AND kind = ? "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (sensor, kind),
        ).fetchone()
        return self._row_to_observation(row) if row else None

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> Observation:
        return Observation(
            ts=datetime.fromisoformat(row["ts"]),
            sensor=row["sensor"],
            kind=row["kind"],
            value=row["value"],
            meta=json.loads(row["meta"]),
        )

    # -- intervals --------------------------------------------------------

    def put_interval(self, iv: Interval) -> None:
        self._conn.execute(
            "INSERT INTO intervals (start, end, klass, tier, reason) VALUES (?,?,?,?,?)",
            (iv.start.isoformat(), iv.end.isoformat(), iv.klass.value, iv.tier, iv.reason),
        )
        self._conn.commit()

    def intervals(self, start: datetime, end: datetime) -> list[Interval]:
        rows = self._conn.execute(
            "SELECT * FROM intervals WHERE start >= ? AND start <= ? ORDER BY start ASC",
            (start.isoformat(), end.isoformat()),
        )
        return [
            Interval(
                start=datetime.fromisoformat(r["start"]),
                end=datetime.fromisoformat(r["end"]),
                klass=Klass(r["klass"]),
                tier=r["tier"],
                reason=r["reason"],
            )
            for r in rows
        ]

    # -- raw access for units that own their own table --------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Escape hatch for units that own a table (ask queue, escalation log).

        They still may not update or delete observations or intervals; that is
        enforced by review and by the absence of any helper here that would.
        """
        return self._conn
