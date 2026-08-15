"""A spent pass must silence a block for as long as that block lasts.

The weekly allowance is counted by ISO week, which is correct: it refills
weekly. Whether a particular block was excused is a different question, and
inferring it from the week was wrong, because a block can straddle the boundary.

The hole this pins: a pass spent at 23:30 on a Sunday was invisible at 00:15 on
the Monday, so the watcher escalated on a block the user had already, legitimately,
bought out of. Rung 3 shifts the room lights and rung 4 rings the phone, so the
lived version is being woken up over a block you paid to skip.

That is the specific failure that gets an instrument like this uninstalled, and
uninstalling it is the only outcome worse than never building it.
"""

from datetime import datetime, timedelta

import pytest

from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.contract import Contract
from lifewatch.models import BlockState

# Sunday night into Monday morning: 2026-08-30 is ISO week 35, 2026-08-31 is 36.
SUNDAY_NIGHT = datetime(2026, 8, 30, 23, 30, 0)


@pytest.fixture
def contract():
    config = Config.empty()
    config.passes_per_week = 1
    return Contract(config, FakeClock(SUNDAY_NIGHT))


def test_the_fixture_really_does_straddle_an_iso_week_boundary():
    """Guard the guard: if the dates stop straddling, the rest proves nothing."""
    start_week = SUNDAY_NIGHT.isocalendar()[:2]
    end_week = (SUNDAY_NIGHT + timedelta(minutes=90)).isocalendar()[:2]
    assert start_week != end_week


def test_a_pass_excuses_the_block_it_was_spent_on(contract):
    block = contract.add_block(
        "COURSE-101", SUNDAY_NIGHT, SUNDAY_NIGHT + timedelta(minutes=90)
    )
    assert contract.use_pass(SUNDAY_NIGHT, block_id=block.id) is True
    assert block.state is BlockState.EXCUSED


def test_the_excusal_holds_after_the_week_rolls_over(contract):
    block = contract.add_block(
        "COURSE-101", SUNDAY_NIGHT, SUNDAY_NIGHT + timedelta(minutes=90)
    )
    contract.use_pass(SUNDAY_NIGHT, block_id=block.id)

    past_midnight = SUNDAY_NIGHT + timedelta(minutes=45)
    still_current = contract.current_block(past_midnight)
    assert still_current is not None
    assert still_current.state is BlockState.EXCUSED, (
        "the pass stopped counting when the ISO week changed"
    )


def test_the_allowance_itself_still_refills_weekly(contract):
    block = contract.add_block(
        "COURSE-101", SUNDAY_NIGHT, SUNDAY_NIGHT + timedelta(minutes=90)
    )
    contract.use_pass(SUNDAY_NIGHT, block_id=block.id)
    assert contract.passes_remaining(SUNDAY_NIGHT) == 0
    assert contract.passes_remaining(SUNDAY_NIGHT + timedelta(days=7)) == 1


def test_a_pass_may_still_be_spent_without_naming_a_block(contract):
    assert contract.use_pass(SUNDAY_NIGHT) is True
    assert contract.passes_remaining(SUNDAY_NIGHT) == 0


def test_a_refused_pass_does_not_excuse_anything(contract):
    """Both spends sit inside ISO week 35, so the second finds the purse empty.

    Placing the second block after midnight would test nothing: the week would
    have rolled over and the allowance legitimately refilled.
    """
    earlier = SUNDAY_NIGHT - timedelta(hours=3)
    first = contract.add_block("COURSE-101", earlier, earlier + timedelta(hours=1))
    second = contract.add_block(
        "COURSE-101", SUNDAY_NIGHT, SUNDAY_NIGHT + timedelta(minutes=90)
    )
    assert earlier.isocalendar()[:2] == SUNDAY_NIGHT.isocalendar()[:2]

    contract.use_pass(earlier, block_id=first.id)

    assert contract.use_pass(SUNDAY_NIGHT, block_id=second.id) is False
    assert second.state is BlockState.PLANNED, (
        "a refused pass must not excuse the block it was refused for"
    )


def test_a_pass_cannot_rewrite_a_block_that_already_settled(contract):
    block = contract.add_block(
        "COURSE-101", SUNDAY_NIGHT, SUNDAY_NIGHT + timedelta(minutes=90)
    )
    contract.complete_block(block.id, SUNDAY_NIGHT + timedelta(minutes=90))
    contract.use_pass(SUNDAY_NIGHT, block_id=block.id)
    assert block.state is BlockState.COMPLETED
