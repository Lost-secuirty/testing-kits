"""Pure fixture for prove_purity_checker self-test: prove reaches only pure code -> OK.

This file is AST-parsed by the checker, never imported or executed; the minimal Teeth
stand-in just gives the checker a ``TEETH = Teeth(prove=...)`` assignment to anchor on.
"""


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2), (3, 4)]


def _compare(a, b):
    return a == b


def prove(impl):
    # judges impl against the frozen CORPUS via a pure local helper — no clock/RNG/IO
    return any(not _compare(impl(x), y) for x, y in CORPUS)


TEETH = Teeth(prove=prove, oracle=lambda x: x, mutants=(), corpus_size=2)
