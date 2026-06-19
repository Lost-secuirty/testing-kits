"""Dynamic fixture for corpus_size_checker self-test: corpus_size is len() over a runtime
call with no anchorable named collection -> UNANALYZABLE (advisory). Models
core/feature_flag, whose matrix is enumerated inside a runner. AST-parsed only.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


def _combos():
    return [(a, b) for a in range(3) for b in range(3)]


def oracle(x):
    return x


def prove(impl):
    results = [impl(c) for c in _combos()]
    return any(r is None for r in results)


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=len(_combos()))
