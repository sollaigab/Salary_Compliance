"""
Headless browser (Playwright + Chromium) for career pages built with JavaScript.

`requests` only gets the initial HTML: if a script loads the ATS widget, the fingerprint
(e.g. boards.greenhouse.io/xyz) is missing. The browser runs the scripts like a real user and
shows the final HTML, the iframes and every network request the page made.

Used only when the `requests` pass finds nothing, because it is slower (~5-15 s per page).
"""

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from lib import http

# Resources useless for finding the ATS: skip them so the page loads faster
BLOCKED_RESOURCES = {"image", "media", "font"}


class Browser:
    """Use as `with Browser() as b: b.render(url)`."""

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=http.USER_AGENT, locale="it-IT")
        return self

    def __exit__(self, *exc):
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def render(self, url: str) -> dict | None:
        """Opens the page and returns {url, html, network, frames, links}, or None on failure.

        Respects robots.txt and the per-domain rate limit like normal requests.
        """
        if not http.allowed_by_robots(url):
            raise http.RobotsDisallowed(url)
        host = urlparse(url).netloc.lower()
        http.wait_turn(host)

        page = self._context.new_page()
        network = []
        page.on("request", lambda req: network.append(req.url))
        page.route("**/*", lambda route: route.abort()
                   if route.request.resource_type in BLOCKED_RESOURCES else route.continue_())
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            try:
                # wait for scripts to finish loading the widgets (max 6 s)
                page.wait_for_load_state("networkidle", timeout=6_000)
            except PlaywrightTimeout:
                pass
            result = {
                "url": page.url,
                "html": page.content(),
                "network": network,
                "frames": [f.url for f in page.frames],
                "links": page.eval_on_selector_all(
                    "a[href]",
                    # in SVG links e.href is an object, not a string: read the attribute instead
                    "els => els.map(e => [typeof e.href === 'string' ? e.href : (e.getAttribute('href') || ''),"
                    " (e.innerText || '').trim().slice(0, 80)])"),
            }
        except PlaywrightError:
            result = None
        finally:
            http.mark_call(host)
            page.close()
        return result
