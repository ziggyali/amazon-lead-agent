from __future__ import annotations

import re
from urllib.parse import urlparse

from amazon_lead_agent.normalization import normalize_company_name, normalize_domain


BLOCKED_ROOT_DOMAINS = {
    # Search / click tracking
    "bing.com",
    "duckduckgo.com",
    "search.yahoo.com",
    "google.com",
    "googleusercontent.com",
    "go.redirectingat.com",
    # Amazon and Amazon-owned / adjacent surfaces
    "amazon.com",
    "amazon.ca",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.in",
    "amazon.co.jp",
    "amazon.com.au",
    "amazon.nl",
    "amazon.sg",
    "amazon.ae",
    "amazon.sa",
    "amazon.se",
    "amazon.pl",
    "amazon.com.mx",
    "amazon.com.br",
    "amazon.com.tr",
    "amazon.com.be",
    "amazon.com.eg",
    "amazonaws.com",
    "amazonvideo.com",
    "primevideo.com",
    "primevideo.amazon.com",
    "amzn.to",
    # Dictionary / reference / language-learning
    "dictionary.com",
    "merriam-webster.com",
    "thesaurus.com",
    "vocabulary.com",
    "collinsdictionary.com",
    "cambridge.org",
    "britannica.com",
    "wiktionary.org",
    "wikipedia.org",
    "duolingo.com",
    "babbel.com",
    "busuu.com",
    "memrise.com",
    # Movie / showtime / video / entertainment references
    "imdb.com",
    "rottentomatoes.com",
    "fandango.com",
    "moviefone.com",
    "tvguide.com",
    "letterboxd.com",
    "justwatch.com",
    "metacritic.com",
    "themoviedb.org",
    "showtimes.com",
    # Generic marketplaces / shopping platforms
    "ebay.com",
    "etsy.com",
    "walmart.com",
    "target.com",
    "aliexpress.com",
    "alibaba.com",
    "wish.com",
    "temu.com",
    "rakuten.com",
    "wayfair.com",
    "shein.com",
    "mercari.com",
    # News / listicles / publishers that often surface in search
    "popsugar.com",
    "glamour.com",
    "womenshealthmag.com",
    "headtopics.com",
    "buzzfeed.com",
    "instyle.com",
    "marieclaire.com",
    "people.com",
    "today.com",
    "goodhousekeeping.com",
}

BLOCKED_DOMAIN_KEYWORDS = (
    "dictionary",
    "reference",
    "marketplace",
    "video",
    "news",
    "listicle",
    "wiki",
    "youtube",
    "vimeo",
    "dailymotion",
    "showtime",
    "movie",
    "movies",
    "showtimes",
    "language",
    "translation",
    "synonym",
)

BLOCKED_TITLE_KEYWORDS = ("best", "top", "award winners", "list", "review", "news", "article")

JUNK_COMPANY_PATTERNS = (
    r"^available$",
    r"^available definition",
    r"^available synonym",
    r"^available adjective",
    r"definition\s*&\s*meaning",
    r"definition and meaning",
    r"synonyms\s*&\s*antonyms",
    r"which is correct",
    r"\btranslation\b",
    r"\bmeaning\b",
    r"\bshowtimes\b",
)

PREFERRED_PATH_HINTS = ("/pages/where-to-buy", "/retailers", "/amazon", "/store-locator", "/contact", "/about")
BRAND_SIGNAL_TERMS = (
    "official",
    "shop",
    "store",
    "products",
    "skincare",
    "pet",
    "home",
    "supplements",
    "wellness",
    "beauty",
)
RELATED_TERM_TERMS = (
    "where to buy",
    "retailers",
    "stockists",
    "available",
)


def normalized_host(value: str | None) -> str:
    return normalize_domain(value)


def _matches_root(domain: str, root: str) -> bool:
    return domain == root or domain.endswith(f".{root}")


def is_blocked_domain(value: str | None) -> bool:
    domain = normalize_domain(value)
    if not domain:
        return True
    if any(_matches_root(domain, root) for root in BLOCKED_ROOT_DOMAINS):
        return True
    if any(keyword in domain for keyword in BLOCKED_DOMAIN_KEYWORDS):
        return True
    return False


def is_tracking_or_search_domain(value: str | None) -> bool:
    domain = normalize_domain(value)
    if not domain:
        return False
    return domain in {"bing.com", "duckduckgo.com", "search.yahoo.com", "google.com", "googleusercontent.com"}


def is_junk_company_name(name: str | None) -> bool:
    normalized = normalize_company_name(name)
    if not normalized:
        return True
    raw = re.sub(r"\s+", " ", (name or "").strip().lower())
    if normalized == "available":
        return True
    if raw.startswith("available ") or raw == "available":
        return True
    return any(re.search(pattern, raw) for pattern in JUNK_COMPANY_PATTERNS)


def is_soft_brand_candidate(url: str | None, title: str | None = "", snippet: str | None = "", category: str | None = "") -> bool:
    domain = normalize_domain(url)
    if not domain or is_blocked_domain(domain) or is_tracking_or_search_domain(domain):
        return False
    if is_junk_company_name(title):
        return False
    if any(keyword in domain for keyword in BLOCKED_DOMAIN_KEYWORDS):
        return False
    title_text = (title or "").lower()
    snippet_text = (snippet or "").lower()
    if category and category.strip().lower() in title_text + " " + snippet_text:
        return True
    if any(term in f"{title_text} {snippet_text}" for term in RELATED_TERM_TERMS):
        return True
    return True


def _has_official_signal(title: str, snippet: str, url: str) -> bool:
    text = f"{title} {snippet}".lower()
    if any(hint in urlparse(url or "").path.lower() for hint in PREFERRED_PATH_HINTS):
        return True
    if any(signal in text for signal in ("official site", "official website", "brand site", "shop our", "where to buy", "store locator", "amazon store")):
        return True
    if "brand" in text and "site" in text:
        return True
    return False


def is_likely_brand_domain(url: str | None, title: str | None = "", snippet: str | None = "", category: str | None = "") -> bool:
    domain = normalize_domain(url)
    if not domain:
        return False
    if is_blocked_domain(domain) or is_tracking_or_search_domain(domain):
        return False
    title_text = (title or "").lower()
    snippet_text = (snippet or "").lower()
    path_text = urlparse(url or "").path.lower()
    signal_text = f"{title_text} {snippet_text}"
    if any(keyword in title_text for keyword in BLOCKED_TITLE_KEYWORDS) and not _has_official_signal(title_text, snippet_text, url or ""):
        return False
    if any(keyword in snippet_text for keyword in ("listicle", "news", "article", "review")) and not _has_official_signal(title_text, snippet_text, url or ""):
        return False
    if any(keyword in domain for keyword in BLOCKED_DOMAIN_KEYWORDS):
        return False
    if any(hint in path_text for hint in PREFERRED_PATH_HINTS):
        return True
    if any(signal in signal_text for signal in ("official site", "official website", "brand site", "where to buy", "amazon store", *BRAND_SIGNAL_TERMS, *RELATED_TERM_TERMS)):
        return True
    if _has_official_signal(title_text, snippet_text, url or ""):
        return True
    root = domain.split(".")
    if len(root) <= 4 and not any(token in domain for token in ("shopify", "bigcommerce", "wix", "squarespace", "wordpress", "blogspot", "amazon", "ebay", "walmart", "etsy")):
        return True
    if category and category.strip().lower() in signal_text:
        return True
    return False


def is_junk_or_blocked_result(url: str | None, title: str | None = "", snippet: str | None = "", category: str | None = "") -> bool:
    if is_blocked_domain(url):
        return True
    if is_tracking_or_search_domain(url):
        return True
    if is_junk_company_name(title):
        return True
    if not is_likely_brand_domain(url, title, snippet, category):
        return True
    return False
