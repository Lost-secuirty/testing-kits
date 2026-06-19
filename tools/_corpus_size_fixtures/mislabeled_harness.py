"""Mislabeled fixture for corpus_size_checker self-test: corpus_size counts an INPUT the
verdict never judges -> MISLABELED. Models core/iot — prove feeds STREAM into impl but
anchors its pass/fail on a separate frozen literal. AST-parsed only, never executed.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


STREAM = [1, 2, 3, 4, 5]   # raw input, handed to impl
EXPECTED = 15              # the frozen literal the verdict actually compares against


def oracle(data):
    return sum(data)


def prove(impl):
    got = impl(STREAM)     # STREAM is an argument here, not iterated or compared
    return got != EXPECTED  # the verdict anchors on EXPECTED, not on STREAM


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=len(STREAM))
