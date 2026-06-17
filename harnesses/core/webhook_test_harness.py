"""
Webhook Delivery / Verification Test Harness
Pure stdlib, zero external dependencies.

Self-test (asserts the teeth — a frozen signature/replay corpus catches a
validator that skips the replay-window check or is off-by-one on the boundary):
  python harnesses/core/webhook_test_harness.py --self-test
  python harnesses/core/webhook_test_harness.py --json
  python harnesses/core/webhook_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.server
import json
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path as _Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Make the shared teeth contract importable whether run as a module or a script.
if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Clock abstraction
# ---------------------------------------------------------------------------

class Clock:
    """Real wall-clock implementation."""
    def now(self) -> float:
        return time.time()


class FakeClock:
    """Injectable fake clock for tests."""
    def __init__(self, start: float = 0.0):
        self._time = start

    def now(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds

    def set(self, t: float) -> None:
        self._time = t


# ---------------------------------------------------------------------------
# HMAC-SHA256 signature helpers
# ---------------------------------------------------------------------------

def sign(payload: bytes, secret: str) -> str:
    """Return hex-encoded HMAC-SHA256 signature."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    mac = hmac.new(key, payload, hashlib.sha256)
    return mac.hexdigest()


def verify(payload: bytes, secret: str, sig: str) -> bool:
    """Constant-time verify; return False for any malformed/wrong input."""
    if not sig or not isinstance(sig, str):
        return False
    try:
        expected = sign(payload, secret)
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WebhookEvent:
    event_id: str
    event_type: str
    payload: dict[str, Any]
    timestamp: float
    sequence_number: int | None = None

    def to_bytes(self) -> bytes:
        """Canonical serialisation for signing."""
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }
        if self.sequence_number is not None:
            data["sequence_number"] = self.sequence_number
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass
class DeliveryAttempt:
    attempt_number: int
    timestamp: float
    status_code: int | None
    success: bool
    error: str | None = None


class DeliveryResult(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    DEAD_LETTERED = "dead_lettered"


@dataclass
class DeliveryReport:
    """Aggregate statistics for a single event's delivery."""
    event_id: str
    result: DeliveryResult
    attempts: list[DeliveryAttempt] = field(default_factory=list)
    dead_letter_reason: str | None = None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def successful_attempt(self) -> DeliveryAttempt | None:
        for a in self.attempts:
            if a.success:
                return a
        return None


@dataclass
class WebhookReport:
    """Aggregate statistics across multiple delivery reports."""
    reports: list[DeliveryReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.reports)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.reports if r.result == DeliveryResult.SUCCESS)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.reports if r.result == DeliveryResult.FAILED)

    @property
    def exhausted_count(self) -> int:
        return sum(1 for r in self.reports if r.result == DeliveryResult.EXHAUSTED)

    @property
    def dead_lettered_count(self) -> int:
        return sum(1 for r in self.reports if r.result == DeliveryResult.DEAD_LETTERED)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.success_count / self.total


# ---------------------------------------------------------------------------
# Dead-letter queue
# ---------------------------------------------------------------------------

class DeadLetterQueue:
    def __init__(self):
        self._items: list[tuple[WebhookEvent, str]] = []
        self._lock = threading.Lock()

    def enqueue(self, event: WebhookEvent, reason: str) -> None:
        with self._lock:
            self._items.append((event, reason))

    def drain(self) -> list[tuple[WebhookEvent, str]]:
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def peek(self) -> list[tuple[WebhookEvent, str]]:
        with self._lock:
            return list(self._items)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)


# ---------------------------------------------------------------------------
# Signature validator (server-side, with timestamp tolerance + replay window)
# ---------------------------------------------------------------------------

class SignatureValidator:
    """Validates inbound webhook signatures with replay protection."""

    def __init__(
        self,
        secret: str,
        tolerance_seconds: float = 300.0,
        clock: Any | None = None,
    ):
        self.secret = secret
        self.tolerance_seconds = tolerance_seconds
        self._clock = clock or Clock()
        self._seen_ids: dict[str, float] = {}   # event_id -> first-seen time
        self._lock = threading.Lock()

    def validate(
        self,
        payload: bytes,
        sig: str,
        event_id: str,
        event_timestamp: float,
    ) -> tuple[bool, str]:
        """
        Returns (ok, reason).
        Checks: signature correct, timestamp within tolerance, not replayed.
        """
        now = self._clock.now()

        # 1. Signature check
        if not verify(payload, self.secret, sig):
            return False, "invalid_signature"

        # 2. Timestamp tolerance
        age = now - event_timestamp
        if abs(age) > self.tolerance_seconds:
            return False, "timestamp_out_of_tolerance"

        # 3. Replay check
        with self._lock:
            if event_id in self._seen_ids:
                return False, "replay_detected"
            self._seen_ids[event_id] = now

        return True, "ok"

    def reset(self) -> None:
        with self._lock:
            self._seen_ids.clear()


# ---------------------------------------------------------------------------
# Retry / backoff sender
# ---------------------------------------------------------------------------

class RetryConfig:
    def __init__(
        self,
        max_attempts: int = 4,
        base_backoff: float = 2.0,
        max_backoff: float = 60.0,
        timeout: float = 5.0,
        retryable_status_codes: list[int] | None = None,
    ):
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.timeout = timeout
        self.retryable_status_codes = retryable_status_codes or list(range(500, 600))

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff: base * 2^(attempt-1), capped at max_backoff."""
        delay = self.base_backoff * (2 ** (attempt - 1))
        return min(delay, self.max_backoff)


class WebhookSender:
    """
    Sends webhook events with retry + exponential backoff.
    Supports exactly-once side effects via deduplication.
    Dead-letters events after exhausting retries.
    """

    def __init__(
        self,
        target_url: str,
        secret: str,
        retry_config: RetryConfig | None = None,
        dlq: DeadLetterQueue | None = None,
        clock: Any | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.target_url = target_url
        self.secret = secret
        self.retry_config = retry_config or RetryConfig()
        self.dlq = dlq or DeadLetterQueue()
        self._clock = clock or Clock()
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._seen: dict[str, DeliveryReport] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, event: WebhookEvent) -> DeliveryReport:
        """
        Send event with retry/backoff. Returns cached report on duplicate
        event_id (exactly-once side effects).
        """
        with self._lock:
            if event.event_id in self._seen:
                return self._seen[event.event_id]

        report = self._attempt_delivery(event)

        with self._lock:
            self._seen[event.event_id] = report

        if report.result in (DeliveryResult.EXHAUSTED, DeliveryResult.DEAD_LETTERED, DeliveryResult.FAILED):
            self.dlq.enqueue(event, report.dead_letter_reason or "exhausted_retries")

        return report

    def reset_dedup(self) -> None:
        with self._lock:
            self._seen.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _attempt_delivery(self, event: WebhookEvent) -> DeliveryReport:
        payload_bytes = event.to_bytes()
        sig = sign(payload_bytes, self.secret)
        attempts: list[DeliveryAttempt] = []
        cfg = self.retry_config

        for attempt_num in range(1, cfg.max_attempts + 1):
            ts = self._clock.now()
            status_code, error = self._http_post(payload_bytes, sig, event)

            success = status_code is not None and 200 <= status_code < 300
            attempt = DeliveryAttempt(
                attempt_number=attempt_num,
                timestamp=ts,
                status_code=status_code,
                success=success,
                error=error,
            )
            attempts.append(attempt)

            if success:
                return DeliveryReport(
                    event_id=event.event_id,
                    result=DeliveryResult.SUCCESS,
                    attempts=attempts,
                )

            # Decide whether to retry
            retryable = (
                status_code is None  # network error / timeout
                or status_code in cfg.retryable_status_codes
            )
            if not retryable:
                report = DeliveryReport(
                    event_id=event.event_id,
                    result=DeliveryResult.FAILED,
                    attempts=attempts,
                    dead_letter_reason=f"non_retryable_status_{status_code}",
                )
                return report

            if attempt_num < cfg.max_attempts:
                delay = cfg.backoff_for(attempt_num)
                self._sleep(delay)

        return DeliveryReport(
            event_id=event.event_id,
            result=DeliveryResult.EXHAUSTED,
            attempts=attempts,
            dead_letter_reason="exhausted_retries",
        )

    def _http_post(
        self, payload_bytes: bytes, sig: str, event: WebhookEvent
    ) -> tuple[int | None, str | None]:
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": sig,
            "X-Webhook-Event-ID": event.event_id,
            "X-Webhook-Event-Type": event.event_type,
            "X-Webhook-Timestamp": str(event.timestamp),
        }
        req = Request(self.target_url, data=payload_bytes, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.retry_config.timeout) as resp:
                return resp.status, None
        except HTTPError as e:
            return e.code, str(e)
        except URLError as e:
            return None, str(e)
        except Exception as e:
            return None, str(e)


# ---------------------------------------------------------------------------
# Sequence-number ordering tracker
# ---------------------------------------------------------------------------

class SequenceTracker:
    """Detects out-of-order and gapped event delivery."""

    def __init__(self):
        self._expected: int | None = None
        self._gaps: list[tuple[int, int]] = []      # (expected, received)
        self._out_of_order: list[tuple[int, int]] = []  # (expected, received)
        self._lock = threading.Lock()

    def record(self, seq: int) -> str:
        """Record a received sequence number. Returns 'ok', 'gap', 'out_of_order'."""
        with self._lock:
            if self._expected is None:
                self._expected = seq + 1
                return "ok"

            if seq == self._expected:
                self._expected += 1
                return "ok"
            elif seq > self._expected:
                self._gaps.append((self._expected, seq))
                self._expected = seq + 1
                return "gap"
            else:
                self._out_of_order.append((self._expected, seq))
                return "out_of_order"

    @property
    def gaps(self) -> list[tuple[int, int]]:
        with self._lock:
            return list(self._gaps)

    @property
    def out_of_order_events(self) -> list[tuple[int, int]]:
        with self._lock:
            return list(self._out_of_order)

    def reset(self) -> None:
        with self._lock:
            self._expected = None
            self._gaps.clear()
            self._out_of_order.clear()


# ---------------------------------------------------------------------------
# Mock webhook HTTP receiver
# ---------------------------------------------------------------------------

class _WebhookRequestHandler(http.server.BaseHTTPRequestHandler):
    """Internal request handler for MockWebhookHandler."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        sig = self.headers.get("X-Webhook-Signature", "")
        event_id = self.headers.get("X-Webhook-Event-ID", "")
        event_type = self.headers.get("X-Webhook-Event-Type", "")

        # Let the server decide how to respond
        status, response_body = self.server.handler_logic(
            path=self.path,
            body=body,
            sig=sig,
            event_id=event_id,
            event_type=event_type,
            headers=dict(self.headers),
        )

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, fmt, *args):  # silence default logging
        pass


class MockWebhookHandler:
    """
    A mock HTTP server that acts as a webhook receiver.
    Supports configurable response codes, request recording, and failure injection.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.received_requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

        # Response configuration
        self._status_sequence: list[int] = []   # pops from front; last repeats
        self._default_status: int = 200
        self._response_body: bytes = b'{"ok":true}'

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        server = http.server.HTTPServer((self.host, self.port), _WebhookRequestHandler)
        # Use a lambda so that replacing self._handle later is still respected.
        server.handler_logic = lambda **kw: self._handle(**kw)
        self._server = server
        self.port = server.server_address[1]  # actual ephemeral port
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            server = self._server
            server.shutdown()
            server.server_close()
            if self._thread:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def set_response(self, status: int, body: bytes = b'{"ok":true}') -> None:
        self._default_status = status
        self._response_body = body

    def set_status_sequence(self, statuses: list[int]) -> None:
        """Respond with each status in sequence; repeat last when exhausted."""
        with self._lock:
            self._status_sequence = list(statuses)

    def clear_requests(self) -> None:
        with self._lock:
            self.received_requests.clear()

    def request_count(self) -> int:
        with self._lock:
            return len(self.received_requests)

    # ------------------------------------------------------------------
    # Internal handler
    # ------------------------------------------------------------------

    def _handle(
        self,
        path: str,
        body: bytes,
        sig: str,
        event_id: str,
        event_type: str,
        headers: dict[str, str],
    ) -> tuple[int, bytes]:
        with self._lock:
            self.received_requests.append({
                "path": path,
                "body": body,
                "sig": sig,
                "event_id": event_id,
                "event_type": event_type,
                "headers": headers,
            })
            status = self._status_sequence.pop(0) if self._status_sequence else self._default_status

        return status, self._response_body


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of inbound webhooks (payload, signature, event_id,
# event_timestamp) judged against a fixed FakeClock ``now`` and replay window.
#
# A webhook validator only has teeth if it CATCHES a server that (a) forwards a
# forged/tampered payload, (b) accepts a stale (replayed) timestamp because it
# skipped the replay-window check, or (c) is off-by-one on the tolerance
# boundary. The contract a correct validator must hold, at now=1000.0 with a
# tolerance of 300.0s and the GENERIC test key ``webhook-test-key``:
#
#   * the HMAC-SHA256 over the payload must match the supplied signature, else
#     reject as ``invalid_signature``;
#   * abs(now - event_timestamp) must be <= tolerance, else reject as
#     ``timestamp_out_of_tolerance`` (a timestamp at EXACTLY the boundary is
#     still inside the window — the ``> tolerance`` vs ``>= tolerance`` seam);
#   * otherwise accept as ``ok``.
#
# An impl is a callable ``validate(payload, sig, event_id, event_timestamp)
# -> (ok, reason)`` evaluated at the frozen now/tolerance/secret baked into the
# corpus. prove() judges each impl against the corpus's FROZEN LITERAL verdicts
# (each (ok, reason) hand-set from the contract above and confirmed once with
# the harness's own signing routine — NEVER read back from the oracle at
# runtime), so the check is non-circular: flipping any one frozen ``expected_*``
# literal would make prove(oracle) return True.
#
# Pure + deterministic: each case spins a fresh in-process SignatureValidator on
# an injected FakeClock pinned to ``now`` (no real clock/sleep), HMAC math only,
# no RNG/threads/network/filesystem (the mock HTTP receiver above is used only
# under ``main`` and the test suite, never here). The two planted mutants model
# genuine real-world validator defects (per the campaign hint):
#
#   * skips_replay_check — drops the timestamp-tolerance branch entirely, so a
#     stale/replayed timestamp far outside the window is wrongly accepted (the
#     classic "we verified the signature but forgot the freshness window" hole);
#   * off_by_one_tolerance — uses ``>= tolerance`` instead of ``> tolerance`` on
#     the window, so a timestamp sitting EXACTLY on the boundary is wrongly
#     rejected (a fencepost defect that silently drops legitimate events).
# ---------------------------------------------------------------------------

# Frozen evaluation context for the teeth corpus. A generic, non-provider test
# key (NOT a real whsec_/sk_-prefixed secret) — see the SECRET GUARD: every
# signature below is an HMAC over this short key, not a leaked credential.
_TEETH_SECRET = "webhook-test-key"  # allowlist secret: non-provider HMAC test key, not a credential
_TEETH_NOW = 1000.0
_TEETH_TOLERANCE = 300.0


@dataclass(frozen=True)
class SigCase:
    """One frozen inbound-webhook case with a literal, hand-set verdict.

    The signature hexes are HMAC-SHA256(payload, _TEETH_SECRET) constants; the
    (expected_ok, expected_reason) verdict is hand-derived from the validator
    contract at now=1000.0, tolerance=300.0 — never read from the oracle.
    """

    name: str
    payload: bytes
    sig: str
    event_id: str
    event_timestamp: float
    expected_ok: bool
    expected_reason: str
    note: str = ""


# Cases chosen so the correct oracle reproduces every literal verdict AND each
# planted mutant gets at least two of them wrong (no single load-bearing
# fixture). All four timestamp branches (in-window, exact past boundary, exact
# future boundary, far-stale both directions) plus a forged-signature case are
# present. ``valid`` / ``future_within`` are decoys the boundary/replay bugs
# cannot distinguish, so the teeth come from the real seams, not coincidence.
SIG_CORPUS: tuple[SigCase, ...] = (
    # Fresh, correctly-signed event (age 0): every impl accepts.
    SigCase("valid", b'{"id":"evt_001","v":1}',
            "0ed5a1e02040c5be075ec1db2c15ebe6a548a8ac8c3dc3cc891f44f03465ec3f",
            "evt_001", 1000.0, True, "ok",
            "fresh + correct sig: accepted by every impl (decoy for the bugs)"),
    # Forged signature (all-zero hex over the same payload): reject as bad sig.
    SigCase("bad_signature", b'{"id":"evt_001","v":1}',
            "0" * 64,
            "evt_bad", 1000.0, False, "invalid_signature",
            "tampered/forged signature must be rejected"),
    # Stale past timestamp (age +400 > 300): outside the window -> reject.
    # skips_replay_check wrongly accepts this.
    SigCase("stale_past", b'{"id":"evt_003","v":3}',
            "73b7142f8caca25082bcc386867a0691d8b3c1edc8021e34fe599315528daa7d",
            "evt_003", 600.0, False, "timestamp_out_of_tolerance",
            "replayed/stale past event: skips_replay_check wrongly accepts"),
    # Far-future timestamp (age -400, abs 400 > 300): also outside -> reject.
    # A SECOND replay-window case so skips_replay_check is caught >=2 ways.
    SigCase("stale_future", b'{"id":"evt_002","v":2}',
            "eeb72d7083ff6cc0cfd3790e5250503b99882445800b855ccf060fa4c9d102a1",
            "evt_002", 1400.0, False, "timestamp_out_of_tolerance",
            "far-future stale event: second replay-window catch"),
    # Past timestamp EXACTLY on the boundary (age +300 == tolerance): inside the
    # window -> accept. off_by_one_tolerance (>=) wrongly rejects this.
    SigCase("boundary_past", b'{"id":"evt_005","v":5}',
            "c7d8277ccf7b3e21b5bd8583950952efe325e0d0fe909f4a0d8fae77c16c735f",
            "evt_005", 700.0, True, "ok",
            "exact past boundary (age==tolerance): off_by_one wrongly rejects"),
    # Future timestamp EXACTLY on the boundary (abs age 300 == tolerance):
    # inside -> accept. A SECOND boundary case so off_by_one is caught >=2 ways.
    SigCase("boundary_future", b'{"id":"evt_006","v":6}',
            "0c3d8abdddf4e8b839f19c63f0574b21600a19ea2fcb641b5d9d62630acb0041",
            "evt_006", 1300.0, True, "ok",
            "exact future boundary: second off_by_one catch"),
    # Future timestamp well inside the window (abs age 200 < 300): accept. Decoy
    # exercising abs() that none of the planted bugs can distinguish.
    SigCase("future_within", b'{"id":"evt_004","v":4}',
            "77ce62704703526140670af1981af42fb4b6ab1f59158bcbc2d09a812890c0a4",
            "evt_004", 1200.0, True, "ok",
            "future but in-window decoy: exercises abs(age)"),
)


# --- ORACLE: reuse the harness's own correct SignatureValidator.validate -----

def oracle_validate(
    payload: bytes, sig: str, event_id: str, event_timestamp: float,
) -> tuple[bool, str]:
    """Correct inbound validation, delegating to the harness's own
    ``SignatureValidator.validate`` on a FakeClock pinned to ``_TEETH_NOW``.

    A fresh validator per call keeps replay state isolated so each corpus case
    is judged independently (the corpus does not probe replay-dedup, which the
    existing TestSignatureValidator suite already covers).
    """
    validator = SignatureValidator(
        _TEETH_SECRET,
        tolerance_seconds=_TEETH_TOLERANCE,
        clock=FakeClock(start=_TEETH_NOW),
    )
    return validator.validate(payload, sig, event_id, event_timestamp)


# --- Planted buggy twins (each models a real validator defect) ---------------

def skips_replay_check(
    payload: bytes, sig: str, event_id: str, event_timestamp: float,
) -> tuple[bool, str]:
    """BUG: verifies the signature but DROPS the timestamp-tolerance branch, so a
    stale/replayed timestamp far outside the window is wrongly accepted.

    Models the "we checked the HMAC but forgot the freshness/replay window"
    vulnerability: a captured-and-resent webhook validates forever.
    """
    if not verify(payload, _TEETH_SECRET, sig):
        return False, "invalid_signature"
    # BUG: no abs(now - event_timestamp) > tolerance check.
    return True, "ok"


def off_by_one_tolerance(
    payload: bytes, sig: str, event_id: str, event_timestamp: float,
) -> tuple[bool, str]:
    """BUG: rejects with ``>=`` instead of ``>`` on the tolerance window, so a
    timestamp sitting EXACTLY on the boundary is wrongly rejected.

    Models a fencepost defect on the freshness window that silently drops
    legitimate events arriving right at the edge of the allowed skew.
    """
    if not verify(payload, _TEETH_SECRET, sig):
        return False, "invalid_signature"
    age = _TEETH_NOW - event_timestamp
    if abs(age) >= _TEETH_TOLERANCE:  # BUG: >= excludes the boundary itself
        return False, "timestamp_out_of_tolerance"
    return True, "ok"


def prove(impl: Callable[[bytes, str, str, float], tuple[bool, str]]) -> bool:
    """True iff ``impl`` returns the WRONG verdict for any frozen corpus case
    (i.e. the validator bug is caught): the ``(ok, reason)`` pair diverges from
    the hand-set literal, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    SIG_CORPUS, never read from the oracle; HMAC arithmetic on a fixed FakeClock
    only, no RNG/real-clock/threads/network/filesystem. An impl that raises on a
    corpus case counts as caught.
    """
    for case in SIG_CORPUS:
        try:
            ok, reason = impl(case.payload, case.sig, case.event_id, case.event_timestamp)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if bool(ok) != case.expected_ok or reason != case.expected_reason:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_validate,
    mutants=(
        Mutant("skips_replay_check", skips_replay_check,
               "drops the timestamp-tolerance branch -> a stale/replayed "
               "timestamp far outside the window is wrongly accepted"),
        Mutant("off_by_one_tolerance", off_by_one_tolerance,
               "uses >= instead of > on the tolerance window -> a timestamp "
               "exactly on the boundary is wrongly rejected"),
    ),
    corpus_size=len(SIG_CORPUS),
    kind="oracle_swap",
    notes="a correct validator accepts iff HMAC matches AND "
          "abs(now - event_timestamp) <= tolerance (boundary inclusive)",
)


def list_scenarios() -> list[str]:
    """Names of the frozen signature/replay corpus cases (the teeth scenarios)."""
    return [c.name for c in SIG_CORPUS]


# ---------------------------------------------------------------------------
# Self-test — fails loud, reports findings.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Assert the teeth: the correct oracle reproduces every frozen verdict, and
    the universal swap-check passes (oracle clean, every planted validator
    mutant caught against the frozen signature/replay corpus)."""
    report = Report("core/webhook")

    # 1. The correct oracle reproduces every frozen (ok, reason) literal.
    for case in SIG_CORPUS:
        ok, reason = oracle_validate(
            case.payload, case.sig, case.event_id, case.event_timestamp,
        )
        report.add(f"oracle_verdict:{case.name}",
                   [case.expected_ok, case.expected_reason],
                   [ok, reason], detail=case.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Webhook signature/replay validation harness")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
