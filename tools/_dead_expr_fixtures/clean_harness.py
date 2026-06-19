"""Clean fixture for dead_expr_checker self-test: no bare side-effect-free expression
statements -> OK. AST-parsed by the checker only, never imported or executed.

The constructs below are exactly the ones the checker must NOT flag: a module/function
docstring, an ellipsis placeholder, bare calls (which may have side effects), a walrus, an
await, and a yield.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2), (3, 4)]


def _noop():
    ...  # ellipsis placeholder — not dead code


def prove(impl):
    """Docstring statements are constants, never flagged."""
    _noop()                      # bare Call — excluded (may have side effects)
    print("running")             # bare Call — excluded
    if (total := len(CORPUS)):   # walrus binds a name — excluded
        return total > 0
    return any(impl(x) != y for x, y in CORPUS)


async def _stream(source):
    await source.next()          # bare Await — excluded
    yield 1                      # bare Yield — excluded


TEETH = Teeth(prove=prove, oracle=lambda x: x, mutants=(), corpus_size=2)
