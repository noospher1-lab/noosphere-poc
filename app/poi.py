"""
PoI scoring for the PoC.

Given an argument's text, call the LLM (through the existing proxy layer) and
return a STRUCTURED score: per-criterion sub-scores plus a composite, never a
single opaque number (constitution Part II, definition of done).

This is the semantic layer ONLY. Per the project's own framing, the semantic
layer alone does not provide sybil-resistance — that is an emergent property
across the semantic, identification, economic, and social layers. The PoC
proves the semantic scoring is real, nothing more.

Key handling: the API key is read from the environment / proxy layer. It is
NEVER hard-coded (constitution Part II).
"""

import os
import json
import urllib.request

# Five criteria. Weights are illustrative for the PoC and live in one place
# so they are tunable — the Greypaper treats PoI weights as tunable.
CRITERIA = {
    "clarity":            0.20,  # is the claim stated precisely?
    "depth":              0.25,  # are the main factors of the topic engaged?
    "counterargument":    0.25,  # does it handle the strongest opposing case?
    "evidence":           0.15,  # are claims grounded rather than asserted?
    "awareness_of_limits":0.15,  # does it acknowledge uncertainty / scope?
}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a reasoning-quality evaluator for an argument graph. "
    "You assess the QUALITY OF REASONING in a single argument, not whether you "
    "agree with its conclusion, and not the identity of its author. "
    "Score each criterion from 0 to 100. Be calibrated: 50 is an average "
    "argument, 80+ is genuinely strong, 90+ is rare. "
    "Also extract a 'topic': a 2-4 word noun phrase naming the specific theme "
    "this argument raises, in the SAME LANGUAGE as the argument. It should name "
    "the angle the argument adds, not restate its relation to other claims. "
    "Respond with ONLY a JSON object, no preamble, no markdown fences, of the form: "
    '{"clarity": int, "depth": int, "counterargument": int, "evidence": int, '
    '"awareness_of_limits": int, "topic": "short theme", '
    '"comment": "one sentence justification"}'
)


def complete(system, user, max_tokens=1024, timeout=90):
    """Generic Anthropic call: (system, user) -> concatenated text. Key from env."""
    return complete_messages(system, [{"role": "user", "content": user}],
                             max_tokens=max_tokens, timeout=timeout)


def complete_messages(system, messages, max_tokens=1024, timeout=120,
                      temperature=None):
    """Multi-turn Anthropic call (used by the PoI dialogue). Key from env."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Route through the proxy/key layer; "
            "never hard-code the key (constitution Part II)."
        )

    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return "".join(b.get("text", "") for b in data.get("content", []))


def _call_llm(argument_text):
    """Call the Anthropic API through the proxy. Returns raw text content."""
    return complete(SYSTEM_PROMPT, f"Argument to evaluate:\n\n{argument_text}", max_tokens=1024)


# A QUESTION is judged on its own merits, not as an argument — a sharp question
# that exposes a real weak point is high quality even though it asserts nothing.
QUESTION_CRITERIA = {
    "relevance":     0.25,  # does it bear on the position / topic?
    "incisiveness":  0.30,  # does it expose a real weak point or hidden assumption?
    "depth":         0.20,  # does it open meaningful inquiry, not surface trivia?
    "clarity":       0.15,  # is it clearly posed and answerable?
    "generativity":  0.10,  # does it move the dialogue forward (not rhetorical/lazy)?
}

QUESTION_SYSTEM_PROMPT = (
    "You evaluate the QUALITY OF A QUESTION asked about a claim in a debate. You "
    "do NOT judge it as an argument — a question asserts nothing. A sharp question "
    "that exposes a real weakness, hidden assumption, or missing evidence is "
    "high quality; a lazy, rhetorical, off-topic or trivial question is low. "
    "Score each criterion 0 to 100. Be calibrated: 50 is an average question, "
    "80+ genuinely sharp, 90+ rare. "
    "Also extract a 'topic': a 2-4 word noun phrase naming what the question "
    "probes, in the SAME LANGUAGE as the question. "
    "Respond with ONLY a JSON object, no markdown, of the form: "
    '{"relevance": int, "incisiveness": int, "depth": int, "clarity": int, '
    '"generativity": int, "topic": "short theme", "comment": "one sentence"}'
)


def score_question(question_text):
    """Returns (composite_score, breakdown_dict) using the question rubric."""
    raw = complete(QUESTION_SYSTEM_PROMPT, f"Question to evaluate:\n\n{question_text}", max_tokens=1024)
    parsed = _parse(raw)
    composite = 0.0
    breakdown = {}
    for criterion, weight in QUESTION_CRITERIA.items():
        sub = float(parsed.get(criterion, 0))
        breakdown[criterion] = sub
        composite += sub * weight
    breakdown["topic"] = parsed.get("topic", "")
    breakdown["comment"] = parsed.get("comment", "")
    breakdown["kind"] = "question"
    return round(composite, 1), breakdown


# A PROPOSAL constructs rather than reacts (vault: exploration-atomization):
# "let's do X" is judged on whether it could actually become a decision.
PROPOSAL_CRITERIA = {
    "concreteness":       0.25,  # is the proposed action specific enough to act on?
    "problem_fit":        0.25,  # does it address problems actually raised in the topic?
    "feasibility":        0.20,  # could it plausibly be implemented (means, actors)?
    "consequences":       0.15,  # are effects and side-effects thought through?
    "awareness_of_limits":0.15,  # does it acknowledge costs, risks, open points?
}

PROPOSAL_SYSTEM_PROMPT = (
    "You evaluate the QUALITY OF A PROPOSAL made in a debate — a constructive "
    "'let's do X', judged on whether it could mature into a decision, not on "
    "whether you agree with it. Score each criterion 0 to 100. Be calibrated: "
    "50 is an average proposal, 80+ genuinely actionable and well-grounded, "
    "90+ rare. Also extract a 'topic': a 2-4 word noun phrase naming what the "
    "proposal is about, in the SAME LANGUAGE as the proposal. "
    "Respond with ONLY a JSON object, no markdown, of the form: "
    '{"concreteness": int, "problem_fit": int, "feasibility": int, '
    '"consequences": int, "awareness_of_limits": int, "topic": "short theme", '
    '"comment": "one sentence"}'
)


def score_proposal(proposal_text):
    """Returns (composite_score, breakdown_dict) using the proposal rubric."""
    raw = complete(PROPOSAL_SYSTEM_PROMPT,
                   f"Proposal to evaluate:\n\n{proposal_text}", max_tokens=1024)
    return _compose(_parse(raw), PROPOSAL_CRITERIA, "proposal")


# An EXPLORATION is an unsettled investigation — the author holds за, против
# and open questions AT ONCE. Balance is a virtue here, where the argument
# rubric would punish it as indecision (vault: exploration-atomization).
EXPLORATION_CRITERIA = {
    "evenhandedness":  0.25,  # честность к обеим сторонам: strongest case each way?
    "depth":           0.25,  # does it engage the topic's real mechanics?
    "question_quality":0.20,  # are the open questions sharp and answerable?
    "fact_vs_guess":   0.15,  # are facts separated from assumptions?
    "coverage":        0.15,  # are the topic's main aspects visited?
}

EXPLORATION_SYSTEM_PROMPT = (
    "You evaluate the QUALITY OF AN EXPLORATION (разбор) — a text where the "
    "author investigates a topic WITHOUT having settled on a position: it "
    "legitimately mixes points for, points against, qualifications and open "
    "questions. Do NOT punish the absence of a conclusion; an honest 'I do "
    "not know yet' backed by sharp questions is a strength. Punish hidden "
    "advocacy dressed as exploration, shallow both-sides-ism, and facts "
    "blurred into speculation. Score each criterion 0 to 100, calibrated: "
    "50 average, 80+ genuinely illuminating, 90+ rare. Also extract a "
    "'topic': a 2-4 word noun phrase in the SAME LANGUAGE as the text. "
    "Respond with ONLY a JSON object, no markdown, of the form: "
    '{"evenhandedness": int, "depth": int, "question_quality": int, '
    '"fact_vs_guess": int, "coverage": int, "topic": "short theme", '
    '"comment": "one sentence"}'
)


def score_exploration(exploration_text):
    """Returns (composite_score, breakdown_dict) using the exploration rubric."""
    raw = complete(EXPLORATION_SYSTEM_PROMPT,
                   f"Exploration to evaluate:\n\n{exploration_text}", max_tokens=1024)
    return _compose(_parse(raw), EXPLORATION_CRITERIA, "exploration")


def _compose(parsed, criteria, kind):
    """Weighted composite + breakdown from a parsed rubric response."""
    composite = 0.0
    breakdown = {}
    for criterion, weight in criteria.items():
        sub = float(parsed.get(criterion, 0))
        breakdown[criterion] = sub
        composite += sub * weight
    breakdown["topic"] = parsed.get("topic", "")
    breakdown["comment"] = parsed.get("comment", "")
    breakdown["kind"] = kind
    return round(composite, 1), breakdown


def _parse(raw):
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


def score_argument(argument_text):
    """
    Returns (composite_score, breakdown_dict).
    breakdown_dict contains each criterion's sub-score plus the comment.
    """
    raw = _call_llm(argument_text)
    parsed = _parse(raw)

    composite = 0.0
    breakdown = {}
    for criterion, weight in CRITERIA.items():
        sub = float(parsed.get(criterion, 0))
        breakdown[criterion] = sub
        composite += sub * weight

    breakdown["topic"] = parsed.get("topic", "")
    breakdown["comment"] = parsed.get("comment", "")
    return round(composite, 1), breakdown
