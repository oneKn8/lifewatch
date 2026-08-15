"""The orchestrator: which tier gets asked, in what order, about what evidence.

Two properties are load-bearing here and are the reason this file exists
separately from the per-tier tests.

The cheapest tier that can decide is the one that decides, so a mechanical
verdict must never cost a model call.

And evidence has an age. The window sensor emits nothing while no window holds
focus, so the last focus observation can sit in the store unchanged for hours;
reading it as a fact about the present would let a title from this morning
classify this afternoon.
"""

from datetime import datetime, timedelta

from lifewatch.classify import DEFAULT_STALENESS_S, TIER_ASK, TIER_UNDECIDED, Classifier
from lifewatch.classify.tier3 import AskQueue
from lifewatch.clock import FakeClock
from lifewatch.config import Config
from lifewatch.models import Block, Klass, Observation
from lifewatch.store import Store

T0 = datetime(2026, 8, 24, 7, 0, 0)


def obs(kind, value, sensor="window", offset=0, meta=None):
    return Observation(T0 + timedelta(seconds=offset), sensor, kind, value, meta or {})


def a_block(start=T0, minutes=90, commitment_id="COURSE-101"):
    return Block(id="blk-1", commitment_id=commitment_id, planned_start=start,
                 planned_end=start + timedelta(minutes=minutes))


class SpyJudge:
    """Records every prompt it is handed and answers with whatever it was told to."""

    def __init__(self, answer="aligned"):
        self.answer = answer
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def a_classifier(tmp_path, judge=None, config=None, **kwargs):
    config = config or Config.empty()
    queue = AskQueue(Store(tmp_path / "t.db", FakeClock(T0)))
    return Classifier(config, queue, judge=judge, **kwargs), queue


# -- tier order --------------------------------------------------------------


def test_a_mechanical_verdict_never_costs_a_model_call(tmp_path):
    judge = SpyJudge()
    classifier, queue = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("ms", str(20 * 60 * 1000), sensor="idle")], a_block(), T0)
    assert result.klass is Klass.ABSENT
    assert result.tier == 1
    assert judge.prompts == []
    assert queue.pending() == []


def test_an_ambiguous_title_goes_to_the_model(tmp_path):
    judge = SpyJudge("aligned")
    classifier, queue = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Lecture Title")], a_block(), T0)
    assert result.klass is Klass.ALIGNED
    assert result.tier == 2
    assert "Some Lecture Title" in judge.prompts[0]
    assert queue.pending() == [], "the model decided; nobody needs to be asked"


def test_a_model_that_cannot_decide_puts_the_question_to_the_user(tmp_path):
    classifier, queue = a_classifier(tmp_path, judge=SpyJudge("no idea"))
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")], a_block(), T0)
    assert result.klass is Klass.UNKNOWN
    assert result.tier == TIER_ASK == 3
    assert [q.title for q in queue.pending()] == ["Some Video Title"]
    assert queue.pending()[0].block_id == "blk-1"


def test_a_model_that_fails_puts_the_question_to_the_user(tmp_path):
    judge = SpyJudge(ConnectionError("no local model"))
    classifier, queue = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")], a_block(), T0)
    assert result.klass is Klass.UNKNOWN
    assert [q.title for q in queue.pending()] == ["Some Video Title"]


def test_with_no_model_configured_the_question_goes_straight_to_the_user(tmp_path):
    """Spec 17.1: no local runtime installed falls back to asking, not to guessing."""
    classifier, queue = a_classifier(tmp_path, judge=None)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")], a_block(), T0)
    assert result.klass is Klass.UNKNOWN
    assert [q.title for q in queue.pending()] == ["Some Video Title"]


def test_the_same_ambiguity_is_only_asked_once(tmp_path):
    """The sensor loop revisits this moment every fifteen seconds."""
    classifier, queue = a_classifier(tmp_path, judge=SpyJudge("no idea"))
    for offset in (0, 15, 30):
        classifier.classify(
            [obs("focus", "TestBrowser|Some Video Title", offset=offset)],
            a_block(), T0 + timedelta(seconds=offset))
    assert len(queue.pending()) == 1


# -- evidence has an age -----------------------------------------------------


def test_stale_evidence_is_not_current_evidence(tmp_path):
    """The focus sensor emits nothing while no window has focus.

    Without an age check the last title observed would keep classifying every
    hour after it, which is the exact bug this bound exists to close.
    """
    judge = SpyJudge("drift")
    classifier, queue = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")],
        a_block(start=T0, minutes=600),
        T0 + timedelta(seconds=DEFAULT_STALENESS_S + 1))
    assert judge.prompts == []
    assert queue.pending() == []
    assert result.klass is Klass.UNKNOWN
    assert result.tier == TIER_UNDECIDED


def test_stale_idle_evidence_does_not_report_absence(tmp_path):
    classifier, _ = a_classifier(tmp_path, judge=SpyJudge())
    result = classifier.classify(
        [obs("ms", str(20 * 60 * 1000), sensor="idle")],
        a_block(start=T0, minutes=600),
        T0 + timedelta(seconds=DEFAULT_STALENESS_S + 1))
    assert result.klass is not Klass.ABSENT


def test_evidence_inside_the_bound_is_still_evidence(tmp_path):
    judge = SpyJudge("drift")
    classifier, _ = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")],
        a_block(start=T0, minutes=600),
        T0 + timedelta(seconds=DEFAULT_STALENESS_S))
    assert result.klass is Klass.DRIFT
    assert len(judge.prompts) == 1


def test_the_staleness_bound_is_configurable(tmp_path):
    judge = SpyJudge("drift")
    classifier, _ = a_classifier(tmp_path, judge=judge, staleness_s=60)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")], a_block(),
        T0 + timedelta(seconds=61))
    assert judge.prompts == []
    assert result.tier == TIER_UNDECIDED


def test_the_staleness_bound_can_come_from_the_config_file(tmp_path):
    config = Config.empty()
    config.classifier = {"staleness_s": 60}
    judge = SpyJudge("drift")
    classifier, _ = a_classifier(tmp_path, judge=judge, config=config)
    classifier.classify([obs("focus", "TestBrowser|Some Video Title")], a_block(),
                        T0 + timedelta(seconds=61))
    assert judge.prompts == []


def test_the_default_bound_outlasts_the_sensor_heartbeat(tmp_path):
    """An unchanged value is only re-stamped every heartbeat (600 s by default).

    A bound tighter than that would discard evidence that is merely steady, so
    the default allows a full missed heartbeat before it stops believing.
    """
    assert DEFAULT_STALENESS_S > 600


def test_observations_from_the_future_are_not_evidence(tmp_path):
    judge = SpyJudge("drift")
    classifier, _ = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title", offset=60)], a_block(), T0)
    assert judge.prompts == []
    assert result.tier == TIER_UNDECIDED


# -- what the model is allowed to know ---------------------------------------


def test_the_prompt_carries_nothing_but_the_title_and_the_commitment(tmp_path):
    """Everything else in the moment stays on this side of the call."""
    judge = SpyJudge("aligned")
    config = Config.empty()
    config.commitments = [{"id": "COURSE-101", "label": "Test Course"}]
    classifier, _ = a_classifier(tmp_path, judge=judge, config=config)
    classifier.classify(
        [
            obs("focus", "TestBrowser|Some Video Title"),
            obs("place", "test-place-name", sensor="network"),
            obs("ms", "4242", sensor="idle"),
        ],
        a_block(),
        T0,
    )
    prompt = judge.prompts[0]
    for leaked in ("test-place-name", "4242", "blk-1", T0.isoformat()):
        assert leaked not in prompt


def test_the_commitment_label_is_preferred_over_its_id(tmp_path):
    judge = SpyJudge("aligned")
    config = Config.empty()
    config.commitments = [{"id": "COURSE-101", "label": "Test Course"}]
    classifier, _ = a_classifier(tmp_path, judge=judge, config=config)
    classifier.classify([obs("focus", "TestBrowser|Some Video Title")],
                        a_block(), T0)
    assert "Test Course" in judge.prompts[0]


def test_the_commitment_id_is_used_when_no_label_is_configured(tmp_path):
    judge = SpyJudge("aligned")
    classifier, _ = a_classifier(tmp_path, judge=judge)
    classifier.classify([obs("focus", "TestBrowser|Some Video Title")],
                        a_block(), T0)
    assert "COURSE-101" in judge.prompts[0]


# -- the span a verdict claims ----------------------------------------------


def test_a_verdict_ends_now_and_starts_no_earlier_than_the_block(tmp_path):
    """A block cannot be charged for time before it was declared."""
    judge = SpyJudge("drift")
    classifier, _ = a_classifier(tmp_path, judge=judge)
    block = a_block(start=T0 + timedelta(minutes=10))
    now = T0 + timedelta(minutes=20)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title", offset=60)], block, now)
    assert result.end == now
    assert result.start == block.planned_start


def test_an_undecided_moment_claims_no_minutes(tmp_path):
    classifier, _ = a_classifier(tmp_path, judge=SpyJudge())
    now = T0 + timedelta(minutes=20)
    result = classifier.classify([], a_block(), now)
    assert result.start == result.end == now


# -- nothing declared --------------------------------------------------------


def test_unclaimed_time_is_not_a_question(tmp_path):
    """With no block there is no commitment to judge a title against.

    Unclaimed waking time is already the loudest thing on the grid; turning it
    into a stream of questions would teach the user to ignore the questions.
    """
    judge = SpyJudge("aligned")
    classifier, queue = a_classifier(tmp_path, judge=judge)
    result = classifier.classify(
        [obs("focus", "TestBrowser|Some Video Title")], None, T0)
    assert judge.prompts == []
    assert queue.pending() == []
    assert result.klass is Klass.UNKNOWN


def test_an_accounted_place_still_wins_without_a_block(tmp_path):
    config = Config.empty()
    config.accounted_places = ["test-campus"]
    classifier, _ = a_classifier(tmp_path, judge=SpyJudge(), config=config)
    result = classifier.classify(
        [obs("place", "test-campus", sensor="network")], None, T0)
    assert result.klass is Klass.ACCOUNTED


def test_the_idle_threshold_comes_from_the_config(tmp_path):
    config = Config.empty()
    config.idle_threshold_s = 60
    classifier, _ = a_classifier(tmp_path, judge=SpyJudge(), config=config)
    result = classifier.classify(
        [obs("ms", str(120 * 1000), sensor="idle")], a_block(), T0)
    assert result.klass is Klass.ABSENT


def test_a_focus_observation_with_no_title_is_not_asked_about(tmp_path):
    judge = SpyJudge("aligned")
    classifier, queue = a_classifier(tmp_path, judge=judge)
    result = classifier.classify([obs("focus", "TestBrowser|")], a_block(), T0)
    assert judge.prompts == []
    assert queue.pending() == []
    assert result.tier == TIER_UNDECIDED


def test_every_verdict_says_which_tier_reached_it(tmp_path):
    classifier, _ = a_classifier(tmp_path, judge=SpyJudge("drift"))
    cases = [
        ([obs("ms", str(20 * 60 * 1000), sensor="idle")], 1),
        ([obs("focus", "TestBrowser|Some Video Title")], 2),
        ([], TIER_UNDECIDED),
    ]
    for observations, tier in cases:
        assert classifier.classify(observations, a_block(), T0).tier == tier


def test_every_verdict_carries_a_reason(tmp_path):
    classifier, _ = a_classifier(tmp_path, judge=SpyJudge("no idea"))
    for observations in ([obs("focus", "TestBrowser|Some Video Title")], []):
        assert classifier.classify(observations, a_block(), T0).reason.strip()
