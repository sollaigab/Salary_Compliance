"""
Small text-cleaning helpers shared by several modules.
"""

import html
import re
import unicodedata


def clean_html(text: str) -> str:
    """Removes HTML tags and normalizes whitespace."""
    if not text:
        return ""
    text = html.unescape(text)          # Greenhouse returns escaped HTML
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(text: str) -> str:
    """'Forlì' -> 'Forli', 'Città' -> 'Citta'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "")
                   if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """Lowercase, no accents or apostrophes, only letters/digits separated by one space.

    "D'Amico S.p.A." -> 'damico s p a'
    """
    text = strip_accents(text).lower()
    text = re.sub(r"['’`]", "", text)          # the apostrophe joins: d'amico -> damico
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()
