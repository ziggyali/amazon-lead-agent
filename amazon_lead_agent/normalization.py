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

