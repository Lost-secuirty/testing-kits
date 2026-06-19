"""Circular fixture: prove recomputes the expected value by calling the oracle at runtime
THROUGH A HELPER -> CIRCULAR. Exercises transitive detection (prove -> _expected -> oracle).
AST-parsed only, never executed.
"""


class Teeth:
    def __init__(self, **kwargs):
        pass


INPUTS = [1, 2, 3]


def oracle(x):
    return x * x


def _expected(x):
    return oracle(x)  # runtime oracle call, reached one hop from prove


def prove(impl):
    return any(impl(x) != _expected(x) for x in INPUTS)


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=3)
