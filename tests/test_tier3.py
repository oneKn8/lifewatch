"""Tier 3: the queue of things only the person can settle.

Tier 3 is the fallback for everything the tiers below it decline, including the
whole of Tier 2 when no model is installed, so it has to behave under repetition:
the same ambiguity arising every fifteen seconds must produce one question, not
one per poll.
"""

from datetime import datetime, timedelta

import pytest

from lifewatch.classify.tier3 import AskQueue, Question
from lifewatch.clock import FakeClock
from lifewatch.models import Klass
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)


def a_queue(tmp_path, name="t.db"):
    return AskQueue(Store(tmp_path / name, FakeClock(T0)))


# -- the behaviour the plan specifies ----------------------------------------


def test_an_enqueued_question_shows_up_as_pending(tmp_path):
    q = a_queue(tmp_path)
    q.enqueue("Some Video Title", block_id="b1", now=T0)
    assert len(q.pending()) == 1
    assert q.pending()[0].title == "Some Video Title"


def test_answering_removes_it_from_pending(tmp_path):
    q = a_queue(tmp_path)
    q.enqueue("Some Video Title", block_id="b1", now=T0)
    q.answer(q.pending()[0].id, Klass.DRIFT)
    assert q.pending() == []


def test_the_same_title_is_not_queued_twice_while_pending(tmp_path):
    q = a_queue(tmp_path)
    q.enqueue("Same Title", block_id="b1", now=T0)
    q.enqueue("Same Title", block_id="b1", now=T0)
    assert len(q.pending()) == 1


# -- the shape the web layer consumes ----------------------------------------


def test_a_question_carries_what_it_was_asked_about(tmp_path):
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    assert isinstance(question, Question)
    assert (question.title, question.block_id, question.asked_at) == (
        "Some Video Title", "b1", T0)
    assert question.answered_klass is None
    assert isinstance(question.id, int)


def test_the_question_shape_is_ready_for_json(tmp_path):
    q = a_queue(tmp_path)
    body = q.enqueue("Some Video Title", block_id="b1", now=T0).as_dict()
    assert body == {
        "id": body["id"],
        "title": "Some Video Title",
        "block_id": "b1",
        "asked_at": T0.isoformat(),
        "answered_klass": None,
    }


def test_enqueueing_the_same_title_returns_the_question_already_pending(tmp_path):
    q = a_queue(tmp_path)
    first = q.enqueue("Same Title", block_id="b1", now=T0)
    again = q.enqueue("Same Title", block_id="b1", now=T0 + timedelta(minutes=5))
    assert again.id == first.id
    assert again.asked_at == T0, "the question was first asked at T0, not later"


def test_different_titles_are_separate_questions(tmp_path):
    q = a_queue(tmp_path)
    q.enqueue("First Title", block_id="b1", now=T0)
    q.enqueue("Second Title", block_id="b1", now=T0 + timedelta(minutes=1))
    assert [x.title for x in q.pending()] == ["First Title", "Second Title"]


def test_pending_comes_back_oldest_first(tmp_path):
    q = a_queue(tmp_path)
    q.enqueue("Later", block_id="b1", now=T0 + timedelta(minutes=10))
    q.enqueue("Earlier", block_id="b1", now=T0)
    assert [x.title for x in q.pending()] == ["Earlier", "Later"]


def test_a_question_can_be_fetched_by_id(tmp_path):
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    assert q.question(question.id).title == "Some Video Title"
    assert q.question(question.id + 1000) is None


def test_questions_survive_a_restart(tmp_path):
    a_queue(tmp_path).enqueue("Some Video Title", block_id="b1", now=T0)
    reopened = a_queue(tmp_path)
    assert [x.title for x in reopened.pending()] == ["Some Video Title"]


# -- answering ---------------------------------------------------------------


def test_answering_records_the_class(tmp_path):
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    answered = q.answer(question.id, Klass.DRIFT)
    assert answered.answered_klass is Klass.DRIFT
    assert q.question(question.id).answered_klass is Klass.DRIFT


def test_a_class_arriving_as_a_string_is_accepted(tmp_path):
    """The web layer hands over whatever the phone posted."""
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    assert q.answer(question.id, "aligned").answered_klass is Klass.ALIGNED


def test_a_class_that_is_not_a_class_is_refused(tmp_path):
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    with pytest.raises(ValueError):
        q.answer(question.id, "banana")


def test_unknown_is_not_an_answer(tmp_path):
    """Answering "unknown" is not answering; the question stays open."""
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    with pytest.raises(ValueError):
        q.answer(question.id, Klass.UNKNOWN)
    assert len(q.pending()) == 1


def test_answering_a_question_that_does_not_exist_raises(tmp_path):
    with pytest.raises(KeyError):
        a_queue(tmp_path).answer(9999, Klass.DRIFT)


def test_answering_twice_the_same_way_is_harmless(tmp_path):
    """A double tap on a phone must not be an error."""
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    q.answer(question.id, Klass.DRIFT)
    assert q.answer(question.id, Klass.DRIFT).answered_klass is Klass.DRIFT


def test_an_answer_cannot_be_quietly_revised(tmp_path):
    """The answer is a record. Changing it would rewrite what was said."""
    q = a_queue(tmp_path)
    question = q.enqueue("Some Video Title", block_id="b1", now=T0)
    q.answer(question.id, Klass.DRIFT)
    with pytest.raises(ValueError):
        q.answer(question.id, Klass.ALIGNED)


def test_the_same_title_can_be_asked_again_once_the_previous_answer_is_in(tmp_path):
    """Learning is stage 2: an answer classifies its own interval and no other.

    Until a ruleset exists, the honest behaviour is to ask again the next time
    the same title turns up rather than to silently reuse yesterday's verdict.
    """
    q = a_queue(tmp_path)
    first = q.enqueue("Same Title", block_id="b1", now=T0)
    q.answer(first.id, Klass.DRIFT)
    second = q.enqueue("Same Title", block_id="b1", now=T0 + timedelta(days=1))
    assert second.id != first.id
    assert [x.title for x in q.pending()] == ["Same Title"]


# -- what cannot be asked ----------------------------------------------------


def test_a_blank_title_is_not_a_question(tmp_path):
    q = a_queue(tmp_path)
    for empty in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            q.enqueue(empty, block_id="b1", now=T0)
    assert q.pending() == []


def test_a_question_without_a_block_is_allowed(tmp_path):
    """Ambiguity outside a declared block is still answerable."""
    q = a_queue(tmp_path)
    assert q.enqueue("Some Video Title", block_id=None, now=T0).block_id is None
