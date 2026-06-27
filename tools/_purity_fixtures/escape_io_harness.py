"""Impure fixture exercising the dynamic-exec and pathlib-I/O detections — both must read
IMPURE. `eval(...)` escapes static analysis; `Path(x).read_text()` is filesystem I/O reached
on a call-result (so the leaf-attr match, not the dotted match, is what catches it).
AST-parsed only, never executed.
"""

from pathlib import Path


class Teeth:
    def __init__(self, **kwargs):
        pass


def prove(impl):
    return bool(eval("0")) or Path("x").read_text() == "" or impl(1) != 2


TEETH = Teeth(prove=prove, oracle=lambda x: x, mutants=(), corpus_size=1)
