"""A REAL harness fixture: its self-test genuinely depends on the oracle, so neutering the
oracle must turn it red. The vacuity gate must classify this as TEETH."""

VACUITY_TARGETS = ["oracle"]


def oracle(value: int) -> int:
    return abs(value)


def buggy(value: int) -> int:
    return value


def _run_self_test() -> int:
    failures = 0
    if oracle(-3) != 3:        # neutering `oracle` breaks this — that is the point
        failures += 1
    if buggy(-3) == 3:         # the planted bug must be caught
        failures += 1
    return 1 if failures else 0
