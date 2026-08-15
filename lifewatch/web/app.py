"""The API, and the two views over it.

Two views, not one responsive layout, because they are different products with
different jobs (spec section 13).

The **wall** is read from across a room, in about a second, by someone who did
not choose to look at it. It shows three numbers and the grid, it never accepts
input, and it repaints on its own so unclaimed time accumulates in red whether or
not anyone is operating the instrument. That autonomy is the whole reason a
passive dashboard fails and this does not.

The **phone** is the control surface: start, stop, move, pass, sick, answer a
classification question.

There is deliberately no dismiss route, and a test asserts its absence. The only
exits are start, complete, move, pass and sick (spec section 3.3).
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lifewatch.classify.tier3 import AskQueue
from lifewatch.clock import Clock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.models import BlockState, Klass
from lifewatch.store import Store
from lifewatch.watcher import Watcher

STATIC = Path(__file__).parent / "static"

# Hours outside this range are not counted as lost. Sleep is not a failure to
# study, and a grid that bled red overnight would teach the reader to ignore red.
DEFAULT_WAKING_START = 6
DEFAULT_WAKING_END = 23


class MoveRequest(BaseModel):
    new_start: datetime
    new_end: datetime


class AnswerRequest(BaseModel):
    klass: str


class SickRequest(BaseModel):
    hours: float = 24.0


class PassRequest(BaseModel):
    block_id: str | None = None


def create_app(
    contract: Contract,
    store: Store,
    watcher: Watcher,
    ask_queue: AskQueue,
    config: Config,
    clock: Clock,
) -> FastAPI:
    """Build the app around already-constructed units.

    Everything is injected rather than constructed here so tests drive the real
    routes against a FakeClock and a temporary store.
    """
    app = FastAPI(title="lifewatch", docs_url=None, redoc_url=None)

    # -- reads ------------------------------------------------------------

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        now = clock.now()
        block = contract.current_block(now)
        intervention = watcher.evaluate(now)
        banked, gone, accounted = _tally_today(store, now, config)
        claimed = _claimed_minutes_today(contract, now)
        return {
            "now": now.isoformat(),
            "current_block": _block_json(block),
            "rung": intervention.rung if intervention else 0,
            "message": intervention.message if intervention else None,
            "next_action": intervention.next_action if intervention else None,
            "banked_minutes": banked,
            "gone_minutes": gone,
            "accounted_minutes": accounted,
            "claimed_minutes": claimed,
            # Spec section 3.1. None rather than zero when nothing was claimed:
            # a ratio with no denominator is undefined, not perfect.
            "integrity": round(banked / claimed, 3) if claimed else None,
            "passes_remaining": contract.passes_remaining(now),
            "silenced": contract.is_silenced(now),
            "questions_pending": len(ask_queue.pending()),
            "consequence_chain": config.consequence_chain,
        }

    @app.get("/api/grid")
    def grid(start: datetime, end: datetime) -> list[dict[str, Any]]:
        """One entry per waking hour in the range, oldest first.

        Hours that have passed with nothing recorded come back as ``gone``. That
        is the point: the grid fills red on its own, with no help from the user
        and no way to avoid it except by doing the work.
        """
        now = clock.now()
        intervals = store.intervals(start - timedelta(days=1), end)
        waking_start = int(config.classifier.get("waking_start", DEFAULT_WAKING_START))
        waking_end = int(config.classifier.get("waking_end", DEFAULT_WAKING_END))

        cells: list[dict[str, Any]] = []
        hour = start.replace(minute=0, second=0, microsecond=0)
        while hour < end:
            if waking_start <= hour.hour < waking_end:
                cells.append(
                    {
                        "hour": hour.isoformat(),
                        "klass": _klass_for_hour(hour, intervals, now),
                    }
                )
            hour += timedelta(hours=1)
        return cells

    @app.get("/api/blocks")
    def blocks() -> list[dict[str, Any]]:
        return [_block_json(b) for b in contract.blocks()]

    @app.get("/api/questions")
    def questions() -> list[dict[str, Any]]:
        return [
            {"id": q.id, "title": q.title, "block_id": q.block_id,
             "asked_at": q.asked_at.isoformat()}
            for q in ask_queue.pending()
        ]

    # -- writes -----------------------------------------------------------

    @app.post("/api/block/{block_id}/start")
    def start_block(block_id: str) -> dict[str, Any]:
        return _block_json(_guard(lambda: contract.start_block(block_id, clock.now())))

    @app.post("/api/block/{block_id}/complete")
    def complete_block(block_id: str) -> dict[str, Any]:
        return _block_json(_guard(lambda: contract.complete_block(block_id, clock.now())))

    @app.post("/api/block/{block_id}/move")
    def move_block(block_id: str, body: MoveRequest) -> dict[str, Any]:
        """Relocate a block. The only routine way out of one.

        A destination is required by the request model, so FastAPI rejects a
        bodyless call with 422 before this runs. That is deliberate: 'move' with
        nowhere to move to would be 'dismiss' wearing a different name.
        """
        return _block_json(
            _guard(
                lambda: contract.move_block(
                    block_id, body.new_start, body.new_end, clock.now()
                )
            )
        )

    @app.post("/api/pass")
    def use_pass(body: PassRequest | None = None) -> dict[str, Any]:
        now = clock.now()
        block_id = body.block_id if body else None
        if block_id is None:
            current = contract.current_block(now)
            block_id = current.id if current else None
        spent = contract.use_pass(now, block_id=block_id)
        return {"spent": spent, "passes_remaining": contract.passes_remaining(now)}

    @app.post("/api/sick")
    def declare_sick(body: SickRequest | None = None) -> dict[str, Any]:
        hours = body.hours if body else 24.0
        until = contract.declare_sick(clock.now(), hours=hours)
        return {"silenced_until": until.isoformat()}

    @app.post("/api/questions/{question_id}")
    def answer_question(question_id: int, body: AnswerRequest) -> dict[str, Any]:
        try:
            klass = Klass(body.klass)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown class {body.klass!r}")
        ask_queue.answer(question_id, klass)
        return {"id": question_id, "klass": klass.value}

    # -- views ------------------------------------------------------------

    @app.get("/")
    def phone() -> FileResponse:
        return FileResponse(STATIC / "phone.html")

    @app.get("/wall")
    def wall() -> FileResponse:
        return FileResponse(STATIC / "wall.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


# -- helpers -----------------------------------------------------------------


def _guard(action):
    try:
        return action()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _block_json(block) -> dict[str, Any] | None:
    if block is None:
        return None
    body = asdict(block) if is_dataclass(block) else dict(block)
    for key, value in list(body.items()):
        if isinstance(value, datetime):
            body[key] = value.isoformat()
        elif isinstance(value, BlockState):
            body[key] = value.value
    return body


def _klass_for_hour(hour: datetime, intervals, now: datetime) -> str:
    """What an hour was, by whichever class held it longest.

    An hour still in the future is ``pending``. An hour that has passed with
    nothing recorded is ``gone``, never ``unknown``: unrecorded time is exactly
    the time this instrument exists to make visible, and softening its name
    would soften the only signal that works without the user's cooperation.
    """
    end = hour + timedelta(hours=1)
    if hour > now:
        return "pending"

    held: dict[str, float] = {}
    for interval in intervals:
        overlap = (min(interval.end, end) - max(interval.start, hour)).total_seconds()
        if overlap > 0:
            held[interval.klass.value] = held.get(interval.klass.value, 0.0) + overlap

    if not held:
        return "gone"
    winner = max(held.items(), key=lambda kv: kv[1])
    # A sliver of evidence does not carry a whole hour.
    return winner[0] if winner[1] >= 300 else "gone"


def _tally_today(store: Store, now: datetime, config: Config) -> tuple[int, int, int]:
    """Minutes banked, lost and accounted for since midnight."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    waking_start = int(config.classifier.get("waking_start", DEFAULT_WAKING_START))

    banked = accounted = 0.0
    for interval in store.intervals(midnight, now):
        minutes = (interval.end - interval.start).total_seconds() / 60
        if interval.klass in (Klass.ALIGNED, Klass.AMBIENT):
            banked += minutes
        elif interval.klass is Klass.ACCOUNTED:
            accounted += minutes

    waking_began = max(midnight.replace(hour=waking_start), midnight)
    elapsed = max(0.0, (now - waking_began).total_seconds() / 60)
    gone = max(0.0, elapsed - banked - accounted)
    return int(banked), int(gone), int(accounted)


def _claimed_minutes_today(contract: Contract, now: datetime) -> int:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for block in contract.blocks():
        if block.state is BlockState.MOVED:
            continue
        if midnight <= block.planned_start < midnight + timedelta(days=1):
            total += block.planned_minutes
    return total
