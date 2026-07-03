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
