"""
Piccole funzioni di pulizia del testo usate da più moduli.
"""

import html
import re
import unicodedata


def clean_html(text: str) -> str:
    """Toglie i tag HTML e normalizza gli spazi."""
    if not text:
        return ""
    text = html.unescape(text)          # Greenhouse restituisce HTML con escape
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(text: str) -> str:
    """'Forlì' -> 'Forli', 'L'Oréal' -> 'L'Oreal'."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "")
                   if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """Minuscolo, senza accenti né apostrofi, solo lettere/cifre separate da uno spazio.

    'acme S.p.A.' -> 'acme s p a'
    """
    text = strip_accents(text).lower()
    text = re.sub(r"['’`]", "", text)          # l'apostrofo unisce: tod's -> tods
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()
