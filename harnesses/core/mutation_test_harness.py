"""
Mutation Test Harness (Harness 12 of 36)

Injects small code changes (mutants) into Python source and checks if a
test suite catches them. Provides a mock HTTP server on a dynamic port
(default 18980) for reporting results.
"""

import ast
import code
import copy
import enum
import http.server
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import traceback
import types
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


# ---------------------------------------------------------------------------
# MutationResult enum
# ---------------------------------------------------------------------------

class MutationResult(enum.Enum):
    """Classification for each mutant run."""
    KILLED = "KILLED"        # Test suite detected the mutation (test failed)
    SURVIVED = "SURVIVED"    # Test suite did not detect the mutation (test passed)
    ERROR = "ERROR"          # Mutant caused a compilation/execution error
    TIMEOUT = "TIMEOUT"      # Mutant took too long to run


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------

class MutationOperator(enum.Enum):
    """The six supported mutation operators."""
    ARITHMETIC_SWAP = "arithmetic_swap"
    COMPARISON_SWAP = "comparison_swap"
    CONSTANT_SWAP = "constant_swap"
    BOOLEAN_SWAP = "boolean_swap"
    RETURN_SWAP = "return_swap"
    CONDITION_NEGATION = "condition_negation"


@dataclass
class Mutant:
    """A single mutant: the modified source and metadata about the change."""
    original_source: str
    mutated_source: str
    operator: MutationOperator
    description: str
    location: Optional[str] = None   # e.g. "line 42"
    mutant_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    result: Optional[MutationResult] = None
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Mutator
# ---------------------------------------------------------------------------

class Mutator:
    """
    Applies the six mutation operators to Python source strings via regex.

    Each operator method returns a list of (mutated_source, description)
    tuples, one per possible mutation site.
    """

    # ------------------------------------------------------------------
    # 1. Arithmetic swap
    # ------------------------------------------------------------------

    # We swap one operator at a time, being careful not to double-count
    # compound operators like ** or //
    _ARITH_SWAPS: List[Tuple[str, str]] = [
        (r'\*\*', '@@POWER@@'),   # protect ** first
        (r'//',   '@@FLOOR@@'),   # protect // first
    ]

    _ARITH_REPLACEMENTS: Dict[str, str] = {
        '+':  '-',
        '-':  '+',
        '*':  '/',
        '/':  '*',
        '//': '**',
        '**': '//',
        '%':  '*',
    }

    def arithmetic_swap(self, source: str) -> List[Tuple[str, str]]:
        """Swap arithmetic operators one at a time."""
        results = []
        # Pattern matches arithmetic operators not part of augmented assignment,
        # comparison operators, or decorators.
        # We find all candidate positions and create one mutant per position.

        # Protect strings and comments first (basic approach: work on tokens)
        lines = source.splitlines(keepends=True)
        mutants = []

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            # Strip comment portion
            code_part, _, _comment = self._split_comment(line)

            # Remove string literals to avoid mutating inside strings
            masked = self._mask_strings(code_part)

            # Look for standalone arithmetic operators
            # Order matters: check longer tokens first
            for op, replacement in [
                ('//', '**'), ('**', '//'),
                ('+', '-'), ('-', '+'),
                ('*', '/'), ('/', '*'),
                ('%', '*'),
            ]:
                pattern = self._arith_pattern(op)
                for m in re.finditer(pattern, masked):
                    # Reconstruct source with this single substitution
                    orig_char_offset = sum(len(l) for l in lines[:line_idx]) + m.start()
                    new_line = (
                        code_part[:m.start()]
                        + m.group().replace(op, replacement, 1)
                        + code_part[m.end():]
                        + (('#' + _comment) if _comment else '')
                    )
                    new_lines = lines[:line_idx] + [new_line] + lines[line_idx + 1:]
                    mutated = ''.join(new_lines)
                    desc = f"arithmetic_swap: '{op}' -> '{replacement}' on line {line_no}"
                    mutants.append((mutated, desc))

        return mutants

    def _arith_pattern(self, op: str) -> str:
        """Return a regex that matches the arithmetic op but not compound ops."""
        escaped = re.escape(op)
        if op == '+':
            # Don't match +=, ++
            return r'(?<![+\-*/<>=!])' + escaped + r'(?!=)'
        if op == '-':
            # Don't match -=, -> (type hints), or unary minus after ( , = [ {
            return r'(?<![+\-*/<>=!])' + escaped + r'(?![=>])'
        if op == '*':
            return r'(?<!\*)' + escaped + r'(?!\*|=)'
        if op == '/':
            return r'(?<!/)' + escaped + r'(?!/|=)'
        if op == '%':
            return escaped + r'(?!=)'
        if op == '//':
            return r'//(?!=)'
        if op == '**':
            return r'\*\*(?!=)'
        return re.escape(op)

    def _split_comment(self, line: str) -> Tuple[str, str, str]:
        """Split a line into (code_part, '#', comment) outside of strings."""
        in_string = False
        string_char = ''
        for i, ch in enumerate(line):
            if in_string:
                if ch == string_char and (i == 0 or line[i-1] != '\\'):
                    in_string = False
            else:
                if ch in ('"', "'"):
                    in_string = True
                    string_char = ch
                elif ch == '#':
                    return line[:i], '#', line[i+1:]
        return line, '', ''

    def _mask_strings(self, code: str) -> str:
        """Replace string literal contents with spaces to avoid mutating them."""
        result = list(code)
        i = 0
        while i < len(code):
            if code[i] in ('"', "'"):
                q = code[i]
                # Check for triple quote
                if code[i:i+3] in ('"""', "'''"):
                    q = code[i:i+3]
                j = i + len(q)
                while j < len(code):
                    if code[j] == '\\':
                        j += 2
                        continue
                    if code[j:j+len(q)] == q:
                        j += len(q)
                        break
                    result[j] = ' '
                    j += 1
                i = j
            else:
                i += 1
        return ''.join(result)

    # ------------------------------------------------------------------
    # 2. Comparison swap
    # ------------------------------------------------------------------

    _COMPARISON_PAIRS: List[Tuple[str, str]] = [
        ('==', '!='),
        ('!=', '=='),
        ('<=', '>='),
        ('>=', '<='),
        ('<',  '>'),
        ('>',  '<'),
        ('is not', 'is'),
        ('is', 'is not'),
        ('not in', 'in'),
        ('in', 'not in'),
    ]

    def comparison_swap(self, source: str) -> List[Tuple[str, str]]:
        """Swap comparison operators one at a time."""
        results = []
        lines = source.splitlines(keepends=True)

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            code_part, sep, comment = self._split_comment(line)
            masked = self._mask_strings(code_part)

            for op, replacement in self._COMPARISON_PAIRS:
                escaped = re.escape(op)
                # For word-based operators add word boundaries
                if op[0].isalpha() or op[-1].isalpha():
                    pattern = r'\b' + escaped + r'\b'
                else:
                    # Avoid matching substrings, e.g. '<' should not match '<='
                    if op == '<':
                        pattern = r'<(?!=|<)'
                    elif op == '>':
                        pattern = r'>(?!=|>)'
                    elif op == '==':
                        pattern = r'=='
                    elif op == '!=':
                        pattern = r'!='
                    elif op == '<=':
                        pattern = r'<='
                    elif op == '>=':
                        pattern = r'>='
                    else:
                        pattern = escaped

                for m in re.finditer(pattern, masked):
                    new_line = (
                        code_part[:m.start()]
                        + replacement
                        + code_part[m.end():]
                        + (sep + comment if comment else '')
                    )
                    new_lines = lines[:line_idx] + [new_line] + lines[line_idx + 1:]
                    mutated = ''.join(new_lines)
                    desc = f"comparison_swap: '{op}' -> '{replacement}' on line {line_no}"
                    results.append((mutated, desc))

        return results

    # ------------------------------------------------------------------
    # 3. Constant swap
    # ------------------------------------------------------------------

    _CONSTANT_PAIRS: List[Tuple[str, str]] = [
        (r'\bTrue\b',  'False'),
        (r'\bFalse\b', 'True'),
        (r'\bNone\b',  '0'),
        (r'\b0\b',     '1'),
        (r'\b1\b',     '0'),
        (r'\b-1\b',    '1'),
        (r'""',        '"MUTATION"'),
        (r"''",        "'MUTATION'"),
    ]

    def constant_swap(self, source: str) -> List[Tuple[str, str]]:
        """Swap constants one at a time."""
        results = []
        lines = source.splitlines(keepends=True)

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            code_part, sep, comment = self._split_comment(line)
            masked = self._mask_strings(code_part)

            for pattern, replacement in self._CONSTANT_PAIRS:
                for m in re.finditer(pattern, masked):
                    new_line = (
                        code_part[:m.start()]
                        + replacement
                        + code_part[m.end():]
                        + (sep + comment if comment else '')
                    )
                    new_lines = lines[:line_idx] + [new_line] + lines[line_idx + 1:]
                    mutated = ''.join(new_lines)
                    desc = f"constant_swap: '{m.group()}' -> '{replacement}' on line {line_no}"
                    results.append((mutated, desc))

        return results

    # ------------------------------------------------------------------
    # 4. Boolean swap
    # ------------------------------------------------------------------

    def boolean_swap(self, source: str) -> List[Tuple[str, str]]:
        """Swap 'and' <-> 'or' one at a time."""
        results = []
        lines = source.splitlines(keepends=True)

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            code_part, sep, comment = self._split_comment(line)
            masked = self._mask_strings(code_part)

            for op, replacement in [('and', 'or'), ('or', 'and')]:
                pattern = r'\b' + op + r'\b'
                for m in re.finditer(pattern, masked):
                    new_line = (
                        code_part[:m.start()]
                        + replacement
                        + code_part[m.end():]
                        + (sep + comment if comment else '')
                    )
                    new_lines = lines[:line_idx] + [new_line] + lines[line_idx + 1:]
                    mutated = ''.join(new_lines)
                    desc = f"boolean_swap: '{op}' -> '{replacement}' on line {line_no}"
                    results.append((mutated, desc))

        return results

    # ------------------------------------------------------------------
    # 5. Return swap
    # ------------------------------------------------------------------

    def return_swap(self, source: str) -> List[Tuple[str, str]]:
        """Swap 'return True' <-> 'return False' one at a time."""
        results = []
        lines = source.splitlines(keepends=True)

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            code_part, sep, comment = self._split_comment(line)
            masked = self._mask_strings(code_part)

            for pattern, replacement in [
                (r'\breturn\s+True\b',  'return False'),
                (r'\breturn\s+False\b', 'return True'),
                (r'\breturn\s+0\b',     'return 1'),
                (r'\breturn\s+1\b',     'return 0'),
                (r'\breturn\s+None\b',  'return False'),
            ]:
                for m in re.finditer(pattern, masked):
                    new_line = (
                        code_part[:m.start()]
                        + replacement
                        + code_part[m.end():]
                        + (sep + comment if comment else '')
                    )
                    new_lines = lines[:line_idx] + [new_line] + lines[line_idx + 1:]
                    mutated = ''.join(new_lines)
                    desc = f"return_swap: '{m.group().strip()}' -> '{replacement}' on line {line_no}"
                    results.append((mutated, desc))

        return results

    # ------------------------------------------------------------------
    # 6. Condition negation
    # ------------------------------------------------------------------

    def condition_negation(self, source: str) -> List[Tuple[str, str]]:
        """
        Negate conditions in if/while/elif statements.
        'if x:' -> 'if not x:' and 'if not x:' -> 'if x:'
        """
        results = []
        lines = source.splitlines(keepends=True)

        for line_idx, line in enumerate(lines):
            line_no = line_idx + 1
            code_part, sep, comment = self._split_comment(line)
            stripped = code_part.strip()

            for keyword in ('if', 'elif', 'while'):
                kw_pattern = r'^(\s*)(' + keyword + r')\s+'
                m = re.match(kw_pattern, code_part)
                if m:
                    indent = m.group(1)
                    kw = m.group(2)
                    rest = code_part[m.end():]

                    # Check if already negated with 'not '
                    not_match = re.match(r'not\s+(.+)', rest)
                    if not_match:
                        # Remove the 'not '
                        inner = not_match.group(1)
                        new_line = (
                            indent + kw + ' ' + inner
                            + (sep + comment if comment else '')
                        )
                        desc = f"condition_negation: remove 'not' on line {line_no}"
                    else:
                        # Add 'not ('
                        # Remove trailing colon from rest if present
                        if rest.rstrip().endswith(':'):
                            cond = rest.rstrip()[:-1].rstrip()
                            new_line = (
                                indent + kw + ' not (' + cond + '):'
                                + (sep + comment if comment else '')
                            )
                        else:
                            new_line = (
                                indent + kw + ' not (' + rest.rstrip() + ')'
                                + (sep + comment if comment else '')
                            )
                        desc = f"condition_negation: add 'not' on line {line_no}"

                    new_lines = lines[:line_idx] + [new_line + '\n'] + lines[line_idx + 1:]
                    mutated = ''.join(new_lines)
                    results.append((mutated, desc))
                    break  # only match one keyword per line

        return results


# ---------------------------------------------------------------------------
# SourceMutator
# ---------------------------------------------------------------------------

class SourceMutator:
    """
    Wraps Mutator to apply mutations one at a time across a source file,
    returning a list of Mutant objects.
    """

    def __init__(self):
        self._mutator = Mutator()

    def generate_mutants(
        self,
        source: str,
        operators: Optional[List[MutationOperator]] = None,
    ) -> List[Mutant]:
        """
        Generate all possible mutants for the given source.

        Parameters
        ----------
        source : str
            The original Python source code.
        operators : list of MutationOperator, optional
            Which operators to apply. Defaults to all six.

        Returns
        -------
        list of Mutant
        """
        if operators is None:
            operators = list(MutationOperator)

        all_mutants: List[Mutant] = []

        op_method_map = {
            MutationOperator.ARITHMETIC_SWAP:   self._mutator.arithmetic_swap,
            MutationOperator.COMPARISON_SWAP:   self._mutator.comparison_swap,
            MutationOperator.CONSTANT_SWAP:     self._mutator.constant_swap,
            MutationOperator.BOOLEAN_SWAP:      self._mutator.boolean_swap,
            MutationOperator.RETURN_SWAP:       self._mutator.return_swap,
            MutationOperator.CONDITION_NEGATION: self._mutator.condition_negation,
        }

        for operator in operators:
            method = op_method_map[operator]
            try:
                pairs = method(source)
            except Exception as exc:
                # If the operator itself crashes, skip it
                continue

            for mutated_source, description in pairs:
                mutant = Mutant(
                    original_source=source,
                    mutated_source=mutated_source,
                    operator=operator,
                    description=description,
                )
                all_mutants.append(mutant)

        return all_mutants

    def generate_mutants_for_operator(
        self, source: str, operator: MutationOperator
    ) -> List[Mutant]:
        """Convenience: generate mutants for a single operator."""
        return self.generate_mutants(source, operators=[operator])


# ---------------------------------------------------------------------------
# MutationRunner
# ---------------------------------------------------------------------------

class MutationRunner:
    """
    Takes source code + a test function and runs all mutants through the
    test function, classifying each as KILLED / SURVIVED / ERROR / TIMEOUT.

    Parameters
    ----------
    test_function : callable
        A function that receives the mutated source (str) and returns True
        if the tests PASS (mutant SURVIVED) or raises/returns False if the
        tests FAIL (mutant KILLED).
    timeout : float
        Seconds to allow per mutant. Default 10.
    """

    def __init__(
        self,
        test_function: Callable[[str], bool],
        timeout: float = 10.0,
    ):
        self.test_function = test_function
        self.timeout = timeout
        self._source_mutator = SourceMutator()

    def run(
        self,
        source: str,
        operators: Optional[List[MutationOperator]] = None,
    ) -> 'MutationReport':
        """Run all mutants and return a MutationReport."""
        mutants = self._source_mutator.generate_mutants(source, operators)
        results: List[Mutant] = []

        for mutant in mutants:
            result = self._run_one(mutant)
            results.append(result)

        return MutationReport(source=source, mutants=results)

    def _run_one(self, mutant: Mutant) -> Mutant:
        """Run a single mutant and classify the result."""
        # First, try to compile the mutant
        try:
            compiled = compile(mutant.mutated_source, '<mutant>', 'exec')
        except SyntaxError as exc:
            mutant.result = MutationResult.ERROR
            mutant.error_message = f"SyntaxError: {exc}"
            return mutant

        # Run the test function in a thread with timeout
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Any] = {}

        def run_test():
            try:
                passed = self.test_function(mutant.mutated_source)
                result_holder['passed'] = bool(passed)
            except Exception as exc:
                error_holder['exc'] = exc
                error_holder['tb'] = traceback.format_exc()

        thread = threading.Thread(target=run_test, daemon=True)
        thread.start()
        thread.join(self.timeout)

        if thread.is_alive():
            mutant.result = MutationResult.TIMEOUT
            return mutant

        if error_holder:
            # Test function raised → the mutant was caught → KILLED
            mutant.result = MutationResult.KILLED
            mutant.error_message = str(error_holder.get('exc', ''))
            return mutant

        if 'passed' not in result_holder:
            mutant.result = MutationResult.ERROR
            mutant.error_message = "Test function returned no result"
            return mutant

        if result_holder['passed']:
            # Tests passed with the mutant → mutant SURVIVED
            mutant.result = MutationResult.SURVIVED
        else:
            # Tests failed with the mutant → mutant was KILLED
            mutant.result = MutationResult.KILLED

        return mutant

    @staticmethod
    def exec_in_sandbox(source: str) -> Dict[str, Any]:
        """
        Execute source in a fresh sandbox namespace.
        Returns the namespace dict after execution.
        Raises on any exception.
        """
        namespace: Dict[str, Any] = {}
        compiled = compile(source, '<mutant>', 'exec')
        exec(compiled, namespace)
        return namespace


# ---------------------------------------------------------------------------
# MutationReport
# ---------------------------------------------------------------------------

@dataclass
class MutationReport:
    """
    Aggregated results of a mutation testing run.

    Attributes
    ----------
    source : str
        Original source code.
    mutants : list of Mutant
        All mutants with their results filled in.
    """
    source: str
    mutants: List[Mutant] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.mutants)

    @property
    def killed(self) -> int:
        return sum(1 for m in self.mutants if m.result == MutationResult.KILLED)

    @property
    def survived(self) -> int:
        return sum(1 for m in self.mutants if m.result == MutationResult.SURVIVED)

    @property
    def errors(self) -> int:
        return sum(1 for m in self.mutants if m.result == MutationResult.ERROR)

    @property
    def timeouts(self) -> int:
        return sum(1 for m in self.mutants if m.result == MutationResult.TIMEOUT)

    @property
    def mutation_score(self) -> float:
        """Fraction of non-error/timeout mutants that were killed. 0.0–1.0."""
        eligible = self.total - self.errors - self.timeouts
        if eligible == 0:
            return 0.0
        return self.killed / eligible

    @property
    def survived_mutants(self) -> List[Mutant]:
        return [m for m in self.mutants if m.result == MutationResult.SURVIVED]

    @property
    def killed_mutants(self) -> List[Mutant]:
        return [m for m in self.mutants if m.result == MutationResult.KILLED]

    @property
    def error_mutants(self) -> List[Mutant]:
        return [m for m in self.mutants if m.result == MutationResult.ERROR]

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (JSON-friendly)."""
        return {
            'total': self.total,
            'killed': self.killed,
            'survived': self.survived,
            'errors': self.errors,
            'timeouts': self.timeouts,
            'mutation_score': self.mutation_score,
            'survived_mutants': [
                {
                    'mutant_id': m.mutant_id,
                    'operator': m.operator.value,
                    'description': m.description,
                    'location': m.location,
                }
                for m in self.survived_mutants
            ],
            'killed_mutants': [
                {
                    'mutant_id': m.mutant_id,
                    'operator': m.operator.value,
                    'description': m.description,
                }
                for m in self.killed_mutants
            ],
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        score_pct = self.mutation_score * 100
        return (
            f"Mutation score: {score_pct:.1f}% "
            f"({self.killed} killed / {self.survived} survived / "
            f"{self.errors} errors / {self.timeouts} timeouts, "
            f"{self.total} total)"
        )


# ---------------------------------------------------------------------------
# MockMutationHandler  — HTTP server
# ---------------------------------------------------------------------------

class MockMutationHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP handler that exposes mutation test results via REST.

    Routes
    ------
    GET  /health            → {"status": "ok"}
    POST /run               → body: {"source": "...", "test": "<not used>"}
                              Runs a built-in trivial test and returns report.
    GET  /report            → Returns last cached report as JSON.
    GET  /operators         → Returns list of available operators.
    POST /mutants           → body: {"source": "..."}
                              Returns list of generated mutant descriptions.
    """

    # Class-level storage shared across requests
    _last_report: Optional[Dict[str, Any]] = None
    _lock = threading.Lock()

    def log_message(self, fmt, *args):
        """Suppress default logging."""
        pass

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length else b''

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/health':
            self._send_json({'status': 'ok'})

        elif path == '/report':
            with MockMutationHandler._lock:
                report = MockMutationHandler._last_report
            if report is None:
                self._send_json({'error': 'No report available yet'}, 404)
            else:
                self._send_json(report)

        elif path == '/operators':
            self._send_json({
                'operators': [op.value for op in MutationOperator]
            })

        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/run':
            body = self._read_body()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._send_json({'error': 'Invalid JSON'}, 400)
                return

            source = data.get('source', '')
            if not source:
                self._send_json({'error': "'source' field required"}, 400)
                return

            # Run with a trivial test: just check compilation
            def trivial_test(mutated_source: str) -> bool:
                try:
                    compile(mutated_source, '<test>', 'exec')
                    return True  # survived: syntax OK
                except SyntaxError:
                    return False  # killed: syntax broke

            try:
                runner = MutationRunner(trivial_test, timeout=5.0)
                report = runner.run(source)
                report_dict = report.to_dict()
                with MockMutationHandler._lock:
                    MockMutationHandler._last_report = report_dict
                self._send_json(report_dict)
            except Exception as exc:
                self._send_json({'error': str(exc)}, 500)

        elif path == '/mutants':
            body = self._read_body()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._send_json({'error': 'Invalid JSON'}, 400)
                return

            source = data.get('source', '')
            if not source:
                self._send_json({'error': "'source' field required"}, 400)
                return

            sm = SourceMutator()
            operators = None
            if 'operators' in data:
                try:
                    operators = [MutationOperator(op) for op in data['operators']]
                except ValueError as exc:
                    self._send_json({'error': str(exc)}, 400)
                    return

            mutants = sm.generate_mutants(source, operators)
            self._send_json({
                'count': len(mutants),
                'mutants': [
                    {
                        'mutant_id': m.mutant_id,
                        'operator': m.operator.value,
                        'description': m.description,
                    }
                    for m in mutants
                ]
            })

        else:
            self._send_json({'error': 'Not found'}, 404)


class MutationHTTPServer:
    """
    Wrapper around HTTPServer that starts on a dynamic or specified port.

    Usage
    -----
    server = MutationHTTPServer(port=18980)
    server.start()          # non-blocking
    ...
    server.stop()
    """

    DEFAULT_PORT = 18980

    def __init__(self, host: str = '127.0.0.1', port: int = 0):
        self.host = host
        self.port = port  # 0 = OS picks a free port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def actual_port(self) -> int:
        if self._server is None:
            return self.port
        return self._server.server_address[1]

    def start(self):
        """Start the HTTP server in a background daemon thread."""
        self._server = HTTPServer((self.host, self.port), MockMutationHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def url(self, path: str = '') -> str:
        return f"http://{self.host}:{self.actual_port}{path}"


# ---------------------------------------------------------------------------
# Convenience helpers for test functions
# ---------------------------------------------------------------------------

def make_exec_test(assertions: str) -> Callable[[str], bool]:
    """
    Factory: returns a test function that executes the mutated source and
    then runs ``assertions`` (a string of Python code) in the same namespace.

    The assertions code should raise AssertionError on failure.
    Returns True if assertions pass, False / raises if they fail.

    Example
    -------
    source = "def add(a, b): return a + b"
    test_fn = make_exec_test("assert add(1, 2) == 3")
    runner = MutationRunner(test_fn)
    report = runner.run(source)
    """
    def test_function(mutated_source: str) -> bool:
        namespace: Dict[str, Any] = {}
        try:
            compiled = compile(mutated_source, '<mutant>', 'exec')
            exec(compiled, namespace)
            # Now run assertions
            exec(compile(assertions, '<assertions>', 'exec'), namespace)
            return True  # assertions passed → mutant SURVIVED
        except AssertionError:
            return False  # assertions failed → mutant KILLED
        except Exception:
            raise  # propagate other errors → KILLED

    return test_function


def sandbox_exec(source: str) -> Dict[str, Any]:
    """Execute source in a fresh sandbox namespace and return the namespace."""
    namespace: Dict[str, Any] = {}
    exec(compile(source, '<mutant>', 'exec'), namespace)
    return namespace


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _run_self_test(verbose: bool = False) -> int:
    """Mutate a tiny function and confirm a thorough test kills its mutants."""
    src = "def classify(n):\n    if n > 0:\n        return n + 1\n    return n - 1\n"
    test = make_exec_test(
        "assert classify(5) == 6\n"
        "assert classify(-3) == -4\n"
        "assert classify(0) == -1"
    )
    d = MutationRunner(test).run(src).to_dict()
    checks = [
        ("generated multiple mutants", d["total"] >= 2, f"total={d['total']}"),
        ("killed at least one mutant", d["killed"] >= 1, f"killed={d['killed']}"),
        ("mutation score in (0, 1]", 0 < d["mutation_score"] <= 1, f"score={d['mutation_score']}"),
    ]
    failures = [n for n, ok, _ in checks if not ok]
    for n, ok, dt in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}  ({dt})")
    print(f"\n  {len(checks) - len(failures)}/{len(checks)} checks passed")
    return 0 if not failures else 1


def _cli():
    import argparse

    parser = argparse.ArgumentParser(
        description="Mutation Test Harness — inject mutants and check test coverage"
    )
    subparsers = parser.add_subparsers(dest='command')

    # server sub-command
    srv = subparsers.add_parser('server', help='Start the mock HTTP server')
    srv.add_argument('--port', type=int, default=MutationHTTPServer.DEFAULT_PORT)
    srv.add_argument('--host', default='127.0.0.1')

    # mutate sub-command
    mut = subparsers.add_parser('mutate', help='Generate mutants for a source file')
    mut.add_argument('source_file', help='Python source file to mutate')
    mut.add_argument(
        '--operators', nargs='+',
        choices=[op.value for op in MutationOperator],
        help='Mutation operators to apply (default: all)'
    )

    parser.add_argument('--self-test', action='store_true',
                        help='Run built-in scenarios and exit')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_run_self_test(verbose=args.verbose))

    if args.command == 'server':
        server = MutationHTTPServer(host=args.host, port=args.port)
        server.start()
        print(f"Mutation HTTP server running on {server.url()}")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            server.stop()

    elif args.command == 'mutate':
        with open(args.source_file) as fh:
            source = fh.read()

        operators = None
        if args.operators:
            operators = [MutationOperator(op) for op in args.operators]

        sm = SourceMutator()
        mutants = sm.generate_mutants(source, operators)
        print(f"Generated {len(mutants)} mutants:")
        for i, m in enumerate(mutants, 1):
            print(f"  {i:4d}. [{m.operator.value}] {m.description}")

    else:
        parser.print_help()


if __name__ == '__main__':
    _cli()
