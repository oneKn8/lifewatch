"""Shared fixtures: a whole lifewatch, assembled from fakes.

Every unit in this system is time-dependent, so nothing here reads the wall
clock. A ``FakeClock`` is threaded through the store, the contract and the
watcher, which is what lets a simulated week replay in milliseconds and lets an
escalation sequence be asserted exactly rather than sampled.

The values are obviously synthetic on purpose. ``COURSE-101`` is not a course
anyone takes and ``problem set 2, question 4`` is not anyone's homework. Real
course codes, real network names and real grades live in the gitignored config
and never in the repository.

**The shape of ``built_system``**, which later tasks replay a week through::

    system.clock      FakeClock, starting at T0 (2026-08-24 07:00), advance(seconds)
    system.store      Store on tmp_path, closed automatically at teardown
    system.contract   Contract holding exactly one block: COMMITMENT_ID,
                      T0 .. T0 + BLOCK_MINUTES, left PLANNED (that is, dead)
    system.watcher    Watcher over the above, default two-rung ladder, no judge
    system.config     the Config the contract and watcher share
    system.block      the one Block, already added
    system.t0         T0, so a test need not import it
    system.commitment_id / system.block_minutes

Attributes are added to that namespace rather than removed, so a test written
against it today keeps working when a later task needs one more handle.

``make_system`` is the same builder with the knobs exposed: a different ladder,
a different commitment, a different pass allowance. Use it when a test needs a
system that differs from the default one, rather than mutating ``built_system``
in place, because a mutated fixture hides what the test is actually about.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from lifewatch.clock import FakeClock
from lifewatch.config import DEFAULT_LADDER, Config
from lifewatch.contract import Contract
from lifewatch.store import Store
from lifewatch.watcher import Watcher

# The first morning of the term the system was built for. Fixed, because a
# suite whose expectations move with the calendar proves nothing twice.
T0 = datetime(2026, 8, 24, 7, 0, 0)

BLOCK_MINUTES = 90
COMMITMENT_ID = "COURSE-101"

# A commitment that can answer "what, specifically, do I do now". The watcher
# refuses to escalate without one, so this is what makes the default system a
# system that escalates at all.
DEFAULT_NEXT_ACTIONS = ["problem set 2, question 4", "read chapter 3"]


def _commitment(next_actions: list[Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": COMMITMENT_ID,
        "label": COMMITMENT_ID,
        "weekly_target_minutes": 600,
    }
    if next_actions is not None:
        body["next_actions"] = list(next_actions)
    return body


def _build(
    db_path,
    commitments: list[dict[str, Any]] | None = None,
    ladder: list[dict[str, Any]] | None = None,
    passes: int = 1,
    block_minutes: int = BLOCK_MINUTES,
    start: datetime = T0,
) -> SimpleNamespace:
    config = Config.empty()
    config.passes_per_week = passes
    # Copied, not aliased: a test that edits the ladder must not edit the
    # module-level default and quietly change every later test in the run.
    config.ladder = [dict(rung) for rung in (ladder if ladder is not None else DEFAULT_LADDER)]
    config.commitments = (
        commitments if commitments is not None else [_commitment(DEFAULT_NEXT_ACTIONS)]
    )

    clock = FakeClock(start)
    store = Store(db_path, clock)
    contract = Contract(config, clock)
    block = contract.add_block(
        COMMITMENT_ID, start, start + timedelta(minutes=block_minutes)
    )
    watcher = Watcher(contract, store, config, clock)

    return SimpleNamespace(
        clock=clock,
        store=store,
        contract=contract,
        watcher=watcher,
        config=config,
        block=block,
        t0=start,
        block_minutes=block_minutes,
        commitment_id=COMMITMENT_ID,
    )


@pytest.fixture
def make_system(tmp_path):
    """Factory for assembled systems. Every store it opens is closed at teardown.

    Each system gets its own database file so a test may build two and have them
    disagree, which is how the "no next action" case is tested next to the
    ordinary one.
    """
    built: list[SimpleNamespace] = []

    def factory(**kwargs) -> SimpleNamespace:
        db_path = tmp_path / f"system-{len(built)}.db"
        system = _build(db_path, **kwargs)
        built.append(system)
        return system

    yield factory

    for system in built:
        system.store.close()


@pytest.fixture
def built_system(make_system) -> SimpleNamespace:
    """One 90-minute block at T0, never started. See the module docstring."""
    return make_system()


@pytest.fixture
def watcher_with_dead_block(built_system) -> Watcher:
    """A watcher over a block that was declared and then left alone.

    This is the case the whole project exists for: the hour arrived, the person
    did not, and nothing in the environment has noticed yet.
    """
    return built_system.watcher


@pytest.fixture
def watcher_with_running_block(built_system) -> Watcher:
    """A watcher over a block that is actually under way. Must stay silent."""
    built_system.contract.start_block(built_system.block.id, T0)
    return built_system.watcher


@pytest.fixture
def watcher_without_next_action(make_system) -> Watcher:
    """A dead block whose commitment cannot say what to do about it.

    The commitment is real and the block is as dead as the one above; the only
    difference is that nothing here resolves to a concrete thing to do now. Per
    spec section 9.2 that difference alone must silence the ladder.
    """
    system = make_system(commitments=[_commitment(next_actions=None)])
    return system.watcher
