from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import urljoin

from amazon_lead_agent.llm.minimax_client import MiniMaxClient, MiniMaxError
from amazon_lead_agent.prompts import load_prompt
from amazon_lead_agent.tools.amazon_backlink_discovery import (
    contains_amazon_buying_signal,
    extract_amazon_links,
    summarize_amazon_evidence,
)
from amazon_lead_agent.tools.web_extraction import (
    discover_contact_like_urls,
    extract_public_emails,
    extract_text_and_links,
    fetch_html,
    get_visible_title,
    filter_public_names,
    looks_blocked,
)


LOGGER = logging.getLogger(__name__)


def _allow_heuristic_fallback() -> bool:
    return os.environ.get("ALLOW_HEURISTIC_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "y", "on"}


def _ensure_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, (tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = re.split(r"[\n,;]+", value)
        return [part.strip() for part in parts if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _build_snapshot(url: str) -> dict:
    html, final_url = fetch_html(url)
    text, links = extract_text_and_links(html, base_url=final_url)
    amazon_links = extract_amazon_links(html, final_url)
    contact_links = discover_contact_like_urls(links)
    public_emails = extract_public_emails(html, text)
    blocked = looks_blocked(html)
    page_sources = [(final_url or url, html, text, links)]
    for contact_url in contact_links[:3]:
        try:
            contact_html, contact_final_url = fetch_html(contact_url, use_playwright=False)
            contact_text, contact_links_found = extract_text_and_links(contact_html, base_url=contact_final_url)
            public_emails.extend(extract_public_emails(contact_html, contact_text))
            page_sources.append((contact_final_url or contact_url, contact_html, contact_text, contact_links_found))
            if looks_blocked(contact_html):
                continue
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("contact page fetch failed for %s: %s", contact_url, exc)
            continue
    combined_text = "\n".join(source[2] for source in page_sources if source[2])
    combined_links = []
    for _, _, _, page_links in page_sources:
        combined_links.extend(page_links)
    return {
        "url": final_url or url,
        "html": html,
        "text": combined_text or text,
        "links": sorted(set(combined_links or links)),
        "amazon_links": amazon_links,
        "contact_links": contact_links,
        "public_emails": sorted(set(public_emails)),
        "blocked": blocked,
        "title": get_visible_title(html),
        "source_urls": sorted(set([source_url for source_url, _, _, _ in page_sources])),
    }


def _heuristic_profile(url: str, snapshot: dict) -> dict:
    text = snapshot["text"]
    title = snapshot.get("title") or ""
    company_name = title.split("|")[0].split("-")[0].strip() or re.sub(r"https?://", "", snapshot["url"]).split("/")[0]
    pain_points = []
    for token in ("inventory", "ads", "marketplace", "operations", "amazon", "fulfillment", "clean", "conversion"):
        if token in text.lower():
            pain_points.append(token)
    amazon_evidence_summary = summarize_amazon_evidence(snapshot["amazon_links"], text)
    return {
        "company_name": company_name,
        "brand_name": company_name,
        "website": snapshot["url"] or url,
        "category": "",
        "country": "",
        "description": text[:800],
        "amazon_links": snapshot["amazon_links"],
        "amazon_evidence_summary": amazon_evidence_summary,
        "amazon_backlink_found": bool(snapshot["amazon_links"] or contains_amazon_buying_signal(text)),
        "founder_or_executive_names": [],
        "ecommerce_or_marketplace_people": [],
        "public_emails": snapshot["public_emails"],
        "contact_page_url": snapshot["contact_links"][0] if snapshot["contact_links"] else "",
        "decision_maker_source_url": snapshot["url"] or url,
        "pain_points": pain_points,
        "confidence": 0.35 if not snapshot["amazon_links"] else 0.6,
        "source_quotes": [title] if title else [],
        "source_urls": snapshot["source_urls"],
    }


def _build_prompt(url: str, snapshot: dict) -> str:
    template = load_prompt("extract_brand.md")
    return "\n".join(
        [
            template,
            "",
            "PUBLIC SNAPSHOT:",
            f"URL: {url}",
            f"FINAL_URL: {snapshot['url']}",
            f"TITLE: {snapshot['title']}",
            f"PUBLIC_EMAILS: {json.dumps(snapshot['public_emails'], ensure_ascii=False)}",
            f"AMAZON_LINKS: {json.dumps(snapshot['amazon_links'], ensure_ascii=False)}",
            f"CONTACT_LINKS: {json.dumps(snapshot['contact_links'], ensure_ascii=False)}",
            "PAGE_TEXT:",
            snapshot["text"][:12000],
        ]
    )


def _normalize_profile(profile: dict, url: str, snapshot: dict, extraction_method: str) -> dict:
    profile = dict(profile or {})
    profile["website"] = profile.get("website") or snapshot["url"] or url
    profile["company_name"] = profile.get("company_name") or profile.get("brand_name") or snapshot.get("title") or snapshot["url"]
    profile["brand_name"] = profile.get("brand_name") or profile["company_name"]
    profile["amazon_links"] = sorted(set(_ensure_list(profile.get("amazon_links")) + list(snapshot["amazon_links"])))
    profile["amazon_evidence_summary"] = profile.get("amazon_evidence_summary") or summarize_amazon_evidence(profile.get("amazon_links", []), snapshot["text"])
    profile["amazon_backlink_found"] = bool(profile.get("amazon_links") or contains_amazon_buying_signal(snapshot["text"]))
    profile["public_emails"] = sorted(set(_ensure_list(profile.get("public_emails")) + list(snapshot["public_emails"])))
    profile["contact_page_url"] = profile.get("contact_page_url") or (snapshot["contact_links"][0] if snapshot["contact_links"] else "")
    profile["decision_maker_source_url"] = profile.get("decision_maker_source_url") or snapshot["url"] or url
    profile["pain_points"] = _ensure_list(profile.get("pain_points", []))
    profile["founder_or_executive_names"] = filter_public_names(profile.get("founder_or_executive_names", []))
    profile["ecommerce_or_marketplace_people"] = filter_public_names(profile.get("ecommerce_or_marketplace_people", []))
    profile["source_quotes"] = sorted(set(_ensure_list(profile.get("source_quotes")) + ([snapshot.get("title")] if snapshot.get("title") else [])))
    profile["source_urls"] = sorted(set(_ensure_list(profile.get("source_urls")) + list(snapshot["source_urls"])))
    profile["extraction_method"] = extraction_method
    profile["blocked_or_error"] = False
    return profile


def _scrapegraph_attempt(url: str, snapshot: dict) -> dict | None:
    try:
        from scrapegraphai.graphs import SmartScraperGraph
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("ScrapeGraphAI not available: %s", exc)
        return None
    try:
        prompt = load_prompt("extract_brand.md")
        graph = SmartScraperGraph(
            prompt=prompt,
            source=url,
            config={
                "headless": True,
                "verbose": False,
            },
        )
        result = graph.run()
        if isinstance(result, str):
            parsed = json.loads(result)
        elif isinstance(result, dict):
            parsed = result
        else:
            parsed = {"result": result}
        return _normalize_profile(parsed, url, snapshot, "scrapegraphai_minimax")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("ScrapeGraphAI extraction failed for %s: %s", url, exc)
        return None


def extract_brand_profile(url: str, minimax_api_key: str | None = None) -> dict:
    snapshot = _build_snapshot(url)
    if snapshot["blocked"]:
        blocked_profile = _heuristic_profile(url, snapshot)
        blocked_profile["extraction_method"] = "blocked_or_error"
        blocked_profile["notes"] = "public page appears blocked or challenged"
        blocked_profile["blocked_or_error"] = True
        return blocked_profile

    scrapegraph_profile = _scrapegraph_attempt(url, snapshot)
    if scrapegraph_profile:
        return scrapegraph_profile

    prompt = _build_prompt(url, snapshot)
    client = MiniMaxClient(api_key=minimax_api_key)
    try:
        profile = client.generate_json(prompt, purpose="extraction")
        used_model = (client.last_used_model or "").lower()
        extraction_method = "minimax_direct_m27" if "2.7" in used_model or "m2.7" in used_model else "minimax_direct_m3"
        return _normalize_profile(profile, url, snapshot, extraction_method)
    except MiniMaxError as exc:
        LOGGER.warning("MiniMax direct extraction failed for %s: %s", url, exc)
        if _allow_heuristic_fallback():
            profile = _heuristic_profile(url, snapshot)
            profile["extraction_method"] = "heuristic_fallback"
            profile["notes"] = str(exc)
            return profile
        raise
