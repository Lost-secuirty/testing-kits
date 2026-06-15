"""
Webhook Delivery / Verification Test Harness
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
            if self._status_sequence:
                status = self._status_sequence.pop(0)
            else:
                status = self._default_status

        return status, self._response_body
