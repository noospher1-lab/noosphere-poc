"""
Layer 2 — composed view. The LLM clusters a topic's arguments into POOLS of
similar / complementary positions and writes a one-line synthesis per pool, so
users see distinct positions instead of raw spam. The audit layer (every node)
stays untouched; pools are a *view* computed on top of it.
"""

import json

from . import poi

SYSTEM = (
    "You merge a debate into a few strong POSITIONS. You are given the arguments "
    "of one discussion. Group arguments that make the same or complementary point "
    "into one pool. For each pool you do NOT summarize or shorten — you COMPOSE "
    "the single strongest, most complete version of that position by integrating "
    "the deepest formulation and EVERY complementary point any member added. Keep "
    "all nuance and unique sub-points; drop nothing of value; remove only literal "
    "duplication. The result should let a reader understand the position as deeply "
    "as possible. You judge by content, never by who wrote it."
)


def cluster_arguments(args):
    """
    args: [{id, text}] -> [{headline, composed, stance, member_ids}].
    `composed` is the maximally-deep merged argument; `headline` is a short title.
    May call the LLM.
    """
    if not args:
        return []
    listing = "\n".join(f'[{a["id"]}] {a["text"]}' for a in args)
    user = (
        f"Arguments in one debate, each as [id] then text:\n\n{listing}\n\n"
        "Group them into POOLS, each collecting arguments that make the same or "
        "complementary point. Every id must appear in exactly one pool. For each "
        "pool produce, in the SAME LANGUAGE as the arguments:\n"
        "- 'headline': a short title (3-7 words) of the position;\n"
        "- 'composed': the FULL merged argument — integrate the deepest version "
        "plus every complementary point from all members into one coherent, "
        "maximally deep argument. Do not summarize or shorten; preserve all "
        "distinct points and nuance, remove only literal repetition;\n"
        "- 'stance': toward the debate's main claim — 'support', 'oppose', or 'mixed';\n"
        "- 'member_ids': the argument ids in this pool.\n"
        "Respond with ONLY JSON, no prose: "
        '{"pools":[{"headline":"...","composed":"...","stance":"support|oppose|mixed","member_ids":[1,2]}]}'
    )
    raw = poi.complete(SYSTEM, user, max_tokens=4096)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(cleaned)
    return data.get("pools", [])


def compose_one(texts):
    """Re-compose a single position from its member argument texts.
    Returns {headline, composed}. Used when an argument is added to a position."""
    if not texts:
        return {"headline": "", "composed": ""}
    listing = "\n".join(f"- {t}" for t in texts)
    user = (
        f"These arguments all make one position in a debate:\n\n{listing}\n\n"
        "Compose them into ONE maximally deep argument: integrate the deepest "
        "formulation and every complementary point, preserve all nuance, remove "
        "only literal repetition — do not summarize or shorten. In the SAME "
        "LANGUAGE as the arguments. Respond with ONLY JSON: "
        '{"headline":"short title (3-7 words)","composed":"the full merged argument"}'
    )
    raw = poi.complete(SYSTEM, user, max_tokens=2048)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


def assign_argument(text, positions):
    """
    INCREMENTAL clustering (scaling draft: no full re-cluster per write).
    Given ONE new argument and the topic's existing positions, decide whether it
    belongs to one of them or opens a new position. One cheap LLM call whose
    input is bounded by the number of POSITIONS (a handful), not by the number
    of arguments in the topic — so cost stays flat as the topic grows.

    positions: [{id, headline, composed}]
    Returns {"position_id": int|None, "headline": str, "composed": str, "stance": str}
    — headline/composed/stance describe the NEW position when position_id is None.
    """
    listing = "\n".join(
        f'[{p["id"]}] {p["headline"]}: {p["composed"]}' for p in positions)
    user = (
        f"Existing POSITIONS in a debate, each as [id] headline: full text:\n\n{listing}\n\n"
        f"A NEW argument has arrived:\n\n{text}\n\n"
        "Does it make the same or a complementary point as one existing position "
        "(then it belongs there), or does it open a genuinely distinct position? "
        "Judge by content only. Respond with ONLY JSON:\n"
        '{"position_id": <existing id or null>, '
        '"headline": "short title (3-7 words) if new, else empty", '
        '"composed": "the argument as a full position text if new, else empty", '
        '"stance": "support|oppose|mixed toward the debate\'s main claim"}'
    )
    raw = poi.complete(SYSTEM, user, max_tokens=2048)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


PRECHECK_SYSTEM = (
    "You help a debate participant check, BEFORE posting, whether their draft "
    "already exists in the discussion. You see the discussion's current "
    "POSITIONS and the user's draft (an argument or a question). Judge by "
    "content only. Your goal is to help the person contribute at their best — "
    "point them to what already exists so they can build on it — never to "
    "gatekeep. When unsure, prefer 'new': a false 'covered' silences a person, "
    "a false 'new' merely adds a duplicate."
)


def precheck_draft(text, positions):
    """
    The AI navigator's pre-publication check (vault: poi-accrual-onboarding).
    One cheap call bounded by the number of POSITIONS. Returns
    {"verdict": "new"|"similar"|"covered", "position_id": int|None, "note": str}
    - new:     nothing like it in the discussion — post it;
    - similar: overlaps position N but may add something — worth a look first;
    - covered: the point is already fully made / the question already answered
               by position N.
    `note` is one helpful sentence in the SAME LANGUAGE as the draft.
    """
    listing = "\n".join(
        f'[{p["id"]}] {p["headline"]}: {p["composed"]}' for p in positions)
    user = (
        f"Current POSITIONS in the discussion, each as [id] headline: full text:"
        f"\n\n{listing}\n\n"
        f"The participant drafted this (argument or question):\n\n{text}\n\n"
        "Compare by content. Respond with ONLY JSON:\n"
        '{"verdict": "new" | "similar" | "covered", '
        '"position_id": <id of the closest position, or null if verdict is new>, '
        '"note": "one sentence, in the SAME LANGUAGE as the draft: what already '
        'exists and what (if anything) the draft would add"}'
    )
    raw = poi.complete(PRECHECK_SYSTEM, user, max_tokens=1024)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


REVIEW_SYSTEM = (
    "You are the AI navigator of an argument-graph platform. BEFORE a draft is "
    "published you review it against the discussion and help the author make "
    "the strongest possible contribution. You never gatekeep: everything you "
    "return is a suggestion the author is free to ignore. Judge content only, "
    "never the author. When unsure about overlap, prefer 'new' — a false "
    "'covered' silences a person, a false 'new' merely adds a duplicate. "
    "Write every note in the SAME LANGUAGE as the draft; when a note mentions "
    "a contribution type, use its natural-language name in that language "
    "(e.g. «против», «вопрос»), never the internal English code."
)

_REVIEW_TYPES = "support (за: supports the parent claim), refute (против: argues against it), qualify (уточнение: narrows or conditions it), question (вопрос: requests information exposing a weak point)"


def review_draft(text, declared_type, parent, branch, positions):
    """
    The pre-publication draft review (vault: ai-navigator-draft-review) — one
    LLM call that checks the draft's TYPE, suggests ONE quality improvement,
    and compares it against the topic's nodes and positions.

    declared_type: support|refute|qualify|question (or argument|question for
    a new topic root). parent: {id, text}|None. branch: rows from
    db.topic_subtree(). positions: [{id, headline, composed}].
    Returns {"type_ok", "suggested_type", "type_note", "quality_note",
             "verdict": "new|similar|covered|answered|countered",
             "node_id", "position_id", "note"}
    """
    parts = []
    if parent is not None:
        parts.append(f"The draft replies to this node:\n\n{parent['text']}")
    if branch:
        listing = "\n".join(
            f'{"  " * n["depth"]}[{n["id"]}] ({n["kind"]}'
            + (f', {n["rel"]} -> {n["parent_id"]}' if n["parent_id"] else ", topic root")
            + f') {n["text"]}' for n in branch)
        parts.append(
            "The discussion tree so far, one node per line as "
            "[id] (kind, relation -> parent id) text:\n\n" + listing)
    if positions:
        plist = "\n".join(
            f'[{p["id"]}] {p["headline"]}: {p["composed"]}' for p in positions)
        parts.append("Composed POSITIONS of the discussion, as [id] headline: "
                     "full text:\n\n" + plist)
    parts.append(
        f"The participant chose the contribution type '{declared_type}' "
        f"(types: {_REVIEW_TYPES}; 'argument' means a standalone claim opening "
        f"a topic) and drafted:\n\n{text}\n\n"
        "Review it:\n"
        "1. TYPE: does the text's form match the chosen type? An assertion "
        "posted as 'question', or a supporting point posted as 'refute', is a "
        "mismatch — suggest the type that fits what they actually wrote.\n"
        "IMPORTANT: whenever the declared type mismatches, do steps 2 and 3 "
        "for the type the text ACTUALLY is (your suggested_type) — one review "
        "must stay valid after the author switches the type. Never advise how "
        "to become a better specimen of the wrongly-declared type.\n"
        "2. QUALITY: ONE concrete, actionable suggestion — but ONLY if the "
        "draft has a genuinely important gap (a missing causal mechanism, a "
        "bare unsupported claim, an obvious unaddressed counter). A solid "
        "draft gets an empty string; most reasonable drafts should. Never "
        "invent nitpicks or polish requests.\n"
        "3. CONTEXT verdict, checked in this order:\n"
        "   - 'answered': the draft is a question and an existing NODE already "
        "answers it (set node_id);\n"
        "   - 'countered': the draft makes a claim and an existing NODE already "
        "objects to / addresses exactly that claim — the author should read it "
        "first and perhaps reply there (set node_id);\n"
        "   - 'covered': a POSITION already fully makes the point / answers the "
        "question (set position_id);\n"
        "   - 'similar': a POSITION overlaps but the draft may add something "
        "(set position_id);\n"
        "   - 'new': none of the above.\n"
        "Respond with ONLY JSON:\n"
        '{"type_ok": true|false, '
        '"suggested_type": "support|refute|qualify|question" or null, '
        '"type_note": "one sentence if type_ok is false, else empty", '
        '"quality_note": "one concrete suggestion or empty", '
        '"verdict": "new|similar|covered|answered|countered", '
        '"node_id": <id or null>, "position_id": <id or null>, '
        '"note": "one sentence: what exists and what the draft would add, '
        'empty if verdict is new"}'
    )
    raw = poi.complete(REVIEW_SYSTEM, "\n\n---\n\n".join(parts), max_tokens=1024)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


CONCLUDE_SYSTEM = (
    "You advance a reasoning chain. Given a position (a 'star') and the questions, "
    "clarifications and details raised around it (its 'orbit'), you write the "
    "CONCLUSION the discussion leads to: the next step that takes the position and "
    "every raised point into account and moves the reasoning forward. It is a new, "
    "sharper claim — not a summary of the old one."
)


def conclude(star_text, planet_texts):
    """Synthesize a star + its orbit into the next conclusion node.
    Returns {headline, composed}."""
    bullets = "\n".join(f"- {t}" for t in planet_texts) if planet_texts else "(пока пусто)"
    user = (
        f"POSITION (star):\n{star_text}\n\n"
        f"RAISED AROUND IT (orbit — questions, clarifications, details):\n{bullets}\n\n"
        "Write the CONCLUSION this leads to — the next step forward that accounts "
        "for the position and the raised points. In the SAME LANGUAGE. "
        'Respond with ONLY JSON: {"headline":"short title (3-7 words)","composed":"the conclusion"}'
    )
    raw = poi.complete(CONCLUDE_SYSTEM, user, max_tokens=2048)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)
