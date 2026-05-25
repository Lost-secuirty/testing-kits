"""
Unit tests for scraper_test_harness.py
46 tests covering all major components.
"""

import threading
import time
import unittest
import urllib.parse

from scraper_test_harness import (
    ErrorRecoveryFetcher,
    MockScraperHandler,
    PaginationTester,
    RateLimitChecker,
    RobotsTxtParser,
    ScraperTestRunner,
    SelectorValidator,
    SimpleHTMLParser,
    _http_get,
    _http_get_no_follow,
    find_free_port,
    start_mock_server,
)


def setUpModule():
    """Start a single mock server for the entire test module."""
    global _server, _base_url
    port = find_free_port()
    _server, _base_url = start_mock_server(port)


def tearDownModule():
    """Shut down the module-level mock server."""
    global _server
    _server.shutdown()


# ---------------------------------------------------------------------------
# SimpleHTMLParser tests
# ---------------------------------------------------------------------------

class TestSimpleHTMLParser(unittest.TestCase):

    _HTML = """
    <!DOCTYPE html>
    <html>
    <head><title>Test</title></head>
    <body>
      <h1 id="title">Hello World</h1>
      <p class="intro">Welcome to <strong>testing</strong>.</p>
      <a href="/page2">Next Page</a>
      <a href="https://example.com">External</a>
      <img src="/logo.png" alt="Logo" />
      <img src="/banner.jpg" alt="Banner Image" />
      <script>var x = 1;</script>
      <p>Hello &amp; World &lt;test&gt;</p>
    </body>
    </html>
    """

    def setUp(self):
        self.parser = SimpleHTMLParser(self._HTML)

    def test_get_text_contains_heading(self):
        text = self.parser.get_text()
        self.assertIn('Hello World', text)

    def test_get_text_strips_tags(self):
        text = self.parser.get_text()
        self.assertNotIn('<h1', text)
        self.assertNotIn('<p', text)

    def test_get_text_excludes_script(self):
        text = self.parser.get_text()
        self.assertNotIn('var x = 1', text)

    def test_get_text_entity_decode(self):
        text = self.parser.get_text()
        self.assertIn('Hello & World', text)
        self.assertIn('<test>', text)

    def test_get_links_count(self):
        links = self.parser.get_links()
        self.assertGreaterEqual(len(links), 2)

    def test_get_links_hrefs(self):
        links = self.parser.get_links()
        hrefs = [l['href'] for l in links]
        self.assertIn('/page2', hrefs)
        self.assertIn('https://example.com', hrefs)

    def test_get_links_with_base_url(self):
        links = self.parser.get_links(base_url='http://test.local')
        hrefs = [l['href'] for l in links]
        self.assertIn('http://test.local/page2', hrefs)

    def test_get_links_text(self):
        links = self.parser.get_links()
        texts = [l['text'] for l in links]
        self.assertIn('Next Page', texts)

    def test_get_images_count(self):
        images = self.parser.get_images()
        self.assertEqual(len(images), 2)

    def test_get_images_src(self):
        images = self.parser.get_images()
        srcs = [i['src'] for i in images]
        self.assertIn('/logo.png', srcs)
        self.assertIn('/banner.jpg', srcs)

    def test_get_images_alt(self):
        images = self.parser.get_images()
        alts = [i['alt'] for i in images]
        self.assertIn('Logo', alts)
        self.assertIn('Banner Image', alts)

    def test_get_images_with_base_url(self):
        images = self.parser.get_images(base_url='http://test.local')
        srcs = [i['src'] for i in images]
        self.assertIn('http://test.local/logo.png', srcs)

    def test_get_tables_with_table_html(self):
        html = """
        <table>
          <tr><th>A</th><th>B</th></tr>
          <tr><td>1</td><td>2</td></tr>
        </table>
        """
        parser = SimpleHTMLParser(html)
        tables = parser.get_tables()
        self.assertEqual(len(tables), 1)
        self.assertEqual(len(tables[0]), 2)  # 2 rows

    def test_get_tables_cell_content(self):
        html = """
        <table>
          <tr><th>Name</th><th>Price</th></tr>
          <tr><td>Widget</td><td>$5.00</td></tr>
        </table>
        """
        parser = SimpleHTMLParser(html)
        tables = parser.get_tables()
        self.assertEqual(tables[0][0], ['Name', 'Price'])
        self.assertEqual(tables[0][1], ['Widget', '$5.00'])


# ---------------------------------------------------------------------------
# SelectorValidator tests
# ---------------------------------------------------------------------------

class TestSelectorValidator(unittest.TestCase):

    _HTML = """
    <html><body>
      <div id="main">Main Content</div>
      <div class="box">Box 1</div>
      <div class="box">Box 2</div>
      <p id="lead">Lead paragraph</p>
      <p class="note">Note 1</p>
      <p class="note">Note 2</p>
      <span class="tag">Tag A</span>
    </body></html>
    """

    def setUp(self):
        self.sv = SelectorValidator(self._HTML)

    def test_select_by_tag(self):
        results = self.sv.select('div')
        self.assertGreaterEqual(len(results), 3)

    def test_select_by_id(self):
        results = self.sv.select('#main')
        self.assertEqual(len(results), 1)
        self.assertIn('Main Content', results[0][1])

    def test_select_by_class(self):
        results = self.sv.select('.box')
        self.assertEqual(len(results), 2)

    def test_select_by_tag_and_id(self):
        results = self.sv.select('p#lead')
        self.assertEqual(len(results), 1)
        self.assertIn('Lead paragraph', results[0][1])

    def test_select_by_tag_and_class(self):
        results = self.sv.select('p.note')
        self.assertEqual(len(results), 2)

    def test_select_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.sv.select('>>invalid<<')

    def test_validate_selector_valid(self):
        self.assertTrue(self.sv.validate_selector('div'))
        self.assertTrue(self.sv.validate_selector('#main'))
        self.assertTrue(self.sv.validate_selector('.box'))
        self.assertTrue(self.sv.validate_selector('p#lead'))
        self.assertTrue(self.sv.validate_selector('div.box'))

    def test_validate_selector_invalid(self):
        self.assertFalse(self.sv.validate_selector('>>bad'))
        self.assertFalse(self.sv.validate_selector('!!!'))


# ---------------------------------------------------------------------------
# PaginationTester tests
# ---------------------------------------------------------------------------

class TestPaginationTester(unittest.TestCase):

    def test_crawl_three_pages(self):
        tester = PaginationTester(_base_url, max_pages=10)
        pages = tester.crawl(f'{_base_url}/page1')
        self.assertEqual(len(pages), 3)

    def test_crawl_all_200(self):
        tester = PaginationTester(_base_url, max_pages=10)
        pages = tester.crawl(f'{_base_url}/page1')
        for page in pages:
            self.assertEqual(page['status'], 200)

    def test_crawl_respects_max_pages(self):
        tester = PaginationTester(_base_url, max_pages=2)
        pages = tester.crawl(f'{_base_url}/page1')
        self.assertLessEqual(len(pages), 2)

    def test_crawl_visits_page1(self):
        tester = PaginationTester(_base_url, max_pages=10)
        pages = tester.crawl(f'{_base_url}/page1')
        urls = [p['url'] for p in pages]
        self.assertTrue(any('page1' in u or u == _base_url + '/' for u in urls))

    def test_crawl_visits_page2(self):
        tester = PaginationTester(_base_url, max_pages=10)
        pages = tester.crawl(f'{_base_url}/page1')
        urls = [p['url'] for p in pages]
        self.assertTrue(any('page2' in u for u in urls))

    def test_crawl_no_infinite_loop(self):
        # /page3 has no next link; pagination should stop naturally
        tester = PaginationTester(_base_url, max_pages=10)
        pages = tester.crawl(f'{_base_url}/page3')
        self.assertEqual(len(pages), 1)


# ---------------------------------------------------------------------------
# RateLimitChecker tests
# ---------------------------------------------------------------------------

class TestRateLimitChecker(unittest.TestCase):

    def test_wait_enforces_delay(self):
        checker = RateLimitChecker(min_delay=0.05)
        checker.wait_if_needed()
        checker.wait_if_needed()
        self.assertGreaterEqual(len(checker.request_times), 2)
        interval = checker.request_times[1] - checker.request_times[0]
        self.assertGreaterEqual(interval, 0.04)  # slight tolerance

    def test_check_compliance_passes(self):
        checker = RateLimitChecker(min_delay=0.05)
        for _ in range(3):
            checker.fetch(f'{_base_url}/rate-test')
        self.assertTrue(checker.check_compliance())

    def test_request_count(self):
        checker = RateLimitChecker(min_delay=0.05)
        for _ in range(4):
            checker.fetch(f'{_base_url}/rate-test')
        self.assertEqual(len(checker.request_times), 4)

    def test_get_average_rate(self):
        checker = RateLimitChecker(min_delay=0.05)
        for _ in range(3):
            checker.fetch(f'{_base_url}/rate-test')
        avg = checker.get_average_rate()
        self.assertGreater(avg, 0)
        # Should not be faster than 1/min_delay with some tolerance
        self.assertLessEqual(avg, 1.0 / checker.min_delay * 1.5)

    def test_single_request_no_compliance_issue(self):
        checker = RateLimitChecker(min_delay=0.1)
        checker.wait_if_needed()
        self.assertTrue(checker.check_compliance())


# ---------------------------------------------------------------------------
# RobotsTxtParser tests
# ---------------------------------------------------------------------------

class TestRobotsTxtParser(unittest.TestCase):

    _ROBOTS = """\
User-agent: *
Disallow: /private/
Disallow: /admin/
Allow: /public/
Crawl-delay: 2

User-agent: GoodBot
Allow: /

User-agent: BadBot
Disallow: /

Sitemap: http://example.com/sitemap.xml
Sitemap: http://example.com/sitemap2.xml
"""

    def setUp(self):
        self.rp = RobotsTxtParser(self._ROBOTS)

    def test_disallow_private(self):
        self.assertFalse(self.rp.is_allowed('/private/'))

    def test_disallow_admin(self):
        self.assertFalse(self.rp.is_allowed('/admin/page'))

    def test_allow_public(self):
        self.assertTrue(self.rp.is_allowed('/public/data'))

    def test_allow_root(self):
        self.assertTrue(self.rp.is_allowed('/'))

    def test_crawl_delay(self):
        self.assertEqual(self.rp.get_crawl_delay(), 2.0)

    def test_sitemaps(self):
        self.assertEqual(len(self.rp.sitemaps), 2)
        self.assertIn('http://example.com/sitemap.xml', self.rp.sitemaps)

    def test_badbot_disallowed(self):
        rp_bad = RobotsTxtParser(self._ROBOTS, user_agent='BadBot')
        self.assertFalse(rp_bad.is_allowed('/'))
        self.assertFalse(rp_bad.is_allowed('/public/'))

    def test_goodbot_allowed(self):
        rp_good = RobotsTxtParser(self._ROBOTS, user_agent='GoodBot')
        self.assertTrue(rp_good.is_allowed('/private/'))

    def test_fetch_robots_from_server(self):
        resp = _http_get(f'{_base_url}/robots.txt')
        self.assertEqual(resp['status'], 200)
        rp = RobotsTxtParser(resp['body'])
        self.assertFalse(rp.is_allowed('/private/'))
        self.assertTrue(rp.is_allowed('/public/info'))


# ---------------------------------------------------------------------------
# Redirect handling tests
# ---------------------------------------------------------------------------

class TestRedirectHandling(unittest.TestCase):

    def test_301_followed(self):
        resp = _http_get(f'{_base_url}/redirect301')
        self.assertEqual(resp['status'], 200)

    def test_302_followed(self):
        resp = _http_get(f'{_base_url}/redirect302')
        self.assertEqual(resp['status'], 200)

    def test_301_content_correct(self):
        resp = _http_get(f'{_base_url}/redirect301')
        self.assertIn('Welcome to Page 1', resp['body'])

    def test_redirect_chain(self):
        resp = _http_get(f'{_base_url}/redirect-chain')
        self.assertEqual(resp['status'], 200)

    def test_no_follow_returns_301(self):
        resp = _http_get_no_follow(f'{_base_url}/redirect301')
        self.assertEqual(resp['status'], 301)

    def test_no_follow_has_location(self):
        resp = _http_get_no_follow(f'{_base_url}/redirect301')
        loc = resp.get('location', '') or resp.get('headers', {}).get('Location', '')
        self.assertTrue(bool(loc))


# ---------------------------------------------------------------------------
# Error recovery tests
# ---------------------------------------------------------------------------

class TestErrorRecovery(unittest.TestCase):

    def test_404_returns_none(self):
        fetcher = ErrorRecoveryFetcher(max_retries=2, retry_delay=0.02)
        result = fetcher.fetch(f'{_base_url}/not-found')
        self.assertIsNone(result)

    def test_404_tracked_in_skipped(self):
        fetcher = ErrorRecoveryFetcher(max_retries=2, retry_delay=0.02)
        url = f'{_base_url}/not-found'
        fetcher.fetch(url)
        self.assertIn(url, fetcher.skipped_urls)

    def test_5xx_retry_and_recover(self):
        # Reset hit counter
        MockScraperHandler._error_hit_counts['/server-error'] = 0
        fetcher = ErrorRecoveryFetcher(max_retries=3, retry_delay=0.02)
        result = fetcher.fetch(f'{_base_url}/server-error')
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 200)

    def test_5xx_retry_count_recorded(self):
        MockScraperHandler._error_hit_counts['/server-error'] = 0
        fetcher = ErrorRecoveryFetcher(max_retries=3, retry_delay=0.02)
        url = f'{_base_url}/server-error'
        fetcher.fetch(url)
        self.assertGreater(fetcher.retry_counts.get(url, 0), 0)

    def test_always_500_exhausts_retries(self):
        fetcher = ErrorRecoveryFetcher(max_retries=2, retry_delay=0.01)
        result = fetcher.fetch(f'{_base_url}/always-500')
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Table extraction tests (via mock server)
# ---------------------------------------------------------------------------

class TestTableExtraction(unittest.TestCase):

    def setUp(self):
        resp = _http_get(f'{_base_url}/table')
        self.parser = SimpleHTMLParser(resp['body'])
        self.tables = self.parser.get_tables()

    def test_table_found(self):
        self.assertEqual(len(self.tables), 1)

    def test_table_row_count(self):
        self.assertEqual(len(self.tables[0]), 4)  # header + 3 data rows

    def test_table_header_columns(self):
        headers = self.tables[0][0]
        self.assertIn('Name', headers)
        self.assertIn('Price', headers)
        self.assertIn('Stock', headers)

    def test_table_data_widget_a(self):
        data = [cell for row in self.tables[0] for cell in row]
        self.assertIn('Widget A', data)

    def test_table_data_price(self):
        data = [cell for row in self.tables[0] for cell in row]
        self.assertIn('$10.00', data)


# ---------------------------------------------------------------------------
# ScraperTestRunner integration test
# ---------------------------------------------------------------------------

class TestScraperTestRunner(unittest.TestCase):

    def test_runner_all_pass(self):
        runner = ScraperTestRunner(_base_url)
        success = runner.run_all()
        self.assertTrue(success, msg=f"Failures: {[r for r in runner.results if not r['passed']]}")

    def test_runner_collects_results(self):
        runner = ScraperTestRunner(_base_url)
        runner.run_all()
        self.assertGreater(len(runner.results), 0)

    def test_runner_result_structure(self):
        runner = ScraperTestRunner(_base_url)
        runner.run_all()
        for result in runner.results:
            self.assertIn('name', result)
            self.assertIn('passed', result)


if __name__ == '__main__':
    unittest.main()
