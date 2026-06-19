"""Dead fixture for dead_expr_checker self-test: exactly seven bare side-effect-free
expression statements, one per flagged value kind, so the self-test can assert the count and
prove every kind is caught. AST-parsed only, never executed (these lines would do nothing).

The docstring above and the ellipsis/call lines below must NOT be flagged.
"""

from __future__ import annotations


class Teeth:
    def __init__(self, **kwargs):
        pass


CORPUS = [(1, 2)]
CONFIG = {"k": 1}


def prove(impl, flag, other):
    ...                 # ellipsis — NOT flagged
    print("noise")      # Call — NOT flagged
    flag                # 1: Name — dead
    impl.value          # 2: Attribute — dead
    impl(1) == 2        # 3: Compare — dead (a forgotten assert/return)
    impl(1) + other     # 4: BinOp — dead (computed and discarded)
    flag and other      # 5: BoolOp — dead
    not flag            # 6: UnaryOp — dead
    CONFIG["k"]         # 7: Subscript — dead (no-op lookup)
    return impl(1) != CORPUS[0][1]


TEETH = Teeth(prove=prove, oracle=lambda x: x, mutants=(), corpus_size=1)
