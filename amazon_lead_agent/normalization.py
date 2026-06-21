from __future__ import annotations

from hashlib import sha1
from urllib.parse import urlparse
import re


COMPANY_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "l.l.c.",
    "ltd",
    "ltd.",
    "co",
    "co.",
    "company",
    "corp",
    "corp.",
    "corporation",
    "brands",
}

SEED_BRAND_ALIASES = {
    "glossier.com": "Glossier",
    "cocokind.com": "Cocokind",
    "tatcha.com": "Tatcha",
    "thehonestkitchen.com": "The Honest Kitchen",
    "wildone.com": "Wild One",
    "fablepets.com": "Fable Pets",
    "brooklinen.com": "Brooklinen",
    "parachutehome.com": "Parachute Home",
}

GENERIC_TITLE_TOKENS = {
    "official",
    "shop",
    "store",
    "stores",
    "products",
    "product",
    "skincare",
    "beauty",
    "wellness",
    "supplements",
    "pet",
    "pets",
    "home",
    "kitchen",
    "luxury",
    "brand",
    "brands",
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def normalize_company_name(value: str | None) -> str:
    text = normalize_text(value)
    words = [word for word in re.findall(r"[a-z0-9]+", text) if word not in COMPANY_SUFFIXES]
    return " ".join(words)


def normalize_domain(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def infer_brand_name_from_domain(value: str | None) -> str:
    domain = normalize_domain(value)
    if not domain:
        return ""
    if " " in domain and "." not in domain:
        domain = domain.replace(" ", ".")
    if domain in SEED_BRAND_ALIASES:
        return SEED_BRAND_ALIASES[domain]
    root = domain.split(".")[0]
    tokens = [token for token in re.split(r"[-_.]+", root) if token]
    if not tokens:
        return ""
    if len(tokens) == 1:
        token = tokens[0]
        if token.isalpha():
            return token[:1].upper() + token[1:]
        return token.replace("-", " ").title().strip()
    return " ".join(token[:1].upper() + token[1:] for token in tokens)


def _looks_like_generic_page_title(value: str | None) -> bool:
    normalized = normalize_company_name(value)
    if not normalized:
        return False
    tokens = [token for token in normalized.split() if token]
    if len(tokens) >= 5:
        return True
    if len(tokens) >= 3 and any(token in GENERIC_TITLE_TOKENS for token in tokens):
        return True
    return False


def _looks_like_domainish_label(value: str | None) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    if "://" in text:
        return True
    if "." in text:
        return True
    return bool(re.search(r"\b(com|net|org|co|io|app|shop|site)\b", text) and " " in text)


def resolve_canonical_brand_name(payload: dict) -> str:
    seed_label = str(payload.get("seed_label") or payload.get("canonical_brand_name") or "").strip()
    if seed_label:
        return seed_label
    brand_name = str(payload.get("brand_name") or "").strip()
    company_name = str(payload.get("company_name") or "").strip()
    website = str(payload.get("website") or payload.get("primary_source_url") or "").strip()
    website_title = str(payload.get("website_title") or payload.get("title") or payload.get("page_title") or "").strip()
    domain = normalize_domain(website)
    inferred_name = infer_brand_name_from_domain(domain or website_title or website or company_name or brand_name)
    for candidate in (brand_name, company_name):
        if candidate and not _looks_like_generic_page_title(candidate) and not _looks_like_domainish_label(candidate):
            return candidate
    if inferred_name:
        return inferred_name
    return brand_name or company_name or ""


def make_deterministic_lead_id(domain: str | None, category: str | None = None) -> str:
    fingerprint = "|".join(part for part in (normalize_domain(domain), normalize_text(category)) if part)
    if not fingerprint:
        fingerprint = "unknown-lead"
    return sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def ensure_lead_identity(lead: dict) -> dict:
    payload = dict(lead or {})
    website = str(payload.get("website") or payload.get("primary_source_url") or "").strip()
    source_urls = payload.get("source_urls") or []
    if not website and isinstance(source_urls, list) and source_urls:
        website = str(source_urls[0] or "").strip()
    domain = normalize_domain(website or payload.get("normalized_domain") or payload.get("lead_domain") or "")
    category = str(payload.get("category") or "").strip()
    company_name = str(payload.get("company_name") or payload.get("brand_name") or website or "").strip()
    canonical_brand_name = resolve_canonical_brand_name(payload)
    inferred_name = infer_brand_name_from_domain(domain or website or canonical_brand_name)
    if canonical_brand_name:
        payload["canonical_brand_name"] = canonical_brand_name
    if not payload.get("brand_name") or _looks_like_generic_page_title(payload.get("brand_name")) or _looks_like_domainish_label(payload.get("brand_name")):
        payload["brand_name"] = canonical_brand_name or inferred_name or payload.get("brand_name") or ""
    if not payload.get("company_name") or _looks_like_generic_page_title(payload.get("company_name")) or _looks_like_domainish_label(payload.get("company_name")) or normalize_company_name(payload.get("company_name")) in {"", normalize_company_name(domain)}:
        payload["company_name"] = canonical_brand_name or inferred_name or payload.get("company_name") or ""
    if not payload.get("normalized_company_name"):
        payload["normalized_company_name"] = normalize_company_name(payload.get("company_name"))
    if domain and not payload.get("normalized_domain"):
        payload["normalized_domain"] = domain
    if not payload.get("website_title"):
        payload["website_title"] = str(payload.get("title") or payload.get("page_title") or "").strip()
    lead_id = str(payload.get("lead_id") or payload.get("id") or "").strip()
    if not lead_id:
        lead_id = make_deterministic_lead_id(domain or website or company_name, category)
    payload["id"] = str(payload.get("id") or lead_id).strip() or lead_id
    payload["lead_id"] = str(payload.get("lead_id") or lead_id).strip() or lead_id
    if not payload.get("company_name"):
        payload["company_name"] = canonical_brand_name or inferred_name or domain or "Unknown"
    if not payload.get("brand_name"):
        payload["brand_name"] = payload["company_name"]
    if not payload.get("canonical_brand_name"):
        payload["canonical_brand_name"] = payload["brand_name"]
    return payload


def validate_lead_identity_for_storage(lead: dict) -> tuple[dict, list[str]]:
    payload = ensure_lead_identity(lead)
    missing: list[str] = []
    for field in ("lead_id", "brand_name", "website", "status"):
        if not str(payload.get(field) or "").strip():
            missing.append(field)
    return payload, missing


def make_lead_id(company_name: str | None, website: str | None, amazon_link: str | None = None) -> str:
    parts = [
        normalize_company_name(company_name),
        normalize_domain(website),
        normalize_text(amazon_link),
    ]
    fingerprint = "|".join(part for part in parts if part)
    if not fingerprint:
        fingerprint = "unknown-lead"
    return sha1(fingerprint.encode("utf-8")).hexdigest()[:16]

