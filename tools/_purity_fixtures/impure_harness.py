"""Impure fixture for prove_purity_checker self-test: prove reaches a clock through a
helper -> IMPURE. Exercises the within-module call-graph BFS (the clock is one hop away
from prove, not a direct call). AST-parsed only, never executed.
"""

import time


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2)]


def _stamp():
    return time.monotonic()  # clock read on the proof path — must be flagged


def prove(impl):
    t = _stamp()
    return impl(1) != 2 or t > 0


TEETH = Teeth(prove=prove, oracle=lambda x: x, mutants=(), corpus_size=1)
