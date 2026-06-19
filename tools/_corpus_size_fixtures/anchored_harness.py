"""Anchored fixture for corpus_size_checker self-test: corpus_size counts the very
collection prove() iterates -> OK. AST-parsed by the checker only, never executed.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2), (3, 4), (5, 6)]


def oracle(x):
    return x + 1


def prove(impl):
    for x, y in CORPUS:          # prove iterates CORPUS — the counted collection
        if impl(x) != y:
            return True
    return False


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=len(CORPUS))
