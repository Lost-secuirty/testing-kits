#!/usr/bin/env python3
"""
graphql_test_harness.py — GraphQL schema/resolver coverage, depth/cost limits, N+1, cycles.
============================================================================================

Pure-stdlib. Zero external dependencies.

GraphQL backends ship without query-cost controls or schema/resolver parity:
schema fields with no resolver (or resolvers for fields that don't exist), N+1
resolver fan-out on list fields, fragment spreads that form cycles, and missing
depth/cost limits that turn a deeply-nested or alias-amplified query into a DoS.
This harness parses queries into an AST and runs coverage, depth, cost, N+1, and
fragment-cycle analyzers, proving the limits reject abusive queries while a
leaky resolver set and a naive (no-limit) executor are caught.

Distinct from `core/contract` (arbitrary-callable pre/post/invariants) and
`core/api` (REST CRUD). Port 19310 reserved; oracle runs in-process.

Usage:
  python harnesses/core/graphql_test_harness.py --self-test
  python harnesses/core/graphql_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import re
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Teeth  # noqa: E402

RESERVED_PORT = 19310


class GraphQLParseError(ValueError):
    pass


class QueryTooDeep(Exception):
    pass


class QueryTooCostly(Exception):
    pass


class UnknownField(Exception):
    pass


class UnknownFragment(Exception):
    pass


# ---------------------------------------------------------------------------
# Schema model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDef:
    type_name: str
    field_name: str
    field_type: str
    is_list: bool = False


@dataclass(frozen=True)
class FragmentDef:
    name: str
    on_type: str
    spreads: tuple[str, ...] = ()


@dataclass
class Schema:
    fields: dict[str, list[FieldDef]]
    query_type: str = "Query"

    def lookup(self, type_name: str, field_name: str) -> FieldDef | None:
        for fd in self.fields.get(type_name, []):
            if fd.field_name == field_name:
                return fd
        return None

    def all_pairs(self) -> set[tuple[str, str]]:
        return {(t, fd.field_name) for t, flist in self.fields.items() for fd in flist}


def _make_schema() -> Schema:
    f = FieldDef
    return Schema(fields={
        "Query": [f("Query", "user", "User"), f("Query", "users", "User", True),
                  f("Query", "search", "User", True)],
        "User": [f("User", "id", "ID"), f("User", "name", "String"),
                 f("User", "posts", "Post", True), f("User", "bestFriend", "User")],
        "Post": [f("Post", "id", "ID"), f("Post", "title", "String"),
                 f("Post", "comments", "Comment", True)],
        "Comment": [f("Comment", "id", "ID"), f("Comment", "body", "String")],
    })


SCHEMA = _make_schema()
RESOLVERS_FULL = SCHEMA.all_pairs()


# ---------------------------------------------------------------------------
# Query parser → AST
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    name: str
    alias: str | None
    is_fragment_spread: bool
    fragment_name: str | None
    children: list[Selection] = field(default_factory=list)


_TOKEN = re.compile(r"\.\.\.|[{}():]|[A-Za-z_][A-Za-z0-9_]*")
_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


class _Parser:
    def __init__(self, tokens: list[str]):
        self.t = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self) -> str:
        if self.i >= len(self.t):
            raise GraphQLParseError("unexpected end of query")
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, tok: str) -> str:
        if self.peek() != tok:
            raise GraphQLParseError(f"expected {tok!r}, got {self.peek()!r}")
        return self.next()


def parse_query(src: str) -> Selection:
    p = _Parser(_TOKEN.findall(src))
    p.expect("{")
    children = _parse_selections(p)
    p.expect("}")
    if p.peek() is not None:
        raise GraphQLParseError(f"trailing tokens after query: {p.peek()!r}")
    return Selection("", None, False, None, children)


def _parse_selections(p: _Parser) -> list[Selection]:
    sels: list[Selection] = []
    while p.peek() is not None and p.peek() != "}":
        sels.append(_parse_selection(p))
    return sels


def _parse_selection(p: _Parser) -> Selection:
    if p.peek() == "...":
        p.next()
        name = p.next()
        if not _NAME.match(name):
            raise GraphQLParseError(f"expected fragment name, got {name!r}")
        return Selection("", None, True, name, [])
    name = p.next()
    if not _NAME.match(name):
        raise GraphQLParseError(f"expected field name, got {name!r}")
    alias = None
    if p.peek() == ":":
        p.next()
        alias = name
        name = p.next()
        if not _NAME.match(name):
            raise GraphQLParseError(f"expected aliased field name, got {name!r}")
    if p.peek() == "(":
        _skip_args(p)
    children: list[Selection] = []
    if p.peek() == "{":
        p.next()
        children = _parse_selections(p)
        p.expect("}")
    return Selection(name, alias, False, None, children)


def _skip_args(p: _Parser) -> None:
    p.expect("(")
    depth = 1
    while depth > 0:
        tok = p.next()
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1


# ---------------------------------------------------------------------------
# Analyzers
# ---------------------------------------------------------------------------


def schema_resolver_coverage(schema: Schema,
                             resolvers: set[tuple[str, str]]
                             ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (missing_resolvers, orphan_resolvers)."""
    pairs = schema.all_pairs()
    return sorted(pairs - resolvers), sorted(resolvers - pairs)


def query_depth(node: Selection) -> int:
    best = 0
    for c in node.children:
        best = max(best, 1) if c.is_fragment_spread else max(best, 1 + query_depth(c))
    return best


def _sel_cost(sel: Selection, schema: Schema, type_name: str, config: GraphQLConfig) -> int:
    fd = schema.lookup(type_name, sel.name)
    child_type = fd.field_type if fd else type_name
    real_children = [c for c in sel.children if not c.is_fragment_spread]
    if real_children:
        sub = sum(_sel_cost(c, schema, child_type, config) for c in real_children)
    else:
        sub = config.scalar_cost
    if fd and fd.is_list:
        return config.list_field_cost * sub
    return sub


def query_cost(node: Selection, schema: Schema, config: GraphQLConfig) -> int:
    return sum(_sel_cost(c, schema, schema.query_type, config)
               for c in node.children if not c.is_fragment_spread)


def detect_n_plus_one(node: Selection, schema: Schema,
                      type_name: str | None = None,
                      batched: frozenset[tuple[str, str]] = frozenset()
                      ) -> list[tuple[str, str]]:
    type_name = type_name or schema.query_type
    found: list[tuple[str, str]] = []
    for sel in node.children:
        if sel.is_fragment_spread:
            continue
        fd = schema.lookup(type_name, sel.name)
        if fd is None:
            continue
        if fd.is_list:
            for ch in sel.children:
                if ch.is_fragment_spread:
                    continue
                cfd = schema.lookup(fd.field_type, ch.name)
                if cfd and (cfd.is_list or cfd.field_type in schema.fields):
                    key = (fd.field_type, ch.name)
                    if key not in batched:
                        found.append(key)
        found.extend(detect_n_plus_one(sel, schema, fd.field_type, batched))
    return found


def fragment_cycles(fragments: dict[str, FragmentDef]) -> int:
    in_cycle: set[str] = set()

    def dfs(name: str, stack: list[str]) -> None:
        frag = fragments.get(name)
        if not frag:
            return
        for sp in frag.spreads:
            if sp in stack:
                in_cycle.update(stack[stack.index(sp):])
                in_cycle.add(sp)
            elif sp in fragments:
                dfs(sp, stack + [sp])
    for n in fragments:
        dfs(n, [n])
    return len(in_cycle)


def validate_fields(node: Selection, schema: Schema,
                    type_name: str | None = None) -> None:
    type_name = type_name or schema.query_type
    for sel in node.children:
        if sel.is_fragment_spread:
            continue
        fd = schema.lookup(type_name, sel.name)
        if fd is None:
            raise UnknownField(f"{type_name}.{sel.name}")
        validate_fields(sel, schema, fd.field_type)


def validate_fragments(node: Selection, fragments: dict[str, FragmentDef]) -> None:
    for sel in node.children:
        if sel.is_fragment_spread:
            if sel.fragment_name not in fragments:
                raise UnknownFragment(str(sel.fragment_name))
        else:
            validate_fragments(sel, fragments)


def enforce_limits(node: Selection, schema: Schema, config: GraphQLConfig) -> int:
    depth = query_depth(node)
    if depth > config.max_depth:
        raise QueryTooDeep(f"depth {depth} > {config.max_depth}")
    cost = query_cost(node, schema, config)
    if cost > config.max_cost:
        raise QueryTooCostly(f"cost {cost} > {config.max_cost}")
    return cost


def naive_execute(node: Selection, schema: Schema, config: GraphQLConfig) -> int:
    """Broken executor: runs without any depth/cost guard."""
    return query_cost(node, schema, config)


# ---------------------------------------------------------------------------
# Config + report + fixtures
# ---------------------------------------------------------------------------


@dataclass
class GraphQLConfig:
    max_depth: int = 8
    max_cost: int = 1_000
    list_field_cost: int = 10
    scalar_cost: int = 1


@dataclass
class GraphQLReport:
    schema_fields: int
    resolved_fields: int
    missing_resolvers: int
    orphan_resolvers: int
    max_query_depth: int
    max_query_cost: int
    n_plus_one_fields: int
    fragment_cycles: int
    rejected_queries: int

    @property
    def fully_covered(self) -> bool:
        return self.missing_resolvers == 0 and self.orphan_resolvers == 0


def _deep_query(bestfriends: int) -> str:
    s = "id"
    for _ in range(bestfriends):
        s = "bestFriend { " + s + " }"
    return "{ user { " + s + " } }"


def _wide_alias_query(n: int) -> str:
    return "{ " + " ".join(f"a{i}:users {{ id }}" for i in range(n)) + " }"


VALID_QUERIES: dict[str, str] = {
    "small": "{ user { id name } }",
    "with_args": "{ user(id: 1) { id posts { id title } } }",
    "fragment": "{ user { ...userFields } }",
}

ABUSIVE_QUERIES: dict[str, str] = {
    "deep": _deep_query(10),
    "costly": "{ users { posts { comments { id body } } } }",
    "wide_alias": _wide_alias_query(120),
}

FRAGMENTS_ACYCLIC: dict[str, FragmentDef] = {
    "userFields": FragmentDef("userFields", "User", ("postFields",)),
    "postFields": FragmentDef("postFields", "Post", ()),
}
FRAGMENTS_DIRECT_CYCLE: dict[str, FragmentDef] = {
    "a": FragmentDef("a", "User", ("b",)),
    "b": FragmentDef("b", "User", ("a",)),
}
FRAGMENTS_INDIRECT_CYCLE: dict[str, FragmentDef] = {
    "a": FragmentDef("a", "User", ("b",)),
    "b": FragmentDef("b", "User", ("c",)),
    "c": FragmentDef("c", "User", ("a",)),
}

LEAKY_RESOLVERS = (RESOLVERS_FULL - {("Comment", "body")}) | {("User", "ghostField")}


def audit(schema: Schema, resolvers: set[tuple[str, str]], query_srcs: list[str],
          config: GraphQLConfig, fragments: dict[str, FragmentDef]) -> GraphQLReport:
    missing, orphan = schema_resolver_coverage(schema, resolvers)
    max_depth = max_cost = npo = rejected = 0
    for q in query_srcs:
        node = parse_query(q)
        max_depth = max(max_depth, query_depth(node))
        max_cost = max(max_cost, query_cost(node, schema, config))
        npo += len(detect_n_plus_one(node, schema))
        try:
            enforce_limits(node, schema, config)
        except (QueryTooDeep, QueryTooCostly):
            rejected += 1
    return GraphQLReport(
        schema_fields=len(schema.all_pairs()),
        resolved_fields=len(resolvers & schema.all_pairs()),
        missing_resolvers=len(missing),
        orphan_resolvers=len(orphan),
        max_query_depth=max_depth,
        max_query_cost=max_cost,
        n_plus_one_fields=npo,
        fragment_cycles=fragment_cycles(fragments),
        rejected_queries=rejected,
    )


# ---------------------------------------------------------------------------
# Teeth: the oracle (enforce_limits) accepts every in-budget query and rejects
# every abusive one; the naive (no-limit) executor waves the abusive queries
# through and is caught. Corpus = the frozen VALID_QUERIES / ABUSIVE_QUERIES.
# ---------------------------------------------------------------------------
# Each case: (query source, must_reject). enforce_limits/naive_execute share the
# (node, schema, config) signature; "caught" means the impl disagrees with the
# expected accept/reject verdict (or raises an unexpected exception).
_TEETH_CASES: tuple[tuple[str, bool], ...] = (
    tuple((src, False) for src in VALID_QUERIES.values())
    + tuple((src, True) for src in ABUSIVE_QUERIES.values())
)


def _prove(impl: Callable[..., object]) -> bool:
    """True iff `impl` disagrees with the frozen corpus on any case (i.e. caught)."""
    cfg = GraphQLConfig()
    for src, must_reject in _TEETH_CASES:
        node = parse_query(src)
        try:
            impl(node, SCHEMA, cfg)
            rejected = False
        except (QueryTooDeep, QueryTooCostly):
            rejected = True
        except Exception:  # noqa: BLE001 — an unexpected raise counts as caught
            return True
        if rejected != must_reject:
            return True
    return False


TEETH = Teeth(
    prove=_prove,
    oracle=enforce_limits,
    mutants=(
        Mutant("no_limit_executor", naive_execute,
               "executor runs without any depth/cost guard, accepting abusive queries"),
    ),
    corpus_size=len(_TEETH_CASES),
    kind="oracle_swap",
    notes="an over-depth or over-cost query must be rejected, not executed",
)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class GqlCheck:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> GqlCheck:
    return GqlCheck(name, bool(cond), detail)


def _cfg() -> GraphQLConfig:
    return GraphQLConfig()


def _raises(fn: Callable[[], object], exc: type) -> bool:
    try:
        fn()
        return False
    except exc:
        return True


def s_schema_full_resolver_coverage() -> GqlCheck:
    missing, orphan = schema_resolver_coverage(SCHEMA, RESOLVERS_FULL)
    return _chk("schema_full_resolver_coverage", missing == [] and orphan == [],
                f"missing={missing} orphan={orphan}")


def s_missing_resolver_detected() -> GqlCheck:
    missing, _ = schema_resolver_coverage(SCHEMA, RESOLVERS_FULL - {("User", "name")})
    return _chk("missing_resolver_detected", ("User", "name") in missing, f"{missing}")


def s_orphan_resolver_detected() -> GqlCheck:
    _, orphan = schema_resolver_coverage(SCHEMA, RESOLVERS_FULL | {("User", "ghost")})
    return _chk("orphan_resolver_detected", ("User", "ghost") in orphan, f"{orphan}")


def s_depth_within_limit_accepted() -> GqlCheck:
    node = parse_query(VALID_QUERIES["small"])
    return _chk("depth_within_limit_accepted",
                not _raises(lambda: enforce_limits(node, SCHEMA, _cfg()), QueryTooDeep),
                f"depth={query_depth(node)}")


def s_depth_exceeds_limit_rejected() -> GqlCheck:
    node = parse_query(ABUSIVE_QUERIES["deep"])
    return _chk("depth_exceeds_limit_rejected",
                _raises(lambda: enforce_limits(node, SCHEMA, _cfg()), QueryTooDeep),
                f"depth={query_depth(node)}")


def s_cost_within_budget_accepted() -> GqlCheck:
    node = parse_query(VALID_QUERIES["small"])
    return _chk("cost_within_budget_accepted",
                query_cost(node, SCHEMA, _cfg()) <= _cfg().max_cost,
                f"cost={query_cost(node, SCHEMA, _cfg())}")


def s_cost_exceeds_budget_rejected() -> GqlCheck:
    node = parse_query(ABUSIVE_QUERIES["costly"])
    return _chk("cost_exceeds_budget_rejected",
                _raises(lambda: enforce_limits(node, SCHEMA, _cfg()), QueryTooCostly),
                f"cost={query_cost(node, SCHEMA, _cfg())}")


def s_aliased_fields_counted_in_cost() -> GqlCheck:
    one = query_cost(parse_query("{ user { id } }"), SCHEMA, _cfg())
    two = query_cost(parse_query("{ a:user { id } b:user { id } }"), SCHEMA, _cfg())
    return _chk("aliased_fields_counted_in_cost", two == 2 * one, f"one={one} two={two}")


def s_list_field_multiplies_cost() -> GqlCheck:
    cfg = _cfg()
    cost = query_cost(parse_query("{ users { id } }"), SCHEMA, cfg)
    return _chk("list_field_multiplies_cost", cost == cfg.list_field_cost * cfg.scalar_cost,
                f"cost={cost}")


def s_nested_list_cost_compounds() -> GqlCheck:
    single = query_cost(parse_query("{ users { id } }"), SCHEMA, _cfg())
    nested = query_cost(parse_query("{ users { posts { id } } }"), SCHEMA, _cfg())
    return _chk("nested_list_cost_compounds", nested > single, f"single={single} nested={nested}")


def s_n_plus_one_list_resolver_detected() -> GqlCheck:
    node = parse_query("{ users { posts { id } } }")
    found = detect_n_plus_one(node, SCHEMA)
    return _chk("n_plus_one_list_resolver_detected", ("User", "posts") in found, f"{found}")


def s_batched_resolver_not_flagged() -> GqlCheck:
    node = parse_query("{ users { posts { id } } }")
    found = detect_n_plus_one(node, SCHEMA, batched=frozenset({("User", "posts")}))
    return _chk("batched_resolver_not_flagged", found == [], f"{found}")


def s_fragment_no_cycle_ok() -> GqlCheck:
    return _chk("fragment_no_cycle_ok", fragment_cycles(FRAGMENTS_ACYCLIC) == 0, "")


def s_fragment_direct_cycle_detected() -> GqlCheck:
    return _chk("fragment_direct_cycle_detected", fragment_cycles(FRAGMENTS_DIRECT_CYCLE) >= 1, "")


def s_fragment_indirect_cycle_detected() -> GqlCheck:
    return _chk("fragment_indirect_cycle_detected",
                fragment_cycles(FRAGMENTS_INDIRECT_CYCLE) >= 1, "")


def s_unknown_field_in_query_rejected() -> GqlCheck:
    node = parse_query("{ user { nope } }")
    return _chk("unknown_field_in_query_rejected",
                _raises(lambda: validate_fields(node, SCHEMA), UnknownField), "")


def s_unknown_fragment_spread_rejected() -> GqlCheck:
    node = parse_query("{ user { ...ghostFrag } }")
    return _chk("unknown_fragment_spread_rejected",
                _raises(lambda: validate_fragments(node, FRAGMENTS_ACYCLIC), UnknownFragment), "")


def s_deeply_nested_dos_query_rejected() -> GqlCheck:
    node = parse_query(ABUSIVE_QUERIES["deep"])
    return _chk("deeply_nested_dos_query_rejected",
                _raises(lambda: enforce_limits(node, SCHEMA, _cfg()), QueryTooDeep), "")


def s_wide_alias_amplification_rejected() -> GqlCheck:
    node = parse_query(ABUSIVE_QUERIES["wide_alias"])
    return _chk("wide_alias_amplification_rejected",
                _raises(lambda: enforce_limits(node, SCHEMA, _cfg()), QueryTooCostly),
                f"cost={query_cost(node, SCHEMA, _cfg())}")


def s_leaky_resolver_set_detected() -> GqlCheck:
    missing, orphan = schema_resolver_coverage(SCHEMA, LEAKY_RESOLVERS)
    return _chk("leaky_resolver_set_detected", len(missing) >= 1 and len(orphan) >= 1,
                f"missing={missing} orphan={orphan}")


def s_naive_executor_accepts_abusive() -> GqlCheck:
    node = parse_query(ABUSIVE_QUERIES["costly"])
    oracle_rejects = _raises(lambda: enforce_limits(node, SCHEMA, _cfg()), QueryTooCostly)
    naive_ok = not _raises(lambda: naive_execute(node, SCHEMA, _cfg()), Exception)
    return _chk("naive_executor_accepts_abusive", oracle_rejects and naive_ok, "")


SCENARIOS: dict[str, Callable[[], GqlCheck]] = {
    f.__name__[2:]: f
    for f in [
        s_schema_full_resolver_coverage,
        s_missing_resolver_detected,
        s_orphan_resolver_detected,
        s_depth_within_limit_accepted,
        s_depth_exceeds_limit_rejected,
        s_cost_within_budget_accepted,
        s_cost_exceeds_budget_rejected,
        s_aliased_fields_counted_in_cost,
        s_list_field_multiplies_cost,
        s_nested_list_cost_compounds,
        s_n_plus_one_list_resolver_detected,
        s_batched_resolver_not_flagged,
        s_fragment_no_cycle_ok,
        s_fragment_direct_cycle_detected,
        s_fragment_indirect_cycle_detected,
        s_unknown_field_in_query_rejected,
        s_unknown_fragment_spread_rejected,
        s_deeply_nested_dos_query_rejected,
        s_wide_alias_amplification_rejected,
        s_leaky_resolver_set_detected,
        s_naive_executor_accepts_abusive,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    failures = [r for r in results if not r.passed]
    for r in results:
        if verbose or not r.passed:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:42s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GraphQL schema/cost/coverage harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
