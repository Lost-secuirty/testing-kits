"""Single-case fixture for fragility_checker self-test: corpus_size counts a one-element
display, below the cardinality floor -> FRAGILE (or EXEMPT when waived). AST-parsed only.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2)]


def oracle(x):
    return x + 1


def prove(impl):
    return any(impl(x) != y for x, y in CORPUS)


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=len(CORPUS))
