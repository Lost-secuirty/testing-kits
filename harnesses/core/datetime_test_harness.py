"""
DateTime Test Harness (Harness 20 of 36)
Pure stdlib, zero external dependencies.
Mock HTTP server on dynamic port (default 19060).
"""

import calendar
import datetime
import http.server
import json
import threading
import time
from datetime import timedelta, timezone

# Try to import zoneinfo (Python 3.9+)
try:
    import zoneinfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False


# ---------------------------------------------------------------------------
# Clock: injectable clock with freeze/advance/reset
# ---------------------------------------------------------------------------

class Clock:
    """Injectable clock supporting freeze, advance, and reset."""

    def __init__(self):
        self._frozen_time = None
        self._offset = timedelta(0)

    def now(self) -> datetime.datetime:
        """Return current time (frozen or real, with offset applied)."""
        if self._frozen_time is not None:
            return self._frozen_time + self._offset
        return datetime.datetime.now(tz=timezone.utc) + self._offset

    def freeze(self, dt: datetime.datetime):
        """Freeze the clock at a specific datetime."""
        self._frozen_time = dt
        self._offset = timedelta(0)

    def advance(self, seconds: float):
        """Advance the clock by given seconds (works in both frozen and live mode)."""
        self._offset += timedelta(seconds=seconds)

    def reset(self):
        """Reset clock to live (unfrozen) with no offset."""
        self._frozen_time = None
        self._offset = timedelta(0)


# ---------------------------------------------------------------------------
# TimezoneTester
# ---------------------------------------------------------------------------

class TimezoneTester:
    """Tests timezone offset conversion and naive-vs-aware detection."""

    # UTC-5 (EST)
    EST = timezone(timedelta(hours=-5), name="EST")
    # UTC+9 (JST)
    JST = timezone(timedelta(hours=9), name="JST")

    def utc_to_est(self, dt_utc: datetime.datetime) -> datetime.datetime:
        """Convert a UTC-aware datetime to EST."""
        if dt_utc.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return dt_utc.astimezone(self.EST)

    def utc_to_jst(self, dt_utc: datetime.datetime) -> datetime.datetime:
        """Convert a UTC-aware datetime to JST."""
        if dt_utc.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return dt_utc.astimezone(self.JST)

    def is_aware(self, dt: datetime.datetime) -> bool:
        """Return True if dt is timezone-aware."""
        return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None

    def is_naive(self, dt: datetime.datetime) -> bool:
        """Return True if dt is timezone-naive."""
        return not self.is_aware(dt)

    def compare_aware_naive(self, dt1: datetime.datetime, dt2: datetime.datetime):
        """
        Attempt to compare an aware and a naive datetime.
        Raises TypeError (as Python does natively).
        """
        # Python raises TypeError natively when comparing aware and naive
        return dt1 < dt2


# ---------------------------------------------------------------------------
# DSTTester
# ---------------------------------------------------------------------------

class DSTTester:
    """Tests DST spring-forward gap and fall-back fold."""

    def get_spring_forward_gap_dt(self):
        """
        Return a datetime that falls in the spring-forward gap.
        2024-03-10 02:30 America/New_York doesn't exist (clocks jump 2→3 AM).
        Using fixed offsets: before gap UTC-5, after gap UTC-4.
        Returns the 'fold=0' version (pre-transition).
        """
        # In a fixed-offset world, we represent the "gap" conceptually.
        # 2024-03-10 02:30 ET — this time is in the gap.
        # With fold=0 it's treated as if it's EST (UTC-5) → UTC 07:30
        # With fold=1 it's treated as if it's EDT (UTC-4) → UTC 06:30
        # We return the naive local time that would be in the gap.
        return datetime.datetime(2024, 3, 10, 2, 30, 0)

    def is_in_spring_forward_gap(self, dt_naive: datetime.datetime) -> bool:
        """
        Check if a naive datetime falls in the spring-forward gap
        (2024-03-10 02:00–03:00 in US Eastern).
        """
        gap_start = datetime.datetime(2024, 3, 10, 2, 0, 0)
        gap_end = datetime.datetime(2024, 3, 10, 3, 0, 0)
        return gap_start <= dt_naive < gap_end

    def get_fall_back_fold_dt(self):
        """
        Return a datetime that falls in the fall-back fold.
        2024-11-03 01:30 America/New_York exists twice (clocks fall 2→1 AM).
        Returns (fold0_dt, fold1_dt) naive datetimes.
        """
        # fold=0: first occurrence (EDT, UTC-4)
        # fold=1: second occurrence (EST, UTC-5)
        dt = datetime.datetime(2024, 11, 3, 1, 30, 0)
        return dt, dt  # same wall time, different UTC interpretations

    def fold_to_utc(self, dt_naive: datetime.datetime, fold: int) -> datetime.datetime:
        """
        Convert a fall-back ambiguous time to UTC using fold.
        fold=0 → EDT (UTC-4), fold=1 → EST (UTC-5)
        """
        if fold == 0:
            offset = timedelta(hours=-4)  # EDT
        else:
            offset = timedelta(hours=-5)  # EST
        tz = timezone(offset)
        dt_aware = dt_naive.replace(tzinfo=tz)
        return dt_aware.astimezone(timezone.utc)

    def is_in_fall_back_fold(self, dt_naive: datetime.datetime) -> bool:
        """Check if naive datetime is in the 2024-11-03 fall-back fold window."""
        fold_start = datetime.datetime(2024, 11, 3, 1, 0, 0)
        fold_end = datetime.datetime(2024, 11, 3, 2, 0, 0)
        return fold_start <= dt_naive < fold_end


# ---------------------------------------------------------------------------
# LeapYearTester
# ---------------------------------------------------------------------------

class LeapYearTester:
    """Tests leap year logic including Feb 29 validity."""

    @staticmethod
    def is_leap_year(year: int) -> bool:
        """Return True if year is a leap year."""
        return calendar.isleap(year)

    @staticmethod
    def feb29_exists(year: int) -> bool:
        """Return True if Feb 29 exists in that year."""
        return calendar.isleap(year)

    @staticmethod
    def get_feb29(year: int) -> datetime.datetime:
        """Return Feb 29 datetime for the given year (raises ValueError if not leap)."""
        return datetime.datetime(year, 2, 29)

    @staticmethod
    def days_in_feb(year: int) -> int:
        """Return number of days in February for given year."""
        return 29 if calendar.isleap(year) else 28


# ---------------------------------------------------------------------------
# BoundaryTester
# ---------------------------------------------------------------------------

class BoundaryTester:
    """Tests epoch, pre-epoch, far future, and 2038 boundary datetimes."""

    # Unix epoch
    EPOCH = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # 2038 problem: 2^31 - 1 seconds after epoch
    Y2038_TIMESTAMP = 2**31 - 1  # 2147483647
    # Far future
    FAR_FUTURE = datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    # Far past (pre-epoch)
    PRE_EPOCH = datetime.datetime(1900, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    @staticmethod
    def timestamp_to_datetime(ts: float) -> datetime.datetime:
        """Convert a Unix timestamp to UTC datetime."""
        return datetime.datetime.fromtimestamp(ts, tz=timezone.utc)

    @staticmethod
    def datetime_to_timestamp(dt: datetime.datetime) -> float:
        """Convert a UTC datetime to Unix timestamp."""
        return dt.timestamp()

    @classmethod
    def get_epoch(cls) -> datetime.datetime:
        return cls.EPOCH

    @classmethod
    def get_y2038_dt(cls) -> datetime.datetime:
        """Return the Y2038 boundary datetime (2^31 - 1 seconds from epoch)."""
        return cls.timestamp_to_datetime(cls.Y2038_TIMESTAMP)

    @classmethod
    def get_pre_epoch_dt(cls) -> datetime.datetime:
        """Return a pre-epoch datetime."""
        return cls.PRE_EPOCH

    @classmethod
    def get_far_future(cls) -> datetime.datetime:
        return cls.FAR_FUTURE

    @staticmethod
    def pre_epoch_timestamp(dt: datetime.datetime) -> float:
        """Return negative timestamp for pre-epoch dates."""
        return dt.timestamp()


# ---------------------------------------------------------------------------
# ParseFormatTester
# ---------------------------------------------------------------------------

class ParseFormatTester:
    """Tests ISO 8601 parse/format roundtrips."""

    ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"
    ISO_FORMAT_TZ = "%Y-%m-%dT%H:%M:%S%z"

    @staticmethod
    def to_iso8601(dt: datetime.datetime) -> str:
        """Format datetime as ISO 8601 string."""
        return dt.isoformat()

    @staticmethod
    def from_iso8601(s: str) -> datetime.datetime:
        """Parse ISO 8601 string to datetime."""
        return datetime.datetime.fromisoformat(s)

    @staticmethod
    def strptime_iso(s: str) -> datetime.datetime:
        """Parse ISO 8601 naive string using strptime."""
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def strftime_iso(dt: datetime.datetime) -> str:
        """Format datetime as ISO 8601 naive string using strftime."""
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def roundtrip_iso(dt: datetime.datetime) -> datetime.datetime:
        """Roundtrip a datetime through ISO 8601 string format."""
        s = dt.isoformat()
        return datetime.datetime.fromisoformat(s)

    @staticmethod
    def roundtrip_strptime(dt: datetime.datetime) -> datetime.datetime:
        """Roundtrip a naive datetime through strptime/strftime."""
        s = dt.strftime("%Y-%m-%dT%H:%M:%S")
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def parse_rfc2822(s: str) -> datetime.datetime:
        """Parse an RFC 2822 style date string (e.g. from HTTP headers)."""
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s)

    @staticmethod
    def format_http_date(dt: datetime.datetime) -> str:
        """Format datetime as HTTP date (RFC 7231)."""
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        utc_dt = dt.astimezone(timezone.utc)
        return (f"{days[utc_dt.weekday()]}, {utc_dt.day:02d} "
                f"{months[utc_dt.month - 1]} {utc_dt.year} "
                f"{utc_dt.hour:02d}:{utc_dt.minute:02d}:{utc_dt.second:02d} GMT")


# ---------------------------------------------------------------------------
# DurationTester
# ---------------------------------------------------------------------------

class DurationTester:
    """Tests timedelta arithmetic and monotonic vs wall time."""

    @staticmethod
    def add_duration(dt: datetime.datetime, td: timedelta) -> datetime.datetime:
        return dt + td

    @staticmethod
    def subtract_duration(dt: datetime.datetime, td: timedelta) -> datetime.datetime:
        return dt - td

    @staticmethod
    def difference(dt1: datetime.datetime, dt2: datetime.datetime) -> timedelta:
        return dt2 - dt1

    @staticmethod
    def total_seconds(td: timedelta) -> float:
        return td.total_seconds()

    @staticmethod
    def monotonic_elapsed(func):
        """Measure elapsed time using monotonic clock."""
        start = time.monotonic()
        result = func()
        end = time.monotonic()
        return result, end - start

    @staticmethod
    def wall_elapsed(func):
        """Measure elapsed time using wall clock."""
        start = time.time()
        result = func()
        end = time.time()
        return result, end - start

    @staticmethod
    def timedelta_components(td: timedelta) -> dict:
        """Break timedelta into days, hours, minutes, seconds."""
        total_secs = int(td.total_seconds())
        sign = -1 if total_secs < 0 else 1
        total_secs = abs(total_secs)
        days, rem = divmod(total_secs, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        return {
            "sign": sign,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
            "total_seconds": td.total_seconds(),
        }

    @staticmethod
    def make_timedelta(days=0, hours=0, minutes=0, seconds=0) -> timedelta:
        return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


# ---------------------------------------------------------------------------
# MockDateTimeHandler: HTTP handler returning current time
# ---------------------------------------------------------------------------

class MockDateTimeHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler that returns current server time as JSON."""

    clock = None  # Can be injected

    def do_GET(self):
        if self.path == "/time":
            if self.clock is not None:
                now = self.clock.now()
            else:
                now = datetime.datetime.now(tz=timezone.utc)
            payload = {
                "iso": now.isoformat(),
                "timestamp": now.timestamp(),
                "year": now.year,
                "month": now.month,
                "day": now.day,
                "hour": now.hour,
                "minute": now.minute,
                "second": now.second,
                "timezone": str(now.tzinfo),
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging


# ---------------------------------------------------------------------------
# ServerTimeTester: starts/stops mock HTTP server and queries it
# ---------------------------------------------------------------------------

class ServerTimeTester:
    """Manages a mock HTTP server that serves current time."""

    DEFAULT_PORT = 19060

    def __init__(self, port: int = 0, clock: Clock = None):
        """
        port=0 means OS assigns a free port.
        """
        self.clock = clock
        self._port = port
        self._server = None
        self._thread = None
        self.actual_port = None

    def start(self):
        """Start the HTTP server in a background thread."""
        handler = MockDateTimeHandler
        handler.clock = self.clock

        self._server = http.server.HTTPServer(("127.0.0.1", self._port), handler)
        self.actual_port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_time(self) -> dict:
        """Query the server's /time endpoint and return parsed JSON."""
        import urllib.request
        url = f"http://127.0.0.1:{self.actual_port}/time"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
