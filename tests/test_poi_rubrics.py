"""
Pure checks on the PoI rubrics (no network): a contribution kind must be judged
by its OWN rubric, and rubric weights must be a proper normalized blend.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import poi                                       # noqa: E402


def _sums_to_one(weights):
    return abs(sum(weights.values()) - 1.0) < 1e-9


def test_every_rubric_is_normalized():
    for rubric in (poi.CRITERIA,
                   poi.QUESTION_CRITERIA_REPLY, poi.QUESTION_CRITERIA_ROOT,
                   poi.DETAIL_CRITERIA_REPLY, poi.DETAIL_CRITERIA_ROOT,
                   poi.PROPOSAL_CRITERIA, poi.EXPLORATION_CRITERIA):
        assert _sums_to_one(rubric), rubric


def test_root_rubrics_drop_relevance_and_renormalize():
    # a root question/detail has nothing external to be relevant to
    assert "relevance" not in poi.QUESTION_CRITERIA_ROOT
    assert "relevance" not in poi.DETAIL_CRITERIA_ROOT
    # the reply forms DO judge relevance to the parent claim
    assert "relevance" in poi.QUESTION_CRITERIA_REPLY
    assert "relevance" in poi.DETAIL_CRITERIA_REPLY


def test_detail_is_not_scored_as_an_argument():
    # the wrong-genre criterion (counterargument handling) must not touch a detail
    assert "counterargument" in poi.CRITERIA
    assert "counterargument" not in poi.DETAIL_CRITERIA_REPLY
    assert "counterargument" not in poi.DETAIL_CRITERIA_ROOT
    # and 'detail' has its own scorer wired, not the argument fallback
    from app import main
    assert main._SCORERS.get("detail") is poi.score_detail
    assert "detail" in main._PARENTED_KINDS
