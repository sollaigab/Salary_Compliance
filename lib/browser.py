"""
Browser headless (Playwright + Chromium) per le pagine carriere costruite in JavaScript.

Con `requests` scarichiamo solo l'HTML iniziale: se il widget dell'ATS viene caricato da uno script,
l'impronta (es. boards.greenhouse.io/xyz) non c'è. Il browser esegue gli script come un utente vero
e ci fa vedere: l'HTML finale, gli iframe e tutte le richieste di rete partite dalla pagina.

Si usa solo quando l'analisi con `requests` non trova niente, perché è più lento (~5-15 s a pagina).
"""

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from lib import http

# Risorse inutili per trovare l'ATS: non le scarichiamo, la pagina carica prima
BLOCKED_RESOURCES = {"image", "media", "font"}


class Browser:
    """Da usare con `with Browser() as b: b.render(url)`."""

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
        """Apre la pagina e restituisce {url, html, network, frames, links}, o None se fallisce.

        Rispetta robots.txt e il rate limit per dominio come le richieste normali.
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
                # aspetta che gli script finiscano di caricare i widget (max 6 s)
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
                    # nei link SVG e.href è un oggetto, non una stringa: in quel caso si legge l'attributo
                    "els => els.map(e => [typeof e.href === 'string' ? e.href : (e.getAttribute('href') || ''),"
                    " (e.innerText || '').trim().slice(0, 80)])"),
            }
        except PlaywrightError:
            result = None
        finally:
            http.mark_call(host)
            page.close()
        return result
