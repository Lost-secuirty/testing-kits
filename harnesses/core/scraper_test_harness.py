"""
Web Scraper Test Harness
Harness 4 of 36 — tests web scraping reliability.
Pure stdlib, zero external dependencies.
Default mock server port: 18910
"""

import argparse
import html
import http.server
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# HTML parsing helpers (no external libs)
# ---------------------------------------------------------------------------

class SimpleHTMLParser:
    """Minimal HTML parser built on top of re / stdlib only."""

    def __init__(self, html_text: str):
        self.html = html_text

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_tags(text: str) -> str:
        """Remove all HTML tags and decode entities."""
        no_tags = re.sub(r'<[^>]+>', '', text)
        return html.unescape(no_tags)

    @staticmethod
    def _get_attr(tag_str: str, attr: str) -> Optional[str]:
        """Extract the value of *attr* from a raw tag string."""
        pattern = r'(?i)' + re.escape(attr) + r'\s*=\s*(?:"([^"]*?)"|\'([^\']*?)\'|([^\s>]+))'
        m = re.search(pattern, tag_str)
        if m:
            return m.group(1) or m.group(2) or m.group(3)
        return None

    # ------------------------------------------------------------------
    # Element finders
    # ------------------------------------------------------------------

    def find_all_tags(self, tag: str) -> List[str]:
        """Return list of full opening-tag strings for *tag*."""
        pattern = r'(?i)<' + re.escape(tag) + r'(?:\s[^>]*)?\s*/?>'
        return re.findall(pattern, self.html)

    def find_all_with_content(self, tag: str) -> List[Tuple[str, str]]:
        """Return list of (opening_tag, inner_text) for paired tags."""
        pattern = r'(?is)<(' + re.escape(tag) + r')(\s[^>]*)?>(.+?)</\1>'
        results = []
        for m in re.finditer(pattern, self.html):
            full_open = '<' + m.group(1) + (m.group(2) or '') + '>'
            inner = m.group(3)
            results.append((full_open, self._strip_tags(inner).strip()))
        return results

    # ------------------------------------------------------------------
    # Extract text content
    # ------------------------------------------------------------------

    def get_text(self) -> str:
        """Return visible text of the document."""
        # Remove <script> and <style> blocks
        cleaned = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', '', self.html)
        return self._strip_tags(cleaned).strip()

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------

    def get_links(self, base_url: str = '') -> List[Dict[str, str]]:
        """Return list of dicts with 'href' and 'text' for all <a> tags."""
        links = []
        for open_tag, text in self.find_all_with_content('a'):
            href = self._get_attr(open_tag, 'href') or ''
            if base_url and href and not href.startswith('http'):
                href = urllib.parse.urljoin(base_url, href)
            links.append({'href': href, 'text': text})
        # Also pick up <a href="..."/> (self-closing or no text)
        for tag_str in self.find_all_tags('a'):
            href = self._get_attr(tag_str, 'href') or ''
            if href and not any(l['href'] == (urllib.parse.urljoin(base_url, href) if (base_url and not href.startswith('http')) else href) for l in links):
                if base_url and not href.startswith('http'):
                    href = urllib.parse.urljoin(base_url, href)
                links.append({'href': href, 'text': ''})
        return links

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    def get_images(self, base_url: str = '') -> List[Dict[str, str]]:
        """Return list of dicts with 'src' and 'alt' for all <img> tags."""
        images = []
        for tag_str in self.find_all_tags('img'):
            src = self._get_attr(tag_str, 'src') or ''
            alt = self._get_attr(tag_str, 'alt') or ''
            if base_url and src and not src.startswith('http'):
                src = urllib.parse.urljoin(base_url, src)
            images.append({'src': src, 'alt': alt})
        return images

    # ------------------------------------------------------------------
    # Table extraction
    # ------------------------------------------------------------------

    def get_tables(self) -> List[List[List[str]]]:
        """Return list of tables; each table is a list of rows; each row is a list of cell strings."""
        tables = []
        table_pattern = r'(?is)<table[^>]*>(.*?)</table>'
        tr_pattern = r'(?is)<tr[^>]*>(.*?)</tr>'
        cell_pattern = r'(?is)<t[dh][^>]*>(.*?)</t[dh]>'
        for table_m in re.finditer(table_pattern, self.html):
            table_html = table_m.group(1)
            rows = []
            for row_m in re.finditer(tr_pattern, table_html):
                row_html = row_m.group(1)
                cells = [self._strip_tags(c.group(1)).strip() for c in re.finditer(cell_pattern, row_html)]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return tables


# ---------------------------------------------------------------------------
# CSS-selector-like extraction (tag, #id, .class)
# ---------------------------------------------------------------------------

class SelectorValidator:
    """Validates and executes simple CSS selectors against HTML."""

    SUPPORTED_SELECTORS = ['tag', 'id', 'class', 'tag.class', 'tag#id']

    def __init__(self, html_text: str):
        self.html = html_text
        self._parser = SimpleHTMLParser(html_text)

    def select(self, selector: str) -> List[Tuple[str, str]]:
        """
        Evaluate *selector* and return list of (open_tag, inner_text).
        Supported forms:
          tag         — e.g. "div"
          #id         — elements with id="id"
          .class      — elements with that class
          tag#id      — tag with id
          tag.class   — tag with class
        """
        selector = selector.strip()

        # Compound: tag#id
        m = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9]*)#([a-zA-Z_-][a-zA-Z0-9_-]*)', selector)
        if m:
            return self._by_tag_and_attr(m.group(1), 'id', m.group(2))

        # Compound: tag.class
        m = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9]*)\.([a-zA-Z_-][a-zA-Z0-9_-]*)', selector)
        if m:
            return self._by_tag_and_class(m.group(1), m.group(2))

        # #id
        m = re.fullmatch(r'#([a-zA-Z_-][a-zA-Z0-9_-]*)', selector)
        if m:
            return self._by_attr_any_tag('id', m.group(1))

        # .class
        m = re.fullmatch(r'\.([a-zA-Z_-][a-zA-Z0-9_-]*)', selector)
        if m:
            return self._by_class_any_tag(m.group(1))

        # tag
        m = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9]*)', selector)
        if m:
            return self._parser.find_all_with_content(m.group(1))

        raise ValueError(f"Unsupported selector: {selector!r}")

    def _by_tag_and_attr(self, tag: str, attr: str, value: str) -> List[Tuple[str, str]]:
        results = []
        for open_tag, text in self._parser.find_all_with_content(tag):
            v = SimpleHTMLParser._get_attr(open_tag, attr)
            if v == value:
                results.append((open_tag, text))
        return results

    def _by_tag_and_class(self, tag: str, cls: str) -> List[Tuple[str, str]]:
        results = []
        for open_tag, text in self._parser.find_all_with_content(tag):
            classes = (SimpleHTMLParser._get_attr(open_tag, 'class') or '').split()
            if cls in classes:
                results.append((open_tag, text))
        return results

    def _by_attr_any_tag(self, attr: str, value: str) -> List[Tuple[str, str]]:
        # Try common block/inline tags
        results = []
        seen = set()
        for tag in ['div', 'span', 'p', 'section', 'article', 'header', 'footer',
                    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a',
                    'table', 'tr', 'td', 'th', 'form', 'input', 'button', 'nav', 'main']:
            for open_tag, text in self._parser.find_all_with_content(tag):
                v = SimpleHTMLParser._get_attr(open_tag, attr)
                key = (open_tag, text)
                if v == value and key not in seen:
                    results.append((open_tag, text))
                    seen.add(key)
        return results

    def _by_class_any_tag(self, cls: str) -> List[Tuple[str, str]]:
        results = []
        seen = set()
        for tag in ['div', 'span', 'p', 'section', 'article', 'header', 'footer',
                    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a',
                    'table', 'tr', 'td', 'th', 'form', 'button', 'nav', 'main']:
            for open_tag, text in self._parser.find_all_with_content(tag):
                classes = (SimpleHTMLParser._get_attr(open_tag, 'class') or '').split()
                key = (open_tag, text)
                if cls in classes and key not in seen:
                    results.append((open_tag, text))
                    seen.add(key)
        return results

    def validate_selector(self, selector: str) -> bool:
        """Return True if *selector* is a recognised form."""
        try:
            self.select(selector)
            return True
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# Pagination tester
# ---------------------------------------------------------------------------

class PaginationTester:
    """Follows 'next page' links and collects content across pages."""

    NEXT_PATTERNS = [
        r'(?i)\bnext\b',
        r'(?i)\bnext\s+page\b',
        r'(?i)›',
        r'(?i)»',
        r'(?i)\bforward\b',
    ]

    def __init__(self, base_url: str, max_pages: int = 20, delay: float = 0.0):
        self.base_url = base_url
        self.max_pages = max_pages
        self.delay = delay

    def _find_next_link(self, html_text: str, current_url: str) -> Optional[str]:
        parser = SimpleHTMLParser(html_text)
        links = parser.get_links(base_url=current_url)
        for link in links:
            text = link.get('text', '')
            rel = SimpleHTMLParser._get_attr(link.get('_raw', ''), 'rel') or ''
            # Check link text
            for pat in self.NEXT_PATTERNS:
                if re.search(pat, text):
                    return link['href']
        # Also look for <link rel="next" href="...">
        m = re.search(r'(?i)<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', html_text)
        if m:
            href = m.group(1)
            return urllib.parse.urljoin(current_url, href)
        m = re.search(r'(?i)<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']next["\']', html_text)
        if m:
            href = m.group(1)
            return urllib.parse.urljoin(current_url, href)
        return None

    def crawl(self, start_url: str) -> List[Dict]:
        """Follow pagination starting at *start_url*. Return list of page dicts."""
        pages = []
        url = start_url
        visited = set()
        while url and len(pages) < self.max_pages:
            if url in visited:
                break
            visited.add(url)
            try:
                resp = _http_get(url)
                body = resp['body']
                pages.append({'url': url, 'status': resp['status'], 'body': body})
                if self.delay:
                    time.sleep(self.delay)
                next_url = self._find_next_link(body, url)
                url = next_url
            except Exception as exc:
                pages.append({'url': url, 'status': -1, 'error': str(exc)})
                break
        return pages


# ---------------------------------------------------------------------------
# Rate limit checker
# ---------------------------------------------------------------------------

class RateLimitChecker:
    """
    Enforces a minimum delay between outgoing requests.
    Tracks request timestamps and raises if the caller is going too fast.
    """

    def __init__(self, min_delay: float = 1.0):
        self.min_delay = min_delay
        self._lock = threading.Lock()
        self._last_request_time: Optional[float] = None
        self.request_times: List[float] = []

    def wait_if_needed(self):
        """Block until the minimum delay has elapsed since the last request."""
        with self._lock:
            now = time.monotonic()
            if self._last_request_time is not None:
                elapsed = now - self._last_request_time
                if elapsed < self.min_delay:
                    time.sleep(self.min_delay - elapsed)
            self._last_request_time = time.monotonic()
            self.request_times.append(self._last_request_time)

    def check_compliance(self) -> bool:
        """Return True if all recorded intervals respect min_delay (with 10% tolerance)."""
        times = self.request_times
        if len(times) < 2:
            return True
        tolerance = self.min_delay * 0.10
        for i in range(1, len(times)):
            interval = times[i] - times[i - 1]
            if interval < self.min_delay - tolerance:
                return False
        return True

    def get_average_rate(self) -> float:
        """Return average requests-per-second over all recorded requests."""
        times = self.request_times
        if len(times) < 2:
            return 0.0
        total_time = times[-1] - times[0]
        if total_time <= 0:
            return 0.0
        return (len(times) - 1) / total_time

    def fetch(self, url: str) -> Dict:
        """Rate-limited HTTP GET."""
        self.wait_if_needed()
        return _http_get(url)


# ---------------------------------------------------------------------------
# robots.txt parser
# ---------------------------------------------------------------------------

class RobotsTxtParser:
    """Parse and query robots.txt rules."""

    def __init__(self, content: str, user_agent: str = '*'):
        self.user_agent = user_agent
        self._rules: Dict[str, List[Tuple[str, str]]] = {}  # agent -> [(allow/disallow, path)]
        self._crawl_delay: Dict[str, Optional[float]] = {}
        self._sitemaps: List[str] = []
        self._parse(content)

    def _parse(self, content: str):
        current_agents: List[str] = []
        for raw_line in content.splitlines():
            line = raw_line.split('#', 1)[0].strip()
            if not line:
                if current_agents:
                    current_agents = []
                continue
            if ':' not in line:
                continue
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip()
            if key == 'user-agent':
                current_agents.append(value.lower())
                self._rules.setdefault(value.lower(), [])
            elif key in ('disallow', 'allow') and current_agents:
                for agent in current_agents:
                    self._rules.setdefault(agent, []).append((key, value))
            elif key == 'crawl-delay' and current_agents:
                try:
                    delay = float(value)
                    for agent in current_agents:
                        self._crawl_delay[agent] = delay
                except ValueError:
                    pass
            elif key == 'sitemap':
                self._sitemaps.append(value)

    def _get_rules(self) -> List[Tuple[str, str]]:
        """Get rules for our user-agent (fall back to '*')."""
        ua = self.user_agent.lower()
        if ua in self._rules:
            return self._rules[ua]
        return self._rules.get('*', [])

    def is_allowed(self, path: str) -> bool:
        """Return True if the given path is allowed for our user-agent."""
        rules = self._get_rules()
        if not rules:
            return True
        # Longer (more specific) paths win; allow beats disallow on tie
        best_len = -1
        best_allowed = True
        for directive, pattern in rules:
            if not pattern:
                # empty disallow => allow all
                if directive == 'disallow':
                    continue
            if path.startswith(pattern) or (pattern and re.match(
                    re.escape(pattern).replace(r'\*', '.*').replace(r'\$', '$'), path)):
                if len(pattern) > best_len:
                    best_len = len(pattern)
                    best_allowed = (directive == 'allow')
                elif len(pattern) == best_len and directive == 'allow':
                    best_allowed = True
        return best_allowed

    def get_crawl_delay(self) -> Optional[float]:
        ua = self.user_agent.lower()
        if ua in self._crawl_delay:
            return self._crawl_delay[ua]
        return self._crawl_delay.get('*')

    @property
    def sitemaps(self) -> List[str]:
        return self._sitemaps


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 10, follow_redirects: bool = True,
              headers: Optional[Dict[str, str]] = None) -> Dict:
    """
    Perform an HTTP GET.  Returns dict with keys:
      status, body, headers, final_url, redirect_chain
    """
    redirect_chain = []
    current_url = url
    max_redirects = 10

    req_headers = {'User-Agent': 'ScraperTestHarness/1.0'}
    if headers:
        req_headers.update(headers)

    for _ in range(max_redirects + 1):
        req = urllib.request.Request(current_url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body_bytes = resp.read()
                charset = 'utf-8'
                ct = resp.headers.get('Content-Type', '')
                m = re.search(r'charset=([^\s;]+)', ct)
                if m:
                    charset = m.group(1)
                try:
                    body = body_bytes.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    body = body_bytes.decode('utf-8', errors='replace')
                return {
                    'status': resp.status,
                    'body': body,
                    'headers': dict(resp.headers),
                    'final_url': resp.url,
                    'redirect_chain': redirect_chain,
                }
        except urllib.error.HTTPError as e:
            return {
                'status': e.code,
                'body': e.read().decode('utf-8', errors='replace'),
                'headers': dict(e.headers) if e.headers else {},
                'final_url': current_url,
                'redirect_chain': redirect_chain,
                'error': str(e),
            }
        except urllib.error.URLError as e:
            # May be a redirect that urllib didn't follow (shouldn't happen normally)
            raise

    raise RuntimeError(f"Too many redirects for {url}")


def _http_get_no_follow(url: str, timeout: int = 10) -> Dict:
    """HTTP GET without following redirects, returns raw status + Location."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # don't follow

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={'User-Agent': 'ScraperTestHarness/1.0'})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return {
                'status': resp.status,
                'body': resp.read().decode('utf-8', errors='replace'),
                'headers': dict(resp.headers),
                'final_url': resp.url,
                'redirect_chain': [],
            }
    except urllib.error.HTTPError as e:
        return {
            'status': e.code,
            'body': '',
            'headers': dict(e.headers) if e.headers else {},
            'final_url': url,
            'redirect_chain': [],
            'location': e.headers.get('Location', '') if e.headers else '',
        }


# ---------------------------------------------------------------------------
# Error recovery wrapper
# ---------------------------------------------------------------------------

class ErrorRecoveryFetcher:
    """Retries on 5xx, skips on 404, raises on other errors."""

    def __init__(self, max_retries: int = 3, retry_delay: float = 0.1):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.skipped_urls: List[str] = []
        self.retry_counts: Dict[str, int] = {}

    def fetch(self, url: str) -> Optional[Dict]:
        attempts = 0
        while attempts <= self.max_retries:
            try:
                result = _http_get(url)
                status = result['status']
                if status == 404:
                    self.skipped_urls.append(url)
                    return None
                if 500 <= status < 600:
                    attempts += 1
                    self.retry_counts[url] = attempts
                    if attempts <= self.max_retries:
                        time.sleep(self.retry_delay)
                    continue
                return result
            except Exception:
                attempts += 1
                self.retry_counts[url] = attempts
                if attempts <= self.max_retries:
                    time.sleep(self.retry_delay)
                else:
                    raise
        return None  # exhausted retries on 5xx


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------

# HTML content served by the mock server
_PAGE1_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Test Page 1</title></head>
<body>
  <h1 id="main-title">Welcome to Page 1</h1>
  <p class="intro">This is the introduction paragraph.</p>
  <p class="content">Some content here with <a href="/page2">next page</a> link.</p>
  <img src="/images/logo.png" alt="Logo" />
  <img src="/images/banner.jpg" alt="Banner" />
  <nav>
    <a href="/">Home</a>
    <a href="/about">About</a>
    <a href="/page2">Next</a>
  </nav>
  <div class="pagination">
    <a href="/page2" class="next">Next</a>
  </div>
</body>
</html>
"""

_PAGE2_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Test Page 2</title></head>
<body>
  <h1>Page 2</h1>
  <p class="content">Page 2 content.</p>
  <div class="pagination">
    <a href="/page1">Previous</a>
    <a href="/page3" class="next">Next</a>
  </div>
</body>
</html>
"""

_PAGE3_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Test Page 3 - Last</title></head>
<body>
  <h1>Page 3 (Last)</h1>
  <p class="content">Final page content.</p>
  <div class="pagination">
    <a href="/page2">Previous</a>
  </div>
</body>
</html>
"""

_TABLE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Table Data</title></head>
<body>
  <h1>Product Table</h1>
  <table id="products">
    <tr><th>Name</th><th>Price</th><th>Stock</th></tr>
    <tr><td>Widget A</td><td>$10.00</td><td>50</td></tr>
    <tr><td>Widget B</td><td>$20.00</td><td>30</td></tr>
    <tr><td>Gadget X</td><td>$15.00</td><td>100</td></tr>
  </table>
</body>
</html>
"""

_ROBOTS_TXT = """\
User-agent: *
Disallow: /private/
Disallow: /admin/
Allow: /public/
Crawl-delay: 1

User-agent: BadBot
Disallow: /

Sitemap: http://example.com/sitemap.xml
"""

_ERROR_HTML = """\
<!DOCTYPE html>
<html>
<body><h1>Error Page</h1><p>Something went wrong.</p></body>
</html>
"""

_SELECTOR_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Selector Test</title></head>
<body>
  <div id="header">Header Content</div>
  <div class="highlight">Highlighted div 1</div>
  <div class="highlight">Highlighted div 2</div>
  <span class="note">A note span</span>
  <p id="intro">Introduction paragraph</p>
  <p class="body-text">Body paragraph 1</p>
  <p class="body-text">Body paragraph 2</p>
  <h2 class="section-title">Section A</h2>
  <h2 class="section-title">Section B</h2>
</body>
</html>
"""


class MockScraperHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the scraper test mock server."""

    # Track 5xx hit counts per path for error-recovery tests
    _error_hit_counts: Dict[str, int] = {}
    _error_hit_lock = threading.Lock()

    def log_message(self, format, *args):
        pass  # Suppress output

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Route requests
        if path == '/robots.txt':
            self._send_text(_ROBOTS_TXT, content_type='text/plain')
        elif path in ('/', '/page1'):
            self._send_html(_PAGE1_HTML)
        elif path == '/page2':
            self._send_html(_PAGE2_HTML)
        elif path == '/page3':
            self._send_html(_PAGE3_HTML)
        elif path == '/table':
            self._send_html(_TABLE_HTML)
        elif path == '/selectors':
            self._send_html(_SELECTOR_HTML)
        elif path == '/redirect301':
            self._send_redirect(301, '/page1')
        elif path == '/redirect302':
            self._send_redirect(302, '/page1')
        elif path == '/redirect-chain':
            self._send_redirect(301, '/redirect302')
        elif path == '/not-found':
            self._send_error_response(404, 'Not Found')
        elif path == '/server-error':
            # Return 500 on first 2 hits, then 200
            with self._error_hit_lock:
                count = self._error_hit_counts.get(path, 0)
                self._error_hit_counts[path] = count + 1
            if count < 2:
                self._send_error_response(500, 'Internal Server Error')
            else:
                self._send_html('<html><body><p>Recovered</p></body></html>')
        elif path == '/always-500':
            self._send_error_response(500, 'Internal Server Error')
        elif path == '/private/secret':
            self._send_html('<html><body><p>Secret</p></body></html>')
        elif path == '/public/info':
            self._send_html('<html><body><p>Public Info</p></body></html>')
        elif path == '/slow':
            time.sleep(0.05)
            self._send_html('<html><body><p>Slow response</p></body></html>')
        elif path == '/rate-test':
            self._send_html('<html><body><p>Rate test page</p></body></html>')
        elif path == '/empty':
            self._send_html('<html><body></body></html>')
        elif path == '/encoding':
            body = '<html><body><p>Hello &amp; World &lt;test&gt;</p></body></html>'
            self._send_html(body)
        else:
            self._send_error_response(404, 'Not Found')

    def _send_html(self, body: str, status: int = 200):
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, body: str, status: int = 200, content_type: str = 'text/plain'):
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_redirect(self, code: int, location: str):
        self.send_response(code)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _send_error_response(self, code: int, message: str):
        data = f'<html><body><h1>{code} {message}</h1></body></html>'.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_mock_server(port: int = 18910) -> Tuple[HTTPServer, str]:
    """
    Start the mock HTTP server on *port* in a daemon thread.
    Returns (server, base_url).
    """
    # Reset error hit counts for fresh test runs
    MockScraperHandler._error_hit_counts.clear()
    server = HTTPServer(('127.0.0.1', port), MockScraperHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{port}'
    return server, base_url


def find_free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

class ScraperTestRunner:
    """Orchestrates all scraper tests against the mock server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.results: List[Dict] = []

    def _record(self, name: str, passed: bool, detail: str = ''):
        self.results.append({'name': name, 'passed': passed, 'detail': detail})
        status = 'PASS' if passed else 'FAIL'
        print(f'  [{status}] {name}' + (f' — {detail}' if detail else ''))

    def run_all(self) -> bool:
        print('=== Scraper Test Harness ===')
        self._test_html_parsing()
        self._test_css_selectors()
        self._test_pagination()
        self._test_redirects()
        self._test_rate_limiting()
        self._test_robots_txt()
        self._test_error_recovery()
        self._test_table_extraction()
        total = len(self.results)
        passed = sum(1 for r in self.results if r['passed'])
        print(f'\nResults: {passed}/{total} passed')
        return passed == total

    # ------------------------------------------------------------------
    # HTML parsing tests
    # ------------------------------------------------------------------

    def _test_html_parsing(self):
        print('\n--- HTML Parsing ---')
        resp = _http_get(f'{self.base_url}/page1')
        parser = SimpleHTMLParser(resp['body'])

        # Text extraction
        text = parser.get_text()
        self._record('parse_get_text', 'Welcome to Page 1' in text, f'text snippet: {text[:60]!r}')

        # Link extraction
        links = parser.get_links(f'{self.base_url}/page1')
        hrefs = [l['href'] for l in links]
        self._record('parse_links_found', len(links) >= 3, f'found {len(links)} links')
        self._record('parse_link_has_next', any('/page2' in h for h in hrefs), f'hrefs={hrefs}')

        # Image extraction
        images = parser.get_images(f'{self.base_url}/page1')
        self._record('parse_images_found', len(images) == 2, f'found {len(images)} images')
        alts = [i['alt'] for i in images]
        self._record('parse_image_alt', 'Logo' in alts, f'alts={alts}')

        # Entity decoding
        resp2 = _http_get(f'{self.base_url}/encoding')
        parser2 = SimpleHTMLParser(resp2['body'])
        text2 = parser2.get_text()
        self._record('parse_entity_decode', 'Hello & World' in text2, f'text={text2!r}')

    # ------------------------------------------------------------------
    # CSS-selector-like tests
    # ------------------------------------------------------------------

    def _test_css_selectors(self):
        print('\n--- CSS Selectors ---')
        resp = _http_get(f'{self.base_url}/selectors')
        sv = SelectorValidator(resp['body'])

        # By tag
        divs = sv.select('div')
        self._record('selector_by_tag', len(divs) >= 2, f'found {len(divs)} divs')

        # By id
        header = sv.select('#header')
        self._record('selector_by_id', len(header) == 1 and 'Header Content' in header[0][1],
                     f'results={header}')

        # By class
        highlights = sv.select('.highlight')
        self._record('selector_by_class', len(highlights) == 2,
                     f'found {len(highlights)} .highlight')

        # By tag+id
        p_intro = sv.select('p#intro')
        self._record('selector_tag_id', len(p_intro) == 1 and 'Introduction' in p_intro[0][1],
                     f'results={p_intro}')

        # By tag+class
        body_texts = sv.select('p.body-text')
        self._record('selector_tag_class', len(body_texts) == 2,
                     f'found {len(body_texts)} p.body-text')

        # Validate selector
        self._record('selector_validate_valid', sv.validate_selector('div.highlight'))
        self._record('selector_validate_invalid', not sv.validate_selector('>>bad<<'))

        # Section titles
        titles = sv.select('h2.section-title')
        self._record('selector_h2_class', len(titles) == 2, f'found {len(titles)} h2.section-title')

    # ------------------------------------------------------------------
    # Pagination tests
    # ------------------------------------------------------------------

    def _test_pagination(self):
        print('\n--- Pagination ---')
        tester = PaginationTester(self.base_url, max_pages=10)
        pages = tester.crawl(f'{self.base_url}/page1')

        self._record('pagination_crawled_3_pages', len(pages) == 3, f'got {len(pages)} pages')
        urls = [p['url'] for p in pages]
        self._record('pagination_page1_visited', any('page1' in u or u.endswith('/') for u in urls),
                     f'urls={urls}')
        self._record('pagination_page2_visited', any('page2' in u for u in urls), f'urls={urls}')
        self._record('pagination_page3_visited', any('page3' in u for u in urls), f'urls={urls}')
        self._record('pagination_all_200', all(p['status'] == 200 for p in pages),
                     f'statuses={[p["status"] for p in pages]}')

        # Max pages limit
        tester2 = PaginationTester(self.base_url, max_pages=2)
        pages2 = tester2.crawl(f'{self.base_url}/page1')
        self._record('pagination_max_pages', len(pages2) <= 2, f'got {len(pages2)} pages')

    # ------------------------------------------------------------------
    # Redirect tests
    # ------------------------------------------------------------------

    def _test_redirects(self):
        print('\n--- Redirect Handling ---')
        # 301
        resp301 = _http_get(f'{self.base_url}/redirect301')
        self._record('redirect_301_followed', resp301['status'] == 200,
                     f'status={resp301["status"]}')
        self._record('redirect_301_content', 'Welcome to Page 1' in resp301['body'],
                     f'body snippet={resp301["body"][:80]!r}')

        # 302
        resp302 = _http_get(f'{self.base_url}/redirect302')
        self._record('redirect_302_followed', resp302['status'] == 200,
                     f'status={resp302["status"]}')

        # Chain of redirects
        resp_chain = _http_get(f'{self.base_url}/redirect-chain')
        self._record('redirect_chain_followed', resp_chain['status'] == 200,
                     f'status={resp_chain["status"]}')

        # Raw redirect response (no follow)
        raw = _http_get_no_follow(f'{self.base_url}/redirect301')
        self._record('redirect_raw_status', raw['status'] == 301,
                     f'status={raw["status"]}')
        loc = raw.get('location', '') or raw.get('headers', {}).get('Location', '')
        self._record('redirect_has_location', bool(loc), f'location={loc!r}')

    # ------------------------------------------------------------------
    # Rate-limiting tests
    # ------------------------------------------------------------------

    def _test_rate_limiting(self):
        print('\n--- Rate Limiting ---')
        checker = RateLimitChecker(min_delay=0.05)
        urls = [f'{self.base_url}/rate-test'] * 4
        for url in urls:
            checker.fetch(url)

        self._record('rate_limit_compliance', checker.check_compliance(),
                     f'times={[round(t - checker.request_times[0], 3) for t in checker.request_times]}')
        self._record('rate_limit_request_count', len(checker.request_times) == 4,
                     f'count={len(checker.request_times)}')

        # Average rate should be <= 1/min_delay requests/sec
        avg = checker.get_average_rate()
        max_rate = 1.0 / checker.min_delay
        self._record('rate_limit_avg_rate', avg <= max_rate * 1.2,
                     f'avg={avg:.2f} req/s, max={max_rate:.2f} req/s')

    # ------------------------------------------------------------------
    # robots.txt tests
    # ------------------------------------------------------------------

    def _test_robots_txt(self):
        print('\n--- robots.txt ---')
        resp = _http_get(f'{self.base_url}/robots.txt')
        self._record('robots_fetch_ok', resp['status'] == 200,
                     f'status={resp["status"]}')

        rp = RobotsTxtParser(resp['body'])
        self._record('robots_disallow_private', not rp.is_allowed('/private/'),
                     'should disallow /private/')
        self._record('robots_disallow_admin', not rp.is_allowed('/admin/'),
                     'should disallow /admin/')
        self._record('robots_allow_public', rp.is_allowed('/public/info'),
                     'should allow /public/info')
        self._record('robots_allow_root', rp.is_allowed('/'),
                     'should allow /')
        self._record('robots_crawl_delay', rp.get_crawl_delay() == 1.0,
                     f'delay={rp.get_crawl_delay()}')
        self._record('robots_sitemap', len(rp.sitemaps) == 1,
                     f'sitemaps={rp.sitemaps}')

        # Bad bot
        rp_bad = RobotsTxtParser(resp['body'], user_agent='BadBot')
        self._record('robots_badbot_disallow_all', not rp_bad.is_allowed('/'),
                     'BadBot should be disallowed everywhere')

    # ------------------------------------------------------------------
    # Error recovery tests
    # ------------------------------------------------------------------

    def _test_error_recovery(self):
        print('\n--- Error Recovery ---')
        fetcher = ErrorRecoveryFetcher(max_retries=3, retry_delay=0.05)

        # 404 should be skipped (return None)
        result_404 = fetcher.fetch(f'{self.base_url}/not-found')
        self._record('error_404_skipped', result_404 is None,
                     f'result={result_404}')
        self._record('error_404_in_skipped', f'{self.base_url}/not-found' in fetcher.skipped_urls,
                     f'skipped={fetcher.skipped_urls}')

        # /server-error returns 500 twice then 200 — should recover
        MockScraperHandler._error_hit_counts['/server-error'] = 0
        result_5xx = fetcher.fetch(f'{self.base_url}/server-error')
        self._record('error_5xx_recovered', result_5xx is not None and result_5xx['status'] == 200,
                     f'result={result_5xx}')
        retries = fetcher.retry_counts.get(f'{self.base_url}/server-error', 0)
        self._record('error_5xx_retried', retries > 0, f'retries={retries}')

        # /always-500 should exhaust retries
        fetcher2 = ErrorRecoveryFetcher(max_retries=2, retry_delay=0.02)
        result_always = fetcher2.fetch(f'{self.base_url}/always-500')
        self._record('error_always500_exhausted', result_always is None,
                     f'result={result_always}')

    # ------------------------------------------------------------------
    # Table extraction tests
    # ------------------------------------------------------------------

    def _test_table_extraction(self):
        print('\n--- Table Extraction ---')
        resp = _http_get(f'{self.base_url}/table')
        parser = SimpleHTMLParser(resp['body'])
        tables = parser.get_tables()

        self._record('table_found', len(tables) == 1, f'found {len(tables)} tables')
        if tables:
            table = tables[0]
            self._record('table_row_count', len(table) == 4, f'rows={len(table)}')
            header_row = table[0]
            self._record('table_header_name', 'Name' in header_row, f'header={header_row}')
            self._record('table_header_price', 'Price' in header_row, f'header={header_row}')
            self._record('table_data_widget_a', any('Widget A' in cell for row in table for cell in row),
                         f'table={table}')
            self._record('table_data_price', any('$10.00' in cell for row in table for cell in row),
                         f'table={table}')
            self._record('table_data_stock', any('50' in cell for row in table for cell in row),
                         f'table={table}')


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _self_test(port: int = 18910) -> bool:
    """Run all tests against the built-in mock server."""
    try:
        server, base_url = start_mock_server(port)
    except OSError:
        # Port in use — try a free one
        port = find_free_port()
        server, base_url = start_mock_server(port)

    try:
        runner = ScraperTestRunner(base_url)
        return runner.run_all()
    finally:
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description='Web Scraper Test Harness')
    parser.add_argument('--self-test', action='store_true',
                        help='Run built-in self-test suite against mock server')
    parser.add_argument('--port', type=int, default=18910,
                        help='Mock server port (default: 18910)')
    parser.add_argument('--url', type=str, default='',
                        help='Run tests against a custom base URL instead of mock server')
    args = parser.parse_args()

    if args.url:
        runner = ScraperTestRunner(args.url)
        success = runner.run_all()
    else:
        success = _self_test(args.port)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
