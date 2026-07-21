"""
Block B — the debate material a decision is voted on.

Rendered ONCE when a decision opens, stored verbatim in
decisions.material_snapshot, and never regenerated (vault:
decisions/vote-dialogue-weight, decisions/public-corpus-external-ai).

Two properties this module exists to guarantee:

1. DETERMINISM. This text is the cached prompt prefix shared by every voter in
   a decision. Prompt caching is a byte-prefix match, so a single differing
   byte between two renders costs a full cache miss for every voter behind it.
   Everything here is therefore ordered by primary key, carries no timestamp,
   no counter, and no dict iteration order.

2. NO AUTHORS. Names are deliberately absent. They are not needed to understand
   a question, and putting them in front of a voter reintroduces the appeal to
   authority that voteweight.py exists to keep out of the graph.

The same text is what the public .md view serves to external AI readers, so it
doubles as the machine-readable representation of a debate.
"""

# Kept at module scope so the interlocutor, the judge and the public view all
# frame the material identically — three different framings of the same debate
# would be three different prefixes, and one of them would be wrong.
CLOSING = (
    "This is the ENTIRE material of this debate. Nothing outside it is "
    "available to you, and its absence is itself information."
)


# Minimum cacheable prefix, per model family. A prefix shorter than this simply
# does not cache — no error, no warning, just cache_creation_input_tokens: 0.
# Surfaced at open time because the failure is otherwise invisible: everything
# works, the bill is just quietly higher than the projection.
MIN_CACHEABLE_TOKENS = {
    "claude-opus-4-8": 4096,
    "claude-opus-4-7": 4096,
    "claude-haiku-4-5": 4096,
    "claude-sonnet-4-6": 2048,
}


def estimate_tokens(text):
    """Rough char/4 estimate — enough to answer 'will this cache at all'."""
    return len(text) // 4


def cache_outlook(prefix_tokens, model):
    """
    Whether a decision's prefix is long enough to cache, and what that means.

    A short prefix is not a bug: a small debate is cheap to re-read, so there
    is little to save. It matters only because the per-voter cost projection
    assumes caching — so the answer belongs in the open response rather than in
    someone's head.
    """
    minimum = MIN_CACHEABLE_TOKENS.get(model, 4096)
    caches = prefix_tokens >= minimum
    return {
        "prefix_tokens_est": prefix_tokens,
        "min_cacheable": minimum,
        "will_cache": caches,
        "note": (
            "prefix is cached: each voter re-reads it at ~0.1x"
            if caches else
            f"prefix is ~{prefix_tokens} tokens, under the {minimum}-token "
            "minimum — it will NOT cache. Harmless while the debate is small "
            "(little to save), but per-voter cost is full price until it grows."
        ),
    }


def _section(title, lines):
    """A section is omitted entirely when empty — an empty heading reads to the
    model as 'this exists but is blank' rather than 'this does not apply'."""
    if not lines:
        return []
    return [title, ""] + lines + [""]


def render(question, positions, questions, atoms, dissents):
    """
    Render block B. All arguments are plain lists of dicts already ordered by
    id — ordering is the caller's job (db.topic_material does it in SQL) so
    that this function is pure and trivially testable for byte-stability.
    """
    out = ["<material>", "", "QUESTION PUT TO A VOTE:", question.strip(), ""]

    pos_lines = []
    for p in positions:
        stance = (p.get("stance") or "unaligned").strip()
        headline = (p.get("headline") or "").strip()
        composed = (p.get("composed") or "").strip()
        pos_lines.append(f"[P{p['id']}] ({stance}) {headline}")
        if composed:
            pos_lines.append(composed)
        pos_lines.append("")
    out += _section(
        "POSITIONS PEOPLE HOLD (each composed from the arguments of its members):",
        pos_lines)

    out += _section(
        "WHERE THE DEBATE DIVERGES (open questions raised inside it):",
        [f"[Q{q['id']}] {q['text'].strip()}" for q in questions])

    # Atoms are points still UNDER investigation — labelling them as claims
    # would let a voter treat an unsettled point as a settled one, which is
    # exactly what criterion 4 of the rubric penalises them for.
    atom_lines = []
    for a in atoms:
        group = (a.get("atom_group") or "").strip()
        prefix = f"[{group}] " if group else ""
        atom_lines.append(f"[A{a['id']}] {prefix}{a['text'].strip()}")
    out += _section(
        "STILL UNSETTLED (points under investigation, not resolved claims):",
        atom_lines)

    out += _section(
        "DISSENTS (arguments their authors pulled out of a composed position, "
        "verbatim):",
        [f"[D{d['id']}] {d['text'].strip()}" for d in dissents])

    out += ["</material>", "", CLOSING]
    return "\n".join(out).rstrip() + "\n"
