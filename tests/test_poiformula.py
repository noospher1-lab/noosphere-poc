"""
Pure tests for the topic-PoI accrual formula (no DB, no LLM).
Formula: vault drafts/poi-accrual-onboarding.md v0.1.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.poiformula import topic_poi, DEFAULT_PRIOR, REACTION_CAP  # noqa: E402


def test_empty_is_default_prior():
    # no onboarding, no contributions -> the near-zero start
    assert topic_poi() == DEFAULT_PRIOR == 10.0


def test_shrinkage_from_default_prior():
    # (3·10 + 80) / 4 = 27.5 — one strong argument does not make an expert
    assert topic_poi(None, [(80, "argument")]) == 27.5
    # five strong arguments approach the onboarded start
    five = [(80, "argument")] * 5
    assert topic_poi(None, five) == round((30 + 400) / 8, 1) == 53.8


def test_onboarded_prior_starts_higher():
    # dialogue gave 60: (3·60 + 80) / 4 = 65
    assert topic_poi(60, [(80, "argument")]) == 65.0


def test_spam_drags_average_down():
    good = topic_poi(50, [(80, "argument")] * 3)
    spammed = topic_poi(50, [(80, "argument")] * 3 + [(20, "argument")] * 10)
    assert spammed < good


def test_questions_count_half():
    only_arg = topic_poi(None, [(80, "argument")])
    only_q = topic_poi(None, [(80, "question")])
    # same score, half the weight -> the question moves PoI less
    assert only_q < only_arg
    assert only_q == round((30 + 40) / 3.5, 1) == 20.0


def test_unscored_contributions_ignored():
    assert topic_poi(None, [(None, "argument")]) == DEFAULT_PRIOR


def test_reactions_are_capped():
    base = topic_poi(50, [(70, "argument")])
    # overwhelming support from strong reactors: still at most +CAP
    mob = [(90, "agree")] * 100
    boosted = topic_poi(50, [(70, "argument")], mob)
    assert boosted <= base + REACTION_CAP + 0.001
    # and disagreement is symmetric
    slammed = topic_poi(50, [(70, "argument")], [(90, "disagree")] * 100)
    assert slammed >= base - REACTION_CAP - 0.001


def test_fresh_account_reactions_negligible():
    base = topic_poi(50, [(70, "argument")])
    # ten brand-new accounts (PoI None -> 10) agreeing: net 100 -> +5·tanh(0.5) ≈ +2.3
    nudged = topic_poi(50, [(70, "argument")], [(None, "agree")] * 10)
    assert nudged - base < 2.5
    # one expert (88) agreeing moves less than the cap but more than noise
    expert = topic_poi(50, [(70, "argument")], [(88, "agree")])
    assert 0 < expert - base < 2.5


def test_clamped_to_0_100():
    assert topic_poi(0, [(0, "argument")] * 10, [(90, "disagree")] * 100) >= 0.0
    assert topic_poi(100, [(100, "argument")] * 10, [(90, "agree")] * 100) <= 100.0
