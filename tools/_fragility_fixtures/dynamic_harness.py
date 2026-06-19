"""Dynamic fixture for fragility_checker self-test: corpus_size is a sum() over a
comprehension with no statically countable collection -> UNANALYZABLE (advisory).
AST-parsed by the checker only, never executed.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


def _rows():
    return [(a, b) for a in range(3) for b in range(3)]


def oracle(x):
    return x


def prove(impl):
    return any(impl(c) is None for c in _rows())


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=sum(1 for _ in _rows()))
