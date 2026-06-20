from __future__ import annotations

import json
import logging
import os
import re

from amazon_lead_agent.llm.router import LLMRouter
from amazon_lead_agent.prompts import load_prompt
from amazon_lead_agent.tools.amazon_backlink_discovery import (
    contains_amazon_buying_signal,
    extract_amazon_links,
    has_verified_amazon_evidence,
    is_valid_amazon_url,
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


def _scrapegraph_enabled(llm_config: dict | None = None) -> bool:
    config_value = None
    if isinstance(llm_config, dict):
        config_value = llm_config.get("enable_scrapegraphai")
    env_value = os.environ.get("ENABLE_SCRAPEGRAPHAI")
    raw = env_value if env_value is not None and env_value.strip() != "" else config_value
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


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
        "website_title": title,
        "category": "",
        "country": "",
        "description": text[:800],
        "amazon_links": snapshot["amazon_links"],
        "amazon_evidence_summary": amazon_evidence_summary,
        "amazon_backlink_found": bool(snapshot["amazon_links"]),
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
    profile["website_title"] = profile.get("website_title") or snapshot.get("title") or ""
    canonical_brand_name = str(profile.get("canonical_brand_name") or profile.get("seed_label") or "").strip()
    if canonical_brand_name:
        profile["company_name"] = profile.get("company_name") or canonical_brand_name
        profile["brand_name"] = profile.get("brand_name") or canonical_brand_name
    else:
        profile["company_name"] = profile.get("company_name") or profile.get("brand_name") or profile["website_title"] or snapshot["url"]
        profile["brand_name"] = profile.get("brand_name") or profile["company_name"]
    amazon_links = [link for link in _ensure_list(profile.get("amazon_links")) + list(snapshot["amazon_links"]) if is_valid_amazon_url(link)]
    amazon_evidence_urls = [url for url in _ensure_list(profile.get("amazon_evidence_url")) + _ensure_list(profile.get("amazon_evidence_urls")) if is_valid_amazon_url(url)]
    profile["amazon_links"] = sorted(set(amazon_links))
    profile["amazon_evidence_urls"] = sorted(set(amazon_evidence_urls or amazon_links))
    if profile["amazon_evidence_urls"]:
        profile["amazon_evidence_url"] = profile["amazon_evidence_urls"][0]
    profile["amazon_evidence_summary"] = profile.get("amazon_evidence_summary") or summarize_amazon_evidence(profile.get("amazon_links", []), snapshot["text"])
    profile["amazon_backlink_found"] = bool(profile.get("amazon_links") or profile.get("amazon_evidence_urls"))
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


def _scrapegraph_attempt(url: str, snapshot: dict, llm_config: dict | None = None) -> tuple[dict | None, str | None]:
    try:
        from scrapegraphai.graphs import SmartScraperGraph
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("ScrapeGraphAI not available: %s", exc)
        return None, str(exc)
    try:
        prompt = load_prompt("extract_brand.md")
        graph_config = {
            "headless": True,
            "verbose": False,
        }
        llm_config = llm_config or {}
        use_minimax = str(llm_config.get("provider") or os.environ.get("SCRAPEGRAPHAI_LLM_PROVIDER", "minimax")).strip().lower() == "minimax"
        minimax_key = str(os.environ.get("MINIMAX_API_KEY", "") or llm_config.get("minimax_api_key") or "").strip()
        if use_minimax and minimax_key:
            graph_config["llm"] = {
                "api_key": minimax_key,
                "model": str(llm_config.get("minimax_model") or os.environ.get("MINIMAX_MODEL", "MiniMax-M3")),
                "base_url": str(llm_config.get("minimax_api_base") or os.environ.get("MINIMAX_API_BASE", "https://api.minimax.io/v1/text/chatcompletion_v2")),
                "temperature": 0,
                "format": "json",
                "model_tokens": int(llm_config.get("minimax_max_tokens_research") or os.environ.get("MINIMAX_MAX_TOKENS_RESEARCH", "2048")),
            }
            extraction_method = "scrapegraphai_minimax"
        else:
            extraction_method = "scrapegraphai_other"
        graph = SmartScraperGraph(
            prompt=prompt,
            source=url,
            config=graph_config,
        )
        result = graph.run()
        if isinstance(result, str):
            parsed = json.loads(result)
        elif isinstance(result, dict):
            parsed = result
        else:
            parsed = {"result": result}
        profile = _normalize_profile(parsed, url, snapshot, extraction_method)
        if extraction_method == "scrapegraphai_minimax":
            profile["llm_provider_used"] = "minimax"
            profile["llm_model_used"] = str(llm_config.get("minimax_model") or os.environ.get("MINIMAX_MODEL", "MiniMax-M3"))
        else:
            profile["llm_provider_used"] = profile.get("llm_provider_used", "")
            profile["llm_model_used"] = profile.get("llm_model_used", "")
        return profile, None
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        LOGGER.warning("ScrapeGraphAI extraction failed for %s: %s", url, message)
        return None, message


def _extraction_method_for_router(router: LLMRouter) -> str:
    provider = (router.last_used_provider or "").strip().lower()
    model = (router.last_used_model or "").strip().lower()
    if provider == "gemini":
        return "gemini_direct"
    if provider == "minimax":
        return "minimax_direct_m27" if "2.7" in model or "m2.7" in model else "minimax_direct_m3"
    if provider == "openai":
        return "openai_direct"
    return "blocked_or_error"


def extract_brand_profile(url: str, minimax_api_key: str | None = None, llm_config: dict | None = None) -> dict:
    try:
        snapshot = _build_snapshot(url)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Snapshot build failed for %s: %s", url, exc)
        snapshot = {
            "url": url,
            "html": "",
            "text": "",
            "links": [],
            "amazon_links": [],
            "contact_links": [],
            "public_emails": [],
            "blocked": True,
            "title": "",
            "source_urls": [url],
        }
    if snapshot["blocked"]:
        blocked_profile = _heuristic_profile(url, snapshot)
        blocked_profile["extraction_method"] = "blocked_or_error"
        blocked_profile["notes"] = "public page appears blocked or challenged"
        blocked_profile["blocked_or_error"] = True
        blocked_profile["scrapegraph_error"] = ""
        blocked_profile["extraction_error"] = "public page appears blocked or challenged"
        return blocked_profile

    scrapegraph_profile: dict | None = None
    scrapegraph_error: str | None = None
    if _scrapegraph_enabled(llm_config):
        scrapegraph_profile, scrapegraph_error = _scrapegraph_attempt(url, snapshot, llm_config=llm_config)
        if scrapegraph_profile:
            scrapegraph_profile["scrapegraph_error"] = scrapegraph_error or ""
            scrapegraph_profile["extraction_error"] = ""
            return scrapegraph_profile
    else:
        LOGGER.info("ScrapeGraphAI disabled for %s; using direct LLM extraction", url)

    prompt = _build_prompt(url, snapshot)
    router = LLMRouter(config={"llm": llm_config} if llm_config else None, minimax_api_key=minimax_api_key)
    try:
        profile = router.generate_json(prompt, purpose="extraction")
        extraction_method = _extraction_method_for_router(router)
        normalized = _normalize_profile(profile, url, snapshot, extraction_method)
        normalized["scrapegraph_error"] = scrapegraph_error or ""
        normalized["extraction_error"] = ""
        normalized["llm_provider_used"] = router.last_used_provider or ""
        normalized["llm_model_used"] = router.last_used_model or ""
        normalized["llm_attempted_providers"] = list(getattr(router, "last_attempted_providers", []))
        return normalized
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Direct LLM extraction failed for %s: %s", url, exc)
        if _allow_heuristic_fallback():
            profile = _heuristic_profile(url, snapshot)
            profile["extraction_method"] = "heuristic_fallback"
            profile["notes"] = str(exc)
            profile["scrapegraph_error"] = scrapegraph_error or ""
            profile["extraction_error"] = str(exc)
            profile["llm_provider_used"] = router.last_used_provider or ""
            profile["llm_model_used"] = router.last_used_model or ""
            profile["llm_attempted_providers"] = list(getattr(router, "last_attempted_providers", []))
            return profile
        blocked_profile = _heuristic_profile(url, snapshot)
        blocked_profile["extraction_method"] = "blocked_or_error"
        blocked_profile["notes"] = str(exc)
        blocked_profile["blocked_or_error"] = True
        blocked_profile["scrapegraph_error"] = scrapegraph_error or ""
        blocked_profile["extraction_error"] = str(exc)
        blocked_profile["llm_provider_used"] = router.last_used_provider or ""
        blocked_profile["llm_model_used"] = router.last_used_model or ""
        blocked_profile["llm_attempted_providers"] = list(getattr(router, "last_attempted_providers", []))
        return blocked_profile
