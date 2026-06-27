"""Non-circular fixture: prove compares impl output to a FROZEN literal, never calling the
oracle at runtime -> OK. AST-parsed only, never executed.
"""


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 1), (2, 4)]  # (input, frozen expected) — the expected values are baked in


def oracle(x):
    return x * x


def prove(impl):
    # judges against the frozen `expected` literal; the oracle function is never invoked
    return any(impl(x) != expected for x, expected in CORPUS)


TEETH = Teeth(prove=prove, oracle=oracle, mutants=(), corpus_size=2)
