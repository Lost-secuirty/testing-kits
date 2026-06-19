"""Robust fixture for fragility_checker self-test: corpus_size counts a 3-case display,
so the cardinality floor (>=2) holds -> OK. Uses an ANNOTATED ``TEETH: Teeth = ...`` so the
self-test also exercises the ast.AnnAssign path in _teeth_kwarg. AST-parsed only, never run.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2), (3, 4), (5, 6)]


def oracle(x):
    return x + 1


def prove(impl):
    return any(impl(x) != y for x, y in CORPUS)


TEETH: Teeth = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=len(CORPUS))
