"""
Topic-PoI accrual formula (vault: drafts/poi-accrual-onboarding.md, v0.1).

    base = (K·P₀ + Σ wᵢ·scoreᵢ) / (K + Σ wᵢ)      shrinkage toward the prior
    adj  = CAP · tanh(net / SCALE)                  bounded reaction nudge
    PoI  = clamp(base + adj, 0, 100)

- P₀: the prior. Onboarding-dialogue score once that exists; a hand-seeded test
  value; otherwise DEFAULT_PRIOR = 10 (decision 2026-07-03: onboarding is
  optional — you can participate right away, but influence starts near zero
  and must be EARNED, by dialogue or by arguments).
- wᵢ: contribution weight — argument 1.0, question/detail 0.5.
- net: Σ reactor_topic_PoI · (+1 agree / −1 disagree) over reactions on the
  author's contributions in this topic. tanh caps the whole reaction channel
  at ±CAP points — reactions are wind, one's own reasoning is the engine
  (the anti-Matthew-effect guard).

Every input lives in the event log, so ANY change to these constants or shapes
is a replay, not a migration. Pure module: no DB, no LLM — trivially testable.
"""

import math

K = 3.0                 # prior strength (pseudo-observations)
DEFAULT_PRIOR = 10.0    # start before onboarding/seed — near-zero influence
WEIGHTS = {"argument": 1.0, "question": 0.5, "detail": 0.5}
REACTION_CAP = 5.0      # reactions can never move PoI by more than this
REACTION_SCALE = 200.0  # net weighted reactions for ~76% of the cap


def topic_poi(prior=None, contributions=(), reactions=()):
    """
    prior:         P₀ or None (-> DEFAULT_PRIOR)
    contributions: iterable of (score, kind) for the author's SCORED nodes
                   in the topic; unscored (None) entries are ignored
    reactions:     iterable of (reactor_poi, stance) on the author's nodes
                   in the topic, self-reactions excluded by the caller
    """
    p0 = DEFAULT_PRIOR if prior is None else float(prior)

    num, den = K * p0, K
    for score, kind in contributions:
        if score is None:
            continue
        w = WEIGHTS.get(kind or "argument", 0.0)
        num += w * float(score)
        den += w
    base = num / den

    net = 0.0
    for reactor_poi, stance in reactions:
        rp = DEFAULT_PRIOR if reactor_poi is None else float(reactor_poi)
        net += rp if stance == "agree" else -rp
    adj = REACTION_CAP * math.tanh(net / REACTION_SCALE)

    return round(max(0.0, min(100.0, base + adj)), 1)
