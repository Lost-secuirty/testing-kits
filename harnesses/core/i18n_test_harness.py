"""
i18n / Unicode / Encoding Test Harness (Harness 30 of 36)

Tests text-handling correctness that AI code routinely botches.
Covers encoding round-trips, BOM detection, surrogate pairs, grapheme clusters,
NFC/NFD normalization, casefolding, byte-safe truncation, East-Asian display
width, and RTL/bidi detection.

Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import json
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path
from threading import Thread
from typing import Any

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import Mutant, Report, Teeth

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 19160
FAMILY_ZWJ = "👨‍👩‍👧"
MOJIBAKE_CAFE = "café".encode("utf-8").decode("latin-1")

# BOM byte sequences
BOM_UTF8 = b"\xef\xbb\xbf"
BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"

# RTL / bidi override characters that could indicate Trojan Source attack.
BIDI_OVERRIDE_CODEPOINTS = (
    0x202A,  # LEFT-TO-RIGHT EMBEDDING
    0x202B,  # RIGHT-TO-LEFT EMBEDDING
    0x202C,  # POP DIRECTIONAL FORMATTING
    0x202D,  # LEFT-TO-RIGHT OVERRIDE
    0x202E,  # RIGHT-TO-LEFT OVERRIDE
    0x2066,  # LEFT-TO-RIGHT ISOLATE
    0x2067,  # RIGHT-TO-LEFT ISOLATE
    0x2068,  # FIRST STRONG ISOLATE
    0x2069,  # POP DIRECTIONAL ISOLATE
    0x200F,  # RIGHT-TO-LEFT MARK
)
BIDI_OVERRIDE_CHARS = {chr(codepoint) for codepoint in BIDI_OVERRIDE_CODEPOINTS}
BIDI_RLO = chr(0x202E)
BIDI_RLO_SAMPLE = f"Hello{BIDI_RLO}World"
MOJIBAKE_MARKERS = ("Ã", "Â", "â", "�")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EncodingResult:
    encoding: str
    original_text: str
    encoded_bytes: bytes
    decoded_text: str
    round_trip_ok: bool
    is_mojibake: bool = False
    error: str | None = None


@dataclass
class NormalizationResult:
    form: str          # "NFC", "NFD", "NFKC", "NFKD"
    original: str
    normalized: str
    changed: bool
    byte_length_original: int
    byte_length_normalized: int


@dataclass
class GraphemeResult:
    text: str
    python_len: int           # naive len() — code points
    grapheme_count: int       # estimated grapheme cluster count
    utf16_units: int          # number of UTF-16 code units
    utf8_bytes: int           # byte length in UTF-8
    has_zwj: bool
    has_lone_surrogate: bool = False


@dataclass
class I18nReport:
    encoding_results: list[EncodingResult] = field(default_factory=list)
    normalization_results: list[NormalizationResult] = field(default_factory=list)
    grapheme_results: list[GraphemeResult] = field(default_factory=list)
    bidi_unsafe_texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def encoding_ok_count(self) -> int:
        return sum(1 for r in self.encoding_results if r.round_trip_ok)

    @property
    def encoding_fail_count(self) -> int:
        return sum(1 for r in self.encoding_results if not r.round_trip_ok)

    @property
    def mojibake_count(self) -> int:
        return sum(1 for r in self.encoding_results if r.is_mojibake)


# ---------------------------------------------------------------------------
# Encoding Round-Trip Tester
# ---------------------------------------------------------------------------

class EncodingTester:
    """Tests encode→decode roundtrips and detects mojibake."""

    SUPPORTED_ENCODINGS = ["utf-8", "utf-16", "latin-1", "ascii"]

    def test_roundtrip(self, text: str, encoding: str) -> EncodingResult:
        """Encode text to bytes and decode back; check for identity."""
        try:
            encoded = text.encode(encoding)
            decoded = encoded.decode(encoding)
            ok = decoded == text
            return EncodingResult(
                encoding=encoding,
                original_text=text,
                encoded_bytes=encoded,
                decoded_text=decoded,
                round_trip_ok=ok,
            )
        except (UnicodeEncodeError, UnicodeDecodeError) as exc:
            return EncodingResult(
                encoding=encoding,
                original_text=text,
                encoded_bytes=b"",
                decoded_text="",
                round_trip_ok=False,
                error=str(exc),
            )

    def detect_mojibake(self, text: str) -> EncodingResult:
        """
        Demonstrate mojibake: encode as UTF-8, then decode as latin-1.
        The resulting string will contain garbage replacement characters.
        """
        utf8_bytes = text.encode("utf-8")
        # Misinterpret UTF-8 bytes as latin-1 — produces mojibake
        mojibake_text = utf8_bytes.decode("latin-1")
        is_mojibake = mojibake_text != text
        return EncodingResult(
            encoding="utf-8→latin-1 (mojibake)",
            original_text=text,
            encoded_bytes=utf8_bytes,
            decoded_text=mojibake_text,
            round_trip_ok=False,
            is_mojibake=is_mojibake,
        )

    def test_ascii_only(self, text: str) -> EncodingResult:
        """Test whether text can be encoded as ASCII."""
        try:
            encoded = text.encode("ascii")
            decoded = encoded.decode("ascii")
            return EncodingResult(
                encoding="ascii",
                original_text=text,
                encoded_bytes=encoded,
                decoded_text=decoded,
                round_trip_ok=True,
            )
        except UnicodeEncodeError as exc:
            return EncodingResult(
                encoding="ascii",
                original_text=text,
                encoded_bytes=b"",
                decoded_text="",
                round_trip_ok=False,
                error=str(exc),
            )


def looks_like_mojibake(text: str) -> bool:
    """Return True when text appears to be an already-corrupted mojibake artifact."""
    if not any(marker in text for marker in MOJIBAKE_MARKERS):
        return False
    if "�" in text:
        return True
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return False
    return repaired != text


# ---------------------------------------------------------------------------
# BOM Detection and Stripping
# ---------------------------------------------------------------------------

class BOMDetector:
    """Detects and strips Byte Order Marks from byte strings."""

    @staticmethod
    def detect(data: bytes) -> str | None:
        """
        Return BOM type string or None.
        Returns: 'utf-8-sig', 'utf-16-le', 'utf-16-be', or None.
        """
        if data.startswith(BOM_UTF8):
            return "utf-8-sig"
        if data.startswith(BOM_UTF16_LE):
            return "utf-16-le"
        if data.startswith(BOM_UTF16_BE):
            return "utf-16-be"
        return None

    @staticmethod
    def strip(data: bytes) -> tuple[bytes, str | None]:
        """
        Strip BOM from data if present.
        Returns (stripped_bytes, bom_type_or_None).
        """
        bom_type = BOMDetector.detect(data)
        if bom_type == "utf-8-sig":
            return data[3:], bom_type
        if bom_type in ("utf-16-le", "utf-16-be"):
            return data[2:], bom_type
        return data, None

    @staticmethod
    def decode_with_bom(data: bytes) -> tuple[str, str | None]:
        """
        Decode bytes, auto-detecting and stripping BOM.
        Returns (decoded_text, bom_type_or_None).
        """
        bom_type = BOMDetector.detect(data)
        if bom_type == "utf-8-sig":
            return data[3:].decode("utf-8"), bom_type
        if bom_type == "utf-16-le":
            return data[2:].decode("utf-16-le"), bom_type
        if bom_type == "utf-16-be":
            return data[2:].decode("utf-16-be"), bom_type
        # No BOM — try UTF-8
        return data.decode("utf-8"), None


# ---------------------------------------------------------------------------
# Surrogate Pair / Astral Plane Analysis
# ---------------------------------------------------------------------------

class SurrogateTester:
    """Analyzes surrogate pairs, astral plane characters, and lone surrogates."""

    @staticmethod
    def analyze(text: str) -> GraphemeResult:
        """
        Analyze a string for code point count, UTF-16 units, UTF-8 bytes,
        ZWJ sequences, and lone surrogates.
        """
        python_len = len(text)
        # Use surrogatepass so strings with lone surrogates can still be measured
        utf8_bytes = len(text.encode("utf-8", errors="surrogatepass"))

        # Count UTF-16 code units (surrogate pairs add 2 units for astral chars)
        utf16_units = len(text.encode("utf-16-le", errors="surrogatepass")) // 2

        has_zwj = "‍" in text  # Zero Width Joiner

        # Detect lone surrogates (invalid in UTF-8; only possible via surrogatepass)
        has_lone_surrogate = False
        for ch in text:
            cp = ord(ch)
            if 0xD800 <= cp <= 0xDFFF:
                has_lone_surrogate = True
                break

        grapheme_count = estimate_grapheme_clusters(text)

        return GraphemeResult(
            text=text,
            python_len=python_len,
            grapheme_count=grapheme_count,
            utf16_units=utf16_units,
            utf8_bytes=utf8_bytes,
            has_zwj=has_zwj,
            has_lone_surrogate=has_lone_surrogate,
        )

    @staticmethod
    def make_lone_surrogate_string() -> str:
        """Create a string with a lone surrogate (using surrogatepass)."""
        # Encode a lone surrogate using surrogatepass codec error handler
        lone = b"\xed\xa0\x80"  # U+D800 encoded as UTF-8 with surrogatepass
        return lone.decode("utf-8", errors="surrogatepass")


# ---------------------------------------------------------------------------
# Grapheme Cluster Estimator
# ---------------------------------------------------------------------------

def estimate_grapheme_clusters(text: str) -> int:
    """
    Estimate the number of grapheme clusters using unicodedata.
    This handles ZWJ sequences (family emoji etc.) and combining characters.

    Algorithm:
    - Group characters that are joined by Zero Width Joiner (U+200D)
    - Group combining marks (category starting with "M") with the preceding base
    - Each group = 1 grapheme cluster
    - Variation selectors (VS15/VS16 U+FE0E/U+FE0F) attach to the preceding char
    """
    if not text:
        return 0

    clusters = []
    i = 0
    chars = list(text)
    n = len(chars)

    while i < n:
        cluster_start = i
        i += 1  # consume the base character

        # Consume any following ZWJ sequences and combining marks / variation selectors
        while i < n:
            ch = chars[i]
            cp = ord(ch)
            cat = unicodedata.category(ch)

            # Zero Width Joiner — next character is part of this cluster
            if ch == "‍":
                i += 1  # consume ZWJ
                if i < n:
                    i += 1  # consume character after ZWJ
                continue

            # Combining marks attach to the base
            if cat.startswith("M"):
                i += 1
                continue

            # Variation selectors (U+FE00–U+FE0F, U+FE0E, U+FE0F and U+E0100–U+E01EF)
            if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
                i += 1
                continue

            # Regional indicator letters (flags): pairs form one cluster
            # U+1F1E6–U+1F1FF
            if 0x1F1E6 <= cp <= 0x1F1FF:
                # This is a regional indicator — if prev char was also one, they pair
                # Here we just treat each regional indicator as standalone
                # (simple estimation; not full UAX#29)
                break

            # Emoji modifier (skin tone) attaches to preceding emoji
            if 0x1F3FB <= cp <= 0x1F3FF:
                i += 1
                continue

            # Enclosing combining mark
            if cat == "Me":
                i += 1
                continue

            break

        clusters.append(text[cluster_start:i])

    return len(clusters)


# ---------------------------------------------------------------------------
# NFC/NFD Normalization
# ---------------------------------------------------------------------------

class NormalizationTester:
    """Tests Unicode normalization forms."""

    FORMS = ["NFC", "NFD", "NFKC", "NFKD"]

    def normalize(self, text: str, form: str) -> NormalizationResult:
        """Normalize text to the given form."""
        normalized = unicodedata.normalize(form, text)
        return NormalizationResult(
            form=form,
            original=text,
            normalized=normalized,
            changed=normalized != text,
            byte_length_original=len(text.encode("utf-8")),
            byte_length_normalized=len(normalized.encode("utf-8")),
        )

    def demonstrate_dedup_bug(self, text_nfc: str, text_nfd: str) -> dict[str, Any]:
        """
        Demonstrate the un-normalized dedup bug:
        Two strings that are semantically equal but byte-unequal will NOT be
        deduped when used as dict keys without normalization.
        """
        # Without normalization
        raw_set = {text_nfc, text_nfd}
        raw_dict = {}
        raw_dict[text_nfc] = "value1"
        raw_dict[text_nfd] = "value2"

        # With normalization
        norm_set = {
            unicodedata.normalize("NFC", text_nfc),
            unicodedata.normalize("NFC", text_nfd),
        }
        norm_dict = {}
        norm_dict[unicodedata.normalize("NFC", text_nfc)] = "value1"
        norm_dict[unicodedata.normalize("NFC", text_nfd)] = "value2"

        return {
            "text_nfc": text_nfc,
            "text_nfd": text_nfd,
            "byte_equal": text_nfc == text_nfd,
            "normalize_equal": (
                unicodedata.normalize("NFC", text_nfc)
                == unicodedata.normalize("NFC", text_nfd)
            ),
            "raw_set_size": len(raw_set),
            "norm_set_size": len(norm_set),
            "raw_dict_overwrites": len(raw_dict) == 1,
            "norm_dict_overwrites": len(norm_dict) == 1,
            "dedup_bug_demonstrated": len(raw_set) == 2 and len(norm_set) == 1,
        }

    def are_equivalent(self, a: str, b: str, form: str = "NFC") -> bool:
        """Check if two strings are canonically equivalent."""
        return unicodedata.normalize(form, a) == unicodedata.normalize(form, b)


# ---------------------------------------------------------------------------
# Casefolding
# ---------------------------------------------------------------------------

class CasefoldTester:
    """Tests Unicode casefolding edge cases."""

    def casefold_compare(self, a: str, b: str) -> bool:
        """Case-insensitive comparison using proper Unicode casefolding."""
        return a.casefold() == b.casefold()

    def demonstrate_german_sharp_s(self) -> dict[str, Any]:
        """
        German ß (sharp s) casefolds to 'ss', not to 'ß'.
        str.lower() returns 'ß', but str.casefold() returns 'ss'.
        """
        sharp_s = "ß"
        upper = "SS"
        return {
            "char": sharp_s,
            "lower": sharp_s.lower(),          # 'ß' — unchanged
            "casefold": sharp_s.casefold(),    # 'ss'
            "upper_lower": upper.lower(),       # 'ss'
            "lower_equal": sharp_s.lower() == upper.lower(),        # True
            "casefold_equal": sharp_s.casefold() == upper.casefold(),  # True
            "strass": "Straße",
            "strasse": "Strasse",
            "strass_casefold": "Straße".casefold(),  # 'strasse'
            "strass_lower": "Straße".lower(),          # 'straße'
            "strass_casefold_match": "Straße".casefold() == "Strasse".casefold(),
        }

    def demonstrate_turkish_dotless_i(self) -> dict[str, Any]:
        """
        Turkish dotless-ı trap:
        In Turkish locale, uppercase I lowercases to dotless ı (U+0131),
        and lowercase i uppercases to İ (U+0130).
        Python's str.lower() uses the default C locale, not Turkish,
        so 'I'.lower() == 'i', not 'ı'.
        This demonstrates the pitfall for locale-aware systems.
        """
        capital_i = "I"
        dotless_i = "ı"  # ı
        dotted_capital_i = "İ"  # İ

        return {
            "capital_I_lower_python": capital_i.lower(),        # 'i' (not 'ı')
            "capital_I_casefold": capital_i.casefold(),         # 'i' (not 'ı')
            "dotless_i_upper": dotless_i.upper(),               # 'I'
            "dotted_cap_lower": dotted_capital_i.lower(),       # 'i̇' (dotted!)
            "turkish_trap": capital_i.lower() != dotless_i,     # True — Python is NOT Turkish
            "dotless_i_is_different": dotless_i != "i",         # True
            "dotless_i_codepoint": hex(ord(dotless_i)),         # 0x131
        }

    def casefold_normalize(self, text: str) -> str:
        """Casefold and NFC-normalize — correct way to do case-insensitive compare."""
        return unicodedata.normalize("NFC", text.casefold())


# ---------------------------------------------------------------------------
# Byte-Safe Truncation
# ---------------------------------------------------------------------------

def safe_truncate_bytes(text: str, max_bytes: int, encoding: str = "utf-8") -> str:
    """
    Truncate a string to at most max_bytes bytes without splitting multi-byte
    sequences. Returns the longest valid string that fits within max_bytes.
    """
    encoded = text.encode(encoding)
    if len(encoded) <= max_bytes:
        return text

    # Truncate at the byte boundary, then decode with errors='ignore'
    # to strip any partial multi-byte sequence at the end
    truncated = encoded[:max_bytes]
    # Decode, ignoring any incomplete sequences
    return truncated.decode(encoding, errors="ignore")


# ---------------------------------------------------------------------------
# East-Asian Display Width
# ---------------------------------------------------------------------------

class DisplayWidthCalculator:
    """Calculates display width of strings considering East-Asian full-width chars."""

    # east_asian_width categories that count as 2 columns
    WIDE_CATEGORIES = {"W", "F"}  # Wide, Fullwidth

    @classmethod
    def char_width(cls, ch: str) -> int:
        """Return display width of a single character (1 or 2)."""
        eaw = unicodedata.east_asian_width(ch)
        return 2 if eaw in cls.WIDE_CATEGORIES else 1

    @classmethod
    def display_width(cls, text: str) -> int:
        """Return total display width of a string."""
        return sum(cls.char_width(ch) for ch in text)

    @classmethod
    def truncate_to_width(cls, text: str, max_width: int) -> str:
        """Truncate text to at most max_width display columns."""
        result = []
        current_width = 0
        for ch in text:
            w = cls.char_width(ch)
            if current_width + w > max_width:
                break
            result.append(ch)
            current_width += w
        return "".join(result)

    @classmethod
    def analyze(cls, text: str) -> dict[str, Any]:
        """Return width analysis of a string."""
        char_widths = [(ch, cls.char_width(ch)) for ch in text]
        total_width = sum(w for _, w in char_widths)
        wide_chars = [ch for ch, w in char_widths if w == 2]
        return {
            "text": text,
            "python_len": len(text),
            "display_width": total_width,
            "wide_char_count": len(wide_chars),
            "wide_chars": wide_chars,
        }


# ---------------------------------------------------------------------------
# RTL / Bidi Detection
# ---------------------------------------------------------------------------

class BidiDetector:
    """Detects RTL/bidi override characters that could enable Trojan Source attacks."""

    @staticmethod
    def contains_bidi_override(text: str) -> bool:
        """Return True if text contains any bidi override character."""
        return any(ch in BIDI_OVERRIDE_CHARS for ch in text)

    @staticmethod
    def find_bidi_overrides(text: str) -> list[tuple[int, str, str]]:
        """
        Return list of (index, char, unicode_name) for each bidi override found.
        """
        results = []
        for i, ch in enumerate(text):
            if ch in BIDI_OVERRIDE_CHARS:
                try:
                    name = unicodedata.name(ch, f"U+{ord(ch):04X}")
                except ValueError:
                    name = f"U+{ord(ch):04X}"
                results.append((i, ch, name))
        return results

    @staticmethod
    def is_rtl_text(text: str) -> bool:
        """
        Heuristic: check if text contains Arabic or Hebrew characters,
        which are inherently RTL.
        """
        for ch in text:
            cat = unicodedata.bidirectional(ch)
            if cat in ("R", "AL", "RLE", "RLO", "RLI"):
                return True
        return False

    @staticmethod
    def scan_for_trojan_source(text: str) -> dict[str, Any]:
        """
        Scan text for Trojan Source attack indicators (CVE-2021-42574).
        Returns a report dict.
        """
        overrides = BidiDetector.find_bidi_overrides(text)
        return {
            "text_preview": text[:80] if len(text) > 80 else text,
            "has_bidi_override": len(overrides) > 0,
            "override_count": len(overrides),
            "overrides": [
                {"index": i, "char": repr(ch), "name": name}
                for i, ch, name in overrides
            ],
            "is_unsafe": len(overrides) > 0,
        }


# ---------------------------------------------------------------------------
# TEETH: frozen Unicode audit corpus + planted analyzer defects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class I18nAuditCase:
    """One frozen i18n observation with literal expected audit events."""

    name: str
    kind: str
    text: str = ""
    other: str = ""
    max_bytes: int = 0
    expected_events: tuple[str, ...] = ()


I18N_AUDIT_CORPUS: tuple[I18nAuditCase, ...] = (
    I18nAuditCase(
        name="nfc_nfd_canonical_equivalence",
        kind="normalization",
        text="é",
        other="e\u0301",
        expected_events=("raw_equal:no", "normalized_equal:yes"),
    ),
    I18nAuditCase(
        name="mojibake_detected",
        kind="mojibake",
        text=MOJIBAKE_CAFE,
        expected_events=("mojibake:yes",),
    ),
    I18nAuditCase(
        name="mojibake_clean_sample_not_flagged",
        kind="mojibake",
        text="café",
        expected_events=("mojibake:no",),
    ),
    I18nAuditCase(
        name="zwj_family_single_grapheme",
        kind="grapheme",
        text=FAMILY_ZWJ,
        expected_events=("python_len:5", "graphemes:1", "has_zwj:yes"),
    ),
    I18nAuditCase(
        name="utf8_truncation_safe",
        kind="truncate",
        text="café",
        max_bytes=4,
        expected_events=("truncated:caf", "valid_utf8:yes"),
    ),
    I18nAuditCase(
        name="east_asian_width",
        kind="width",
        text="A日B",
        expected_events=("display_width:4",),
    ),
    I18nAuditCase(
        name="bidi_override_detected",
        kind="bidi",
        text="Hello\u202eWorld",
        expected_events=("bidi_override:yes", "unsafe:yes"),
    ),
)


def _audit_normalization(case: I18nAuditCase) -> tuple[str, ...]:
    tester = NormalizationTester()
    return (
        f"raw_equal:{'yes' if case.text == case.other else 'no'}",
        f"normalized_equal:{'yes' if tester.are_equivalent(case.text, case.other) else 'no'}",
    )


def _audit_mojibake(case: I18nAuditCase) -> tuple[str, ...]:
    return (f"mojibake:{'yes' if looks_like_mojibake(case.text) else 'no'}",)


def _audit_grapheme(case: I18nAuditCase) -> tuple[str, ...]:
    result = SurrogateTester.analyze(case.text)
    return (
        f"python_len:{result.python_len}",
        f"graphemes:{result.grapheme_count}",
        f"has_zwj:{'yes' if result.has_zwj else 'no'}",
    )


def _is_utf8_encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _audit_truncate(case: I18nAuditCase) -> tuple[str, ...]:
    truncated = safe_truncate_bytes(case.text, case.max_bytes)
    valid = "yes" if _is_utf8_encodable(truncated) else "no"
    return (f"truncated:{truncated}", f"valid_utf8:{valid}")


def _audit_width(case: I18nAuditCase) -> tuple[str, ...]:
    width = DisplayWidthCalculator.display_width(case.text)
    return (f"display_width:{width}",)


def _audit_bidi(case: I18nAuditCase) -> tuple[str, ...]:
    result = BidiDetector.scan_for_trojan_source(case.text)
    return (
        f"bidi_override:{'yes' if result['has_bidi_override'] else 'no'}",
        f"unsafe:{'yes' if result['is_unsafe'] else 'no'}",
    )


I18N_AUDITORS: dict[str, Callable[[I18nAuditCase], tuple[str, ...]]] = {
    "normalization": _audit_normalization,
    "mojibake": _audit_mojibake,
    "grapheme": _audit_grapheme,
    "truncate": _audit_truncate,
    "width": _audit_width,
    "bidi": _audit_bidi,
}


def oracle_i18n_audit(case: I18nAuditCase) -> tuple[str, ...]:
    """Correct pure analyzer over frozen Unicode/i18n cases."""
    try:
        auditor = I18N_AUDITORS[case.kind]
    except KeyError as exc:
        raise ValueError(f"unknown i18n audit kind: {case.kind}") from exc
    return auditor(case)


def raw_normalization_auditor(case: I18nAuditCase) -> tuple[str, ...]:
    """BUG: compares raw code points and skips canonical normalization."""
    if case.kind == "normalization":
        raw = "yes" if case.text == case.other else "no"
        return (f"raw_equal:{raw}", f"normalized_equal:{raw}")
    return oracle_i18n_audit(case)


def generated_mojibake_auditor(case: I18nAuditCase) -> tuple[str, ...]:
    """BUG: generates mojibake from clean text instead of detecting corrupted input."""
    if case.kind == "mojibake":
        result = EncodingTester().detect_mojibake(case.text)
        return (f"mojibake:{'yes' if result.is_mojibake else 'no'}",)
    return oracle_i18n_audit(case)


def naive_grapheme_auditor(case: I18nAuditCase) -> tuple[str, ...]:
    """BUG: treats Python code-point length as user-visible grapheme count."""
    if case.kind == "grapheme":
        result = SurrogateTester.analyze(case.text)
        return (
            f"python_len:{result.python_len}",
            f"graphemes:{result.python_len}",
            f"has_zwj:{'yes' if result.has_zwj else 'no'}",
        )
    return oracle_i18n_audit(case)


def byte_slice_truncation_auditor(case: I18nAuditCase) -> tuple[str, ...]:
    """BUG: slices raw UTF-8 bytes and can split a multi-byte scalar."""
    if case.kind == "truncate":
        raw = case.text.encode("utf-8")[:case.max_bytes]
        try:
            truncated = raw.decode("utf-8")
            valid = "yes"
        except UnicodeDecodeError:
            truncated = "<invalid>"
            valid = "no"
        return (f"truncated:{truncated}", f"valid_utf8:{valid}")
    return oracle_i18n_audit(case)


def bidi_blind_auditor(case: I18nAuditCase) -> tuple[str, ...]:
    """BUG: ignores directional override characters."""
    if case.kind == "bidi":
        return ("bidi_override:no", "unsafe:no")
    return oracle_i18n_audit(case)


def prove(impl: Callable[[I18nAuditCase], tuple[str, ...]]) -> bool:
    """True iff the analyzer diverges from any frozen i18n expectation."""
    for case in I18N_AUDIT_CORPUS:
        try:
            if tuple(impl(case)) != case.expected_events:
                return True
        except Exception:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_i18n_audit,
    mutants=(
        Mutant("raw_normalization_auditor", raw_normalization_auditor,
               "misses canonical NFC/NFD equivalence"),
        Mutant("generated_mojibake_auditor", generated_mojibake_auditor,
               "generates mojibake from clean text instead of detecting corrupted input"),
        Mutant("naive_grapheme_auditor", naive_grapheme_auditor,
               "counts code points instead of grapheme clusters"),
        Mutant("byte_slice_truncation_auditor", byte_slice_truncation_auditor,
               "splits UTF-8 multi-byte characters while truncating"),
        Mutant("bidi_blind_auditor", bidi_blind_auditor,
               "misses bidi override / Trojan Source indicators"),
    ),
    corpus_size=len(I18N_AUDIT_CORPUS),
    kind="oracle_swap",
    notes="Frozen Unicode normalization, grapheme, truncation, width, and bidi corpus.",
)


def list_scenarios() -> list[str]:
    return [case.name for case in I18N_AUDIT_CORPUS]


# ---------------------------------------------------------------------------
# Combined I18n Analyzer
# ---------------------------------------------------------------------------

class I18nAnalyzer:
    """High-level API combining all i18n checks."""

    def __init__(self):
        self.encoding_tester = EncodingTester()
        self.bom_detector = BOMDetector()
        self.surrogate_tester = SurrogateTester()
        self.normalization_tester = NormalizationTester()
        self.casefold_tester = CasefoldTester()
        self.display_width = DisplayWidthCalculator()
        self.bidi_detector = BidiDetector()

    def full_report(self, texts: list[str]) -> I18nReport:
        """Run all i18n checks on the given list of texts."""
        report = I18nReport()

        for text in texts:
            # Encoding round-trips
            for enc in ["utf-8", "utf-16", "latin-1"]:
                try:
                    result = self.encoding_tester.test_roundtrip(text, enc)
                    report.encoding_results.append(result)
                except Exception as exc:
                    report.warnings.append(f"Encoding test error for {enc}: {exc}")

            # Mojibake detection (only for non-ASCII text)
            if any(ord(ch) > 127 for ch in text):
                mojibake = self.encoding_tester.detect_mojibake(text)
                report.encoding_results.append(mojibake)

            # Normalization
            for form in ["NFC", "NFD"]:
                try:
                    result = self.normalization_tester.normalize(text, form)
                    report.normalization_results.append(result)
                except Exception as exc:
                    report.warnings.append(f"Normalization error for {form}: {exc}")

            # Grapheme analysis
            try:
                gr = self.surrogate_tester.analyze(text)
                report.grapheme_results.append(gr)
            except Exception as exc:
                report.warnings.append(f"Grapheme analysis error: {exc}")

            # Bidi detection
            if self.bidi_detector.contains_bidi_override(text):
                report.bidi_unsafe_texts.append(text)

        return report


# ---------------------------------------------------------------------------
# Mock HTTP Server
# ---------------------------------------------------------------------------

class MockI18nHandler(BaseHTTPRequestHandler):
    """HTTP handler for the mock i18n server."""

    def log_message(self, fmt, *args):
        pass  # Suppress default access log

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json({"status": "ok", "harness": "i18n"})

        elif path == "/encoding/roundtrip":
            tester = EncodingTester()
            text = "Héllo Wörld — こんにちは"
            results = []
            for enc in ["utf-8", "utf-16", "latin-1"]:
                r = tester.test_roundtrip(text, enc)
                results.append({
                    "encoding": r.encoding,
                    "round_trip_ok": r.round_trip_ok,
                    "error": r.error,
                })
            self._send_json({"text": text, "results": results})

        elif path == "/encoding/mojibake":
            tester = EncodingTester()
            text = "café"
            r = tester.detect_mojibake(text)
            self._send_json({
                "original": r.original_text,
                "mojibake": r.decoded_text,
                "is_mojibake": r.is_mojibake,
            })

        elif path == "/bom/detect":
            results = []
            # UTF-8 with BOM
            text = "Hello"
            for bom_name, bom_bytes, encoding in [
                ("utf-8-sig", BOM_UTF8, "utf-8"),
                ("utf-16-le", BOM_UTF16_LE, "utf-16-le"),
                ("utf-16-be", BOM_UTF16_BE, "utf-16-be"),
            ]:
                data = bom_bytes + text.encode(encoding)
                detected = BOMDetector.detect(data)
                stripped, bom_type = BOMDetector.strip(data)
                results.append({
                    "bom_name": bom_name,
                    "detected": detected,
                    "stripped_length": len(stripped),
                })
            self._send_json({"results": results})

        elif path == "/grapheme/analyze":
            analyzer = SurrogateTester()
            emoji = "🎉"
            family = "👨‍👩‍👧"
            results = []
            for text in [emoji, family]:
                gr = analyzer.analyze(text)
                results.append({
                    "text": text,
                    "python_len": gr.python_len,
                    "grapheme_count": gr.grapheme_count,
                    "utf16_units": gr.utf16_units,
                    "utf8_bytes": gr.utf8_bytes,
                    "has_zwj": gr.has_zwj,
                })
            self._send_json({"results": results})

        elif path == "/normalization/demo":
            tester = NormalizationTester()
            # é as precomposed (NFC) vs decomposed (NFD)
            e_precomposed = "é"    # é — single code point
            e_decomposed = "é"   # e + combining acute accent
            dedup = tester.demonstrate_dedup_bug(e_precomposed, e_decomposed)
            self._send_json(dedup)

        elif path == "/casefold/german":
            tester = CasefoldTester()
            result = tester.demonstrate_german_sharp_s()
            self._send_json(result)

        elif path == "/casefold/turkish":
            tester = CasefoldTester()
            result = tester.demonstrate_turkish_dotless_i()
            self._send_json(result)

        elif path == "/truncate":
            text = "Hello 🎉 World"
            results = {}
            for n in [5, 8, 10, 14]:
                results[str(n)] = safe_truncate_bytes(text, n)
            self._send_json({"text": text, "truncated": results})

        elif path == "/width/eastasian":
            calc = DisplayWidthCalculator()
            texts = [
                "Hello",          # all ASCII width 1
                "こんにちは",        # all full-width
                "A日B",            # mixed
            ]
            results = [calc.analyze(t) for t in texts]
            self._send_json({"results": results})

        elif path == "/bidi/detect":
            detector = BidiDetector()
            safe_text = "Hello World"
            unsafe_text = BIDI_RLO_SAMPLE
            self._send_json({
                "safe": detector.scan_for_trojan_source(safe_text),
                "unsafe": detector.scan_for_trojan_source(unsafe_text),
            })

        elif path == "/report":
            analyzer = I18nAnalyzer()
            texts = [
                "Hello World",
                "café",
                "Héllo Wörld",
                "🎉",
                "こんにちは",
            ]
            report = analyzer.full_report(texts)
            self._send_json({
                "encoding_ok": report.encoding_ok_count,
                "encoding_fail": report.encoding_fail_count,
                "mojibake_count": report.mojibake_count,
                "normalization_count": len(report.normalization_results),
                "grapheme_count": len(report.grapheme_results),
                "bidi_unsafe_count": len(report.bidi_unsafe_texts),
                "warnings": report.warnings,
            })

        else:
            self._send_error(404, "Not Found")

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        self._send_json({"error": message}, status=status)


# ---------------------------------------------------------------------------
# Server lifecycle helpers
# ---------------------------------------------------------------------------

def start_server(port: int = DEFAULT_PORT) -> HTTPServer:
    """Start the mock i18n server in a background daemon thread."""
    server = HTTPServer(("127.0.0.1", port), MockI18nHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop_server(server: HTTPServer) -> None:
    """Shut down the mock server."""
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# Self-test / CLI entry point
# ---------------------------------------------------------------------------

def _self_test(as_json: bool = False) -> int:
    """Run a quick smoke-test and return exit code (0 = pass)."""

    report = Report("core/i18n")

    # 1. Encoding round-trip
    tester = EncodingTester()
    roundtrips = [
        tester.test_roundtrip("Hello, World!", enc).round_trip_ok
        for enc in ("utf-8", "utf-16", "latin-1")
    ]
    report.record("encoding_roundtrips_green", all(roundtrips), detail=f"roundtrips={roundtrips}")

    # 2. Mojibake detection
    r = tester.detect_mojibake("café")
    report.record("mojibake_detected", r.is_mojibake)

    # 3. BOM detection
    data = BOM_UTF8 + b"hello"
    bom = BOMDetector.detect(data)
    report.add("bom_detect_utf8_sig", "utf-8-sig", bom)

    # 4. BOM stripping
    stripped, bom_type = BOMDetector.strip(data)
    report.add("bom_strip_bytes", b"hello", stripped)
    report.add("bom_strip_type", "utf-8-sig", bom_type)

    # 5. Grapheme clusters
    gr = SurrogateTester().analyze(FAMILY_ZWJ)
    report.add("family_grapheme_count", 1, gr.grapheme_count)

    # 6. NFC/NFD normalization
    e_pre = "é"
    e_dec = "é"
    nt = NormalizationTester()
    report.record("nfc_nfd_equivalent", nt.are_equivalent(e_pre, e_dec))

    # 7. Dedup bug
    dedup = nt.demonstrate_dedup_bug(e_pre, e_dec)
    report.record("dedup_bug_demonstrated", bool(dedup["dedup_bug_demonstrated"]))

    # 8. Casefolding
    ct = CasefoldTester()
    german = ct.demonstrate_german_sharp_s()
    report.add("german_sharp_s_casefold", "ss", german["casefold"])

    # 9. Byte-safe truncation
    text = "café"  # 5 bytes in UTF-8 (c-a-f-é where é is 2 bytes)
    truncated = safe_truncate_bytes(text, 4)
    report.record("safe_truncate_valid_utf8", _is_utf8_encodable(truncated))

    # 10. Display width
    calc = DisplayWidthCalculator()
    report.add("ascii_display_width", 1, calc.display_width("A"))
    report.add("cjk_display_width", 2, calc.display_width("日"))

    # 11. Bidi detection
    detector = BidiDetector()
    report.record("bidi_override_detected", detector.contains_bidi_override(BIDI_RLO_SAMPLE))
    report.record("bidi_safe_text_clean", not detector.contains_bidi_override("Hello World"))
    for case in I18N_AUDIT_CORPUS:
        report.add(
            f"oracle_i18n_audit:{case.name}",
            list(case.expected_events),
            list(oracle_i18n_audit(case)),
        )
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="i18n / Unicode / Encoding Test Harness")
    parser.add_argument("--self-test", action="store_true", help="Run self-tests and exit")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="List frozen TEETH scenario names and exit")
    parser.add_argument("--json", action="store_true",
                        help="Output self-test report as JSON")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port")
    parser.add_argument("--serve", action="store_true", help="Start mock server")
    args = parser.parse_args()

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        sys.exit(0)

    if args.self_test:
        sys.exit(_self_test(as_json=args.json))

    if args.serve:
        server = start_server(args.port)
        print(f"i18n mock server running on port {args.port}. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_server(server)
            print("Server stopped.")
    else:
        parser.print_help()
