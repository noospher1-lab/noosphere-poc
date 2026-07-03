"""
Vote-weight computation for the PoC.

A PURE function: given nodes with PoI scores, compute each node's vote weight.
The point is that the formula computes on real PoI scores — not that the
economy is built. Stake is a fixed constant for the PoC (constitution Part II).

Formula (PoC form):
    weight = sqrt(stake) * (poi_score / 100)

An argument stands on its OWN reasoning quality: the SAME text carries the same
weight no matter who posted it. We deliberately do NOT fold the author's
reputation into this number — that would reintroduce an appeal to authority and
the rich-get-richer dynamic the platform exists to prevent.

The author's PoI is a SEPARATE, per-topic signal (see author_topic_poi) used as
a *lens* to explore the graph — who argued/voted, grouped by understanding of
that specific topic — never as a multiplier on an argument's weight.

- sqrt(stake) keeps capital influence sub-linear, so weight is not simply
  "money buys votes" — this is the anti-plutocracy intent of the design.
- (poi_score / 100) scales influence by argument quality. A node with no
  PoI score yet contributes zero weight until evaluated.

This is deliberately simple and self-contained so it is trivially testable.
"""

import math

# Fixed for the PoC. Real staking mechanics are explicitly out of scope.
DEFAULT_STAKE = 100.0


def vote_weight(poi_score, stake=DEFAULT_STAKE):
    if poi_score is None:
        return 0.0
    return round(math.sqrt(stake) * (poi_score / 100.0), 3)


def compute_graph_weights(graph):
    """
    Take a graph dict (from db.get_graph) and return a list of
    {id, text, poi_score, weight} for every node.
    """
    out = []
    for n in graph["nodes"]:
        out.append(
            {
                "id": n["id"],
                "text": n["text"],
                "poi_score": n["poi_score"],
                "author": n.get("author"),
                "weight": vote_weight(n["poi_score"]),
            }
        )
    return out
