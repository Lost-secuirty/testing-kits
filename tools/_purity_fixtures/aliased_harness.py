"""Impure fixture using an IMPORTED-ALIAS clock: `from time import monotonic; monotonic()`
inside prove -> IMPURE. Proves the import-map resolution closes the bare-name bypass that a
dotted-only matcher would miss. AST-parsed only, never executed.
"""

from time import monotonic as _now


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2)]


def prove(impl):
    # the clock is reached through a bare aliased name, not `time.monotonic`
    return impl(1) != 2 or _now() > 0


TEETH = Teeth(prove=prove, oracle=lambda x: x, mutants=(), corpus_size=1)
