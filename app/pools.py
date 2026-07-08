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

_REVIEW_TYPES = ("support (за: supports the parent claim), refute (против: "
                 "argues against it), qualify (уточнение: narrows, conditions "
                 "or supplements it), question (вопрос: requests information "
                 "exposing a weak point), proposal (предложение: constructs — "
                 "'let's do X' — rather than reacting to a claim), exploration "
                 "(исследование/разбор: a LARGE unsettled investigation mixing "
                 "за, против and open questions, the author has NOT taken a "
                 "position)")


_ROOT_KINDS_DESC = ("argument (standalone claim opening a topic), question "
                    "(вопрос), proposal (предложение), exploration "
                    "(исследование/разбор: a LARGE unsettled investigation "
                    "mixing за, против and open questions, the author has NOT "
                    "taken a position)")


def review_draft(text, parent, branch, positions):
    """
    The pre-publication draft review (vault: ai-navigator-draft-review) — one
    LLM call that determines the draft's ACTUAL type, suggests ONE quality
    improvement, and compares it against the topic's nodes and positions.

    Deliberately does NOT take the author's declared type as input: the type,
    quality and split analysis is a property of the TEXT alone. Feeding the
    declared type into the prompt made the verdict depend on what the caller
    passed in, so the same text could be told "you should switch to A" and,
    once the author switched, "no, actually switch to B" — an oscillating
    loop with no way to land (vault: ai-navigator-type-loop). The caller
    compares the returned actual_type against whatever the author currently
    has selected, so the comparison — not the classification — is what
    changes when the author flips the dropdown.

    parent: {id, text}|None (None = drafting a new topic root).
    branch: rows from db.topic_subtree(). positions: [{id, headline, composed}].
    Returns {"actual_type", "type_note", "quality_note",
             "verdict": "new|similar|covered|answered|countered",
             "node_id", "position_id", "note", "split"}
    """
    is_root = parent is None
    type_vocab = _ROOT_KINDS_DESC if is_root else _REVIEW_TYPES
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
        f"The draft (this is a {'new topic root' if is_root else 'reply'}), "
        f"types available: {type_vocab}:\n\n{text}\n\n"
        "Review it. You are NOT told what type the author declared — judge "
        "the text purely on its own form:\n"
        "1. TYPE: which type does the text's own form actually fit? Write "
        "actual_type and, in type_note, ONE sentence describing what the "
        "text reads as and why (e.g. it opens with partial agreement before "
        "asking something, so it reads as a conditioned qualification rather "
        "than a plain question). Never phrase the note as a correction of a "
        "specific wrong type — you don't know which one the author picked.\n"
        "GENRE RULE: a LONG text (several paragraphs) that mixes claims, "
        "questions, additions and proposals in an unsettled, investigative "
        "way is an EXPLORATION — actual_type 'exploration', do NOT split it; "
        "atomization happens later with the author's consent. Never use "
        "'exploration' for a short reply or for a text that clearly argues "
        "one side at length.\n"
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
        "4. SPLIT: this is a property of the text, independent of steps 1-3 — "
        "always check it. ONLY when the draft is SHORT and glues together "
        "exactly TWO contributions of DIFFERENT types (e.g. a question plus "
        "a claim), provide 'split': the two parts, each with its own type. "
        "CUT, do not rewrite — reuse the author's own words with minimal "
        "glue; invent nothing. A long single-type text, however many points "
        "it makes, is NOT a split candidate. When you provide split, "
        "actual_type must be the type of the dominant part — NEVER "
        "'exploration' for a short draft.\n"
        "Respond with ONLY JSON:\n"
        '{"actual_type": "support|refute|qualify|question|proposal|exploration" '
        '(or "argument|question|proposal|exploration" for a topic root), '
        '"type_note": "one sentence, see above", '
        '"quality_note": "one concrete suggestion or empty", '
        '"verdict": "new|similar|covered|answered|countered", '
        '"node_id": <id or null>, "position_id": <id or null>, '
        '"note": "one sentence: what exists and what the draft would add, '
        'empty if verdict is new", '
        '"split": [{"type": "support|refute|qualify|question|proposal", '
        '"text": "..."}, {...}] or null}'
    )
    raw = poi.complete(REVIEW_SYSTEM, "\n\n---\n\n".join(parts),
                       max_tokens=1024, temperature=0)
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


ATOMIZE_SYSTEM = (
    "You atomize an EXPLORATION (разбор) — a text where the author "
    "investigates a topic without having settled on a position — into its "
    "constituent contributions, so a community can engage each one "
    "separately. You CUT, you never rewrite: every atom must reuse the "
    "author's own words with at most minimal glue for grammar; invent "
    "nothing, add nothing, sharpen nothing. Atoms from an exploration are "
    "points under investigation, NOT positions the author has taken. "
    "Everything you produce is a PROPOSAL the author will edit and approve "
    "or reject. Write group titles in the SAME LANGUAGE as the text."
)


def atomize(text):
    """
    Propose an atomization of an exploration (vault: exploration-atomization):
    thematic GROUPS, each holding ATOMS typed as claim/question/detail/
    proposal. A preview for the author to edit — nothing is published here.
    Returns {"groups": [{"title": str, "atoms": [{"type", "text"}]}]}
    """
    user = (
        f"The exploration to atomize:\n\n{text}\n\n"
        "Break it into thematic GROUPS (2-6, each a short title) and, inside "
        "each group, ATOMS — the individual contributions the text contains. "
        "Each atom gets a type:\n"
        "- 'argument': a claim / thesis the text advances or examines;\n"
        "- 'question': an open question the author poses;\n"
        "- 'detail': an addition, qualification or piece of context;\n"
        "- 'proposal': a constructive 'let's do X'.\n"
        "Use the author's own words (trim, do not rephrase). Skip filler; not "
        "every sentence must become an atom. 3-12 atoms total is typical.\n"
        "Respond with ONLY JSON:\n"
        '{"groups": [{"title": "short group title", '
        '"atoms": [{"type": "argument|question|detail|proposal", '
        '"text": "the atom, in the author\'s words"}]}]}'
    )
    raw = poi.complete(ATOMIZE_SYSTEM, user, max_tokens=4096)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)
