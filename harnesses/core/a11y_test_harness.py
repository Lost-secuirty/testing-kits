"""
Accessibility (a11y) Test Harness - WCAG-flavored static checks on HTML.
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class A11yIssue:
    checker_name: str
    severity: str  # "ERROR" / "WARNING" / "INFO"
    description: str
    element: str
    wcag_criterion: str


@dataclass
class A11yReport:
    issues: list[A11yIssue] = field(default_factory=list)

    def add(self, issue: A11yIssue) -> None:
        self.issues.append(issue)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {"ERROR": 0, "WARNING": 0, "INFO": 0}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts

    def errors(self) -> list[A11yIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    def warnings(self) -> list[A11yIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    def by_checker(self, name: str) -> list[A11yIssue]:
        return [i for i in self.issues if i.checker_name == name]


# ---------------------------------------------------------------------------
# HTML Parser helper
# ---------------------------------------------------------------------------

class ElementInfo:
    """Represents a parsed HTML element."""

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], text: str = ""):
        self.tag = tag.lower()
        self.attrs: dict[str, str | None] = {k.lower(): v for k, v in attrs}
        self.text = text
        self.children: list[ElementInfo] = []
        self.parent: ElementInfo | None = None

    def get(self, attr: str, default: str | None = None) -> str | None:
        return self.attrs.get(attr.lower(), default)

    def __str__(self) -> str:
        attrs_str = " ".join(
            f'{k}="{v}"' if v is not None else k
            for k, v in self.attrs.items()
        )
        if attrs_str:
            return f"<{self.tag} {attrs_str}>"
        return f"<{self.tag}>"


class A11yHTMLParser(HTMLParser):
    """Full-document HTML parser that builds a flat element list."""

    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[ElementInfo] = []
        self._stack: list[ElementInfo] = []
        self._current_text: list[str] = []
        self.html_element: ElementInfo | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        elem = ElementInfo(tag, attrs)
        if tag.lower() == "html":
            self.html_element = elem
        if self._stack:
            elem.parent = self._stack[-1]
            self._stack[-1].children.append(elem)
        self.elements.append(elem)
        if tag.lower() not in self.VOID_ELEMENTS:
            self._stack.append(elem)
        self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        # Pop matching tag from stack
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag.lower():
                # Assign accumulated text to the element
                self._stack[i].text = "".join(self._current_text).strip()
                self._stack.pop(i)
                break
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._current_text.append(data)

    def get_elements_by_tag(self, tag: str) -> list[ElementInfo]:
        return [e for e in self.elements if e.tag == tag.lower()]

    def get_element_by_id(self, elem_id: str) -> ElementInfo | None:
        for e in self.elements:
            if e.get("id") == elem_id:
                return e
        return None


def parse_html(html_content: str) -> A11yHTMLParser:
    parser = A11yHTMLParser()
    parser.feed(html_content)
    return parser


# ---------------------------------------------------------------------------
# Colour / contrast utilities
# ---------------------------------------------------------------------------

def _parse_hex_color(value: str) -> tuple[int, int, int] | None:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) == 6:
        try:
            r = int(value[0:2], 16)
            g = int(value[2:4], 16)
            b = int(value[4:6], 16)
            return r, g, b
        except ValueError:
            return None
    return None


def _parse_rgb_color(value: str) -> tuple[int, int, int] | None:
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value.strip())
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def parse_color(value: str) -> tuple[int, int, int] | None:
    """Parse #rrggbb, #rgb, or rgb() color value to (r, g, b) tuple."""
    value = value.strip()
    if value.startswith("#"):
        return _parse_hex_color(value)
    if value.startswith("rgb"):
        return _parse_rgb_color(value)
    return None


def relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG 2.1 relative luminance."""
    def channel(c: int) -> float:
        s = c / 255.0
        if s <= 0.03928:
            return s / 12.92
        return ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """WCAG contrast ratio between two colours."""
    l1 = relative_luminance(*rgb1)
    l2 = relative_luminance(*rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def parse_inline_style(style: str) -> dict[str, str]:
    """Parse a CSS inline style string into a dict."""
    result: dict[str, str] = {}
    if not style:
        return result
    for part in style.split(";"):
        part = part.strip()
        if ":" in part:
            key, _, val = part.partition(":")
            result[key.strip().lower()] = val.strip()
    return result


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------

SUSPICIOUS_ALT_WORDS = {"image", "photo", "picture", "graphic", "icon", "img", "figure"}


class AltTextChecker:
    NAME = "AltTextChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []
        for img in parser.get_elements_by_tag("img"):
            if "alt" not in img.attrs:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="ERROR",
                    description="img element missing alt attribute",
                    element=str(img),
                    wcag_criterion="1.1.1",
                ))
                continue

            alt = img.attrs["alt"] or ""
            if alt == "":
                # Empty alt is valid for decorative images; issue INFO
                # But we still track it as INFO
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="INFO",
                    description="img element has empty alt attribute (decorative?)",
                    element=str(img),
                    wcag_criterion="1.1.1",
                ))
                continue

            alt_lower = alt.lower().strip()
            # Check suspicious words
            if alt_lower in SUSPICIOUS_ALT_WORDS:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="WARNING",
                    description=f'img alt text "{alt}" is not descriptive',
                    element=str(img),
                    wcag_criterion="1.1.1",
                ))
                continue

            # Check if alt matches filename pattern of src
            src = img.get("src", "") or ""
            if src:
                filename = src.split("/")[-1].split("?")[0]  # e.g. "photo.jpg"
                name_without_ext = re.sub(r"\.\w+$", "", filename).lower()
                if name_without_ext and alt_lower == name_without_ext:
                    issues.append(A11yIssue(
                        checker_name=self.NAME,
                        severity="WARNING",
                        description=f'img alt text "{alt}" appears to be the filename',
                        element=str(img),
                        wcag_criterion="1.1.1",
                    ))

        return issues


INTERACTIVE_INPUTS = {"input", "select", "textarea"}
SKIP_INPUT_TYPES = {"hidden", "submit", "reset", "button", "image"}


class LabelChecker:
    NAME = "LabelChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []

        # Build set of label targets (for= attributes)
        labeled_ids: set[str] = set()
        for label in parser.get_elements_by_tag("label"):
            for_attr = label.get("for")
            if for_attr:
                labeled_ids.add(for_attr)

        for tag in INTERACTIVE_INPUTS:
            for elem in parser.get_elements_by_tag(tag):
                # Skip hidden/button types for input
                if tag == "input":
                    input_type = (elem.get("type") or "text").lower()
                    if input_type in SKIP_INPUT_TYPES:
                        continue

                elem_id = elem.get("id")
                aria_label = elem.get("aria-label")
                aria_labelledby = elem.get("aria-labelledby")

                has_label = (
                    (elem_id and elem_id in labeled_ids)
                    or aria_label
                    or aria_labelledby
                )

                if not has_label:
                    issues.append(A11yIssue(
                        checker_name=self.NAME,
                        severity="ERROR",
                        description=f"<{tag}> element lacks accessible label",
                        element=str(elem),
                        wcag_criterion="1.3.1",
                    ))

        return issues


class HeadingOrderChecker:
    NAME = "HeadingOrderChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []
        HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]

        headings = []
        for elem in parser.elements:
            if elem.tag in HEADING_TAGS:
                headings.append(elem)

        # Check for multiple h1
        h1_list = [h for h in headings if h.tag == "h1"]
        if len(h1_list) > 1:
            for extra_h1 in h1_list[1:]:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="WARNING",
                    description="Multiple <h1> elements found",
                    element=str(extra_h1),
                    wcag_criterion="1.3.1",
                ))

        # Check for skipped heading levels
        prev_level = 0
        for heading in headings:
            level = int(heading.tag[1])
            if prev_level > 0 and level > prev_level + 1:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="ERROR",
                    description=f"Heading level skipped: h{prev_level} to {heading.tag}",
                    element=str(heading),
                    wcag_criterion="1.3.1",
                ))
            prev_level = level

        return issues


# ARIA roles reference list (WAI-ARIA 1.2)
VALID_ARIA_ROLES = {
    # Abstract roles (should not be used directly)
    # Landmark roles
    "banner", "complementary", "contentinfo", "form", "main", "navigation",
    "region", "search",
    # Widget roles
    "alert", "alertdialog", "button", "checkbox", "combobox", "dialog",
    "grid", "gridcell", "link", "listbox", "log", "marquee", "menubar",
    "menu", "menuitem", "menuitemcheckbox", "menuitemradio", "option",
    "progressbar", "radio", "radiogroup", "scrollbar", "searchbox",
    "separator", "slider", "spinbutton", "status", "switch", "tab",
    "tablist", "tabpanel", "textbox", "timer", "toolbar", "tooltip",
    "tree", "treegrid", "treeitem",
    # Document structure roles
    "application", "article", "cell", "columnheader", "definition",
    "directory", "document", "feed", "figure", "group", "heading",
    "img", "list", "listitem", "math", "none", "note", "presentation",
    "row", "rowgroup", "rowheader", "table", "term",
    # Live region roles
    "generic",
}

# Required aria-* attributes for certain roles
REQUIRED_ARIA_ATTRS: dict[str, list[str]] = {
    "checkbox": ["aria-checked"],
    "combobox": ["aria-expanded"],
    "scrollbar": ["aria-controls", "aria-valuenow"],
    "slider": ["aria-valuenow"],
    "spinbutton": ["aria-valuenow"],
    "switch": ["aria-checked"],
    "option": [],
}

# Focusable element tags
FOCUSABLE_TAGS = {"a", "button", "input", "select", "textarea", "details", "summary"}


class AriaChecker:
    NAME = "AriaChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []

        for elem in parser.elements:
            role = elem.get("role")
            aria_hidden = elem.get("aria-hidden")

            # Validate role attribute
            if role is not None:
                role_lower = role.strip().lower()
                if role_lower not in VALID_ARIA_ROLES:
                    issues.append(A11yIssue(
                        checker_name=self.NAME,
                        severity="ERROR",
                        description=f'Invalid ARIA role "{role}"',
                        element=str(elem),
                        wcag_criterion="4.1.2",
                    ))
                else:
                    # Check required aria-* attrs
                    required = REQUIRED_ARIA_ATTRS.get(role_lower, [])
                    for req_attr in required:
                        if req_attr not in elem.attrs:
                            issues.append(A11yIssue(
                                checker_name=self.NAME,
                                severity="ERROR",
                                description=f'Role "{role}" requires attribute "{req_attr}"',
                                element=str(elem),
                                wcag_criterion="4.1.2",
                            ))

            # Check aria-hidden on focusable elements
            if aria_hidden and aria_hidden.strip().lower() == "true":
                tag = elem.tag
                tabindex = elem.get("tabindex")
                # An element is focusable if it's in FOCUSABLE_TAGS
                # (a needs href to be focusable normally, but we check structurally)
                is_focusable = tag in FOCUSABLE_TAGS
                # Also check tabindex >= 0
                if tabindex is not None:
                    try:
                        if int(tabindex) >= 0:
                            is_focusable = True
                    except ValueError:
                        pass
                if is_focusable:
                    issues.append(A11yIssue(
                        checker_name=self.NAME,
                        severity="ERROR",
                        description=f'aria-hidden="true" on focusable <{tag}> element',
                        element=str(elem),
                        wcag_criterion="4.1.2",
                    ))

        return issues


class ContrastChecker:
    NAME = "ContrastChecker"

    NORMAL_TEXT_RATIO = 4.5
    LARGE_TEXT_RATIO = 3.0

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []

        for elem in parser.elements:
            style = elem.get("style", "")
            if not style:
                continue

            css = parse_inline_style(style)
            fg_str = css.get("color")
            bg_str = css.get("background-color")

            if not fg_str or not bg_str:
                continue

            fg = parse_color(fg_str)
            bg = parse_color(bg_str)

            if fg is None or bg is None:
                continue

            ratio = contrast_ratio(fg, bg)

            # Determine if large text (font-size >= 18pt or >= 14pt bold)
            font_size = css.get("font-size", "")
            font_weight = css.get("font-weight", "")
            is_large = False

            if font_size:
                # Check pt values
                m = re.match(r"([\d.]+)pt", font_size)
                if m:
                    pt = float(m.group(1))
                    is_large = pt >= 18 or (pt >= 14 and font_weight in ("bold", "700", "800", "900"))
                # Check px values (18pt ≈ 24px, 14pt ≈ 18.67px)
                m = re.match(r"([\d.]+)px", font_size)
                if m:
                    px = float(m.group(1))
                    is_large = px >= 24 or (px >= 18.67 and font_weight in ("bold", "700", "800", "900"))

            required_ratio = self.LARGE_TEXT_RATIO if is_large else self.NORMAL_TEXT_RATIO

            if ratio < required_ratio:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="ERROR",
                    description=(
                        f"Insufficient contrast ratio {ratio:.2f}:1 "
                        f"(required {required_ratio}:1) for "
                        f"color={fg_str} on background-color={bg_str}"
                    ),
                    element=str(elem),
                    wcag_criterion="1.4.3",
                ))

        return issues


class LangChecker:
    NAME = "LangChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []

        html_elem = parser.html_element
        # Also search elements list as fallback
        if html_elem is None:
            for e in parser.elements:
                if e.tag == "html":
                    html_elem = e
                    break

        if html_elem is None:
            issues.append(A11yIssue(
                checker_name=self.NAME,
                severity="ERROR",
                description="No <html> element found",
                element="<html>",
                wcag_criterion="3.1.1",
            ))
            return issues

        lang = html_elem.get("lang")
        if not lang or not lang.strip():
            issues.append(A11yIssue(
                checker_name=self.NAME,
                severity="ERROR",
                description='<html> element missing lang attribute',
                element=str(html_elem),
                wcag_criterion="3.1.1",
            ))

        return issues


NON_DESCRIPTIVE_LINK_TEXTS = {"click here", "here", "read more", "more", "link", "click"}


class LinkTextChecker:
    NAME = "LinkTextChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []

        for anchor in parser.get_elements_by_tag("a"):
            text = anchor.text.strip()
            # Collect all text content including children
            text = self._get_text_content(anchor, parser)

            if not text:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="ERROR",
                    description="Link has no text content",
                    element=str(anchor),
                    wcag_criterion="2.4.4",
                ))
            elif text.lower() in NON_DESCRIPTIVE_LINK_TEXTS:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="WARNING",
                    description=f'Link text "{text}" is not descriptive',
                    element=str(anchor),
                    wcag_criterion="2.4.4",
                ))

        return issues

    def _get_text_content(self, elem: ElementInfo, parser: A11yHTMLParser) -> str:
        """Get text from element and its children."""
        texts = [elem.text or ""]
        for child in elem.children:
            texts.append(self._get_text_content(child, parser))
        return " ".join(t for t in texts if t).strip()


class TableChecker:
    NAME = "TableChecker"

    def check(self, parser: A11yHTMLParser) -> list[A11yIssue]:
        issues: list[A11yIssue] = []

        for table in parser.get_elements_by_tag("table"):
            # Check if it's a data table (has <td> elements)
            # We check all td elements in the document that are descendants
            # For simplicity, we look at all td elements in parser
            tds = parser.get_elements_by_tag("td")
            ths = parser.get_elements_by_tag("th")

            if not tds:
                continue  # No data cells, skip

            # Check if there are th elements with scope
            th_with_scope = [th for th in ths if th.get("scope")]

            if not ths:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="ERROR",
                    description="Data table missing <th> elements",
                    element=str(table),
                    wcag_criterion="1.3.1",
                ))
            elif not th_with_scope:
                issues.append(A11yIssue(
                    checker_name=self.NAME,
                    severity="WARNING",
                    description="Table <th> elements missing scope attribute",
                    element=str(table),
                    wcag_criterion="1.3.1",
                ))

        return issues


# ---------------------------------------------------------------------------
# Combined runner
# ---------------------------------------------------------------------------

ALL_CHECKERS = [
    AltTextChecker,
    LabelChecker,
    HeadingOrderChecker,
    AriaChecker,
    ContrastChecker,
    LangChecker,
    LinkTextChecker,
    TableChecker,
]


def run_checks(html_content: str, checkers=None) -> A11yReport:
    """Run all (or specified) checkers on the given HTML content."""
    if checkers is None:
        checkers = [cls() for cls in ALL_CHECKERS]

    parser = parse_html(html_content)
    report = A11yReport()

    for checker in checkers:
        issues = checker.check(parser)
        for issue in issues:
            report.add(issue)

    return report


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------

DEFAULT_PORT = 19180

# Sample HTML pages for testing
SAMPLE_PAGES: dict[str, str] = {
    "/": """<!DOCTYPE html>
<html lang="en">
<head><title>Good Page</title></head>
<body>
  <h1>Welcome</h1>
  <h2>Section</h2>
  <img src="logo.png" alt="Company logo">
  <a href="/about">About us</a>
  <form>
    <label for="name">Name</label>
    <input id="name" type="text">
  </form>
  <table>
    <tr><th scope="col">Name</th><th scope="col">Age</th></tr>
    <tr><td>Alice</td><td>30</td></tr>
  </table>
</body>
</html>""",

    "/missing-alt": """<!DOCTYPE html>
<html lang="en">
<body>
  <img src="photo.jpg">
  <img src="icon.png" alt="">
  <img src="banner.gif" alt="image">
</body>
</html>""",

    "/bad-headings": """<!DOCTYPE html>
<html lang="en">
<body>
  <h1>Main Title</h1>
  <h3>Skipped h2</h3>
  <h1>Second h1</h1>
</body>
</html>""",

    "/no-lang": """<!DOCTYPE html>
<html>
<body><p>Hello</p></body>
</html>""",

    "/bad-links": """<!DOCTYPE html>
<html lang="en">
<body>
  <a href="/page1">Click here</a>
  <a href="/page2"></a>
  <a href="/page3">Read more</a>
</body>
</html>""",

    "/bad-contrast": """<!DOCTYPE html>
<html lang="en">
<body>
  <p style="color: #aaaaaa; background-color: #ffffff;">Low contrast text</p>
</body>
</html>""",

    "/bad-table": """<!DOCTYPE html>
<html lang="en">
<body>
  <table>
    <tr><td>Name</td><td>Age</td></tr>
    <tr><td>Alice</td><td>30</td></tr>
  </table>
</body>
</html>""",

    "/unlabeled-inputs": """<!DOCTYPE html>
<html lang="en">
<body>
  <form>
    <input type="text" id="email">
    <select id="country"><option>US</option></select>
    <textarea id="message"></textarea>
  </form>
</body>
</html>""",
}


class MockA11yHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving test HTML pages."""

    pages = SAMPLE_PAGES

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in self.pages:
            content = self.pages[path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args) -> None:  # type: ignore[override]
        pass  # Silence request logs during tests


class MockA11yServer:
    """Context manager wrapping the mock HTTP server."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.server = HTTPServer(("127.0.0.1", self.port), MockA11yHandler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.server:
            server = self.server
            server.shutdown()
            server.server_close()
            if self._thread:
                self._thread.join(timeout=5)
            self.server = None
            self._thread = None

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> MockA11yServer:
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


def find_free_port() -> int:
    """Find a free TCP port on localhost."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_self_test(verbose: bool = False) -> int:
    """Smoke self-test: deliberately inaccessible HTML must surface more
    ERROR-level issues than the bundled accessible sample. Exercises every
    checker through run_checks()."""
    bad = ('<html><body><img src="x.png">'
           '<a href="#"></a><input type="text"></body></html>')
    bad_rep = run_checks(bad)
    good_rep = run_checks(SAMPLE_PAGES["/"])
    bad_err, good_err = len(bad_rep.errors()), len(good_rep.errors())
    checks = [
        ("inaccessible HTML surfaces >=1 ERROR", bad_err >= 1, f"errors={bad_err}"),
        ("accessible sample has fewer ERRORs", good_err < bad_err, f"good={good_err} bad={bad_err}"),
    ]
    failures = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
    if verbose:
        print(f"  bad issues: {[(i.severity, i.checker_name) for i in bad_rep.issues]}")
    print(f"\n  {len(checks) - len(failures)}/{len(checks)} checks passed")
    return 0 if not failures else 1


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Accessibility (a11y) static-check harness")
    p.add_argument("file", nargs="?", help="HTML file to check")
    p.add_argument("--self-test", action="store_true", help="Run built-in scenarios and exit")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            content = f.read()
        report = run_checks(content)
        print(f"Found {len(report.issues)} issues:")
        for issue in report.issues:
            print(f"  [{issue.severity}] {issue.checker_name}: {issue.description}")
            print(f"    Element: {issue.element}")
            print(f"    WCAG: {issue.wcag_criterion}")
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
