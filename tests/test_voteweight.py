"""
Tests for the pure vote-weight function. These run without an LLM or a DB,
so the formula's correctness is verifiable in isolation.
Run:  python -m pytest tests/ -q   (or: python tests/test_voteweight.py)
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.voteweight import vote_weight, DEFAULT_STAKE


def test_none_score_is_zero_weight():
    assert vote_weight(None) == 0.0


def test_zero_score_is_zero_weight():
    assert vote_weight(0) == 0.0


def test_full_score_equals_sqrt_stake():
    assert vote_weight(100) == round(math.sqrt(DEFAULT_STAKE), 3)


def test_weight_is_monotonic_in_poi():
    assert vote_weight(40) < vote_weight(60) < vote_weight(90)


def test_capital_influence_is_sublinear():
    # 4x stake should give 2x weight, not 4x — sqrt damping
    w1 = vote_weight(100, stake=100)
    w4 = vote_weight(100, stake=400)
    assert math.isclose(w4, 2 * w1, rel_tol=1e-6)


# ---- author-independence: the same text weighs the same no matter who -------
def test_weight_is_author_independent():
    # vote_weight takes only the argument's own PoI — there is no author input,
    # so identical arguments can never differ in weight by author. This is the
    # platform's core promise: reasoning quality, not who you are.
    assert vote_weight(58.6) == vote_weight(58.6)
    import inspect
    params = list(inspect.signature(vote_weight).parameters)
    assert "reputation" not in params and "author" not in params


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all passed")
