from __future__ import annotations

from pathlib import Path

from amazon_lead_agent.tools.sqlite_store import get_connection, get_leads_for_scoring, upsert_lead, record_outreach_event


RELEVANT_CATEGORIES = {"beauty", "pet", "home", "supplements"}


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def score_lead(lead: dict) -> dict:
    score = 0
    reasons: list[str] = []

    amazon_backlink = _truthy(lead.get("amazon_backlink_found"))
    amazon_links = lead.get("amazon_links") or lead.get("amazon_links_json") or []
    if amazon_backlink or amazon_links:
        score += 30
        reasons.append("Amazon evidence found")

    evidence_text = " ".join(
        str(part or "")
        for part in [lead.get("amazon_evidence_summary"), lead.get("description"), lead.get("notes")]
    ).lower()
    if any(signal in evidence_text for signal in ("available on amazon", "shop our amazon store", "buy on amazon", "amazon storefront", "amazon store")):
        score += 15
        reasons.append("Strong buying signal text")

    if lead.get("website"):
        score += 10
        reasons.append("Website found")

    if str(lead.get("category", "")).lower() in RELEVANT_CATEGORIES:
        score += 10
        reasons.append("Relevant category")

    public_emails = lead.get("public_emails") or []
    contact_page = lead.get("contact_page_url")
    if public_emails or contact_page:
        score += 15
        reasons.append("Contact path found")

    decision_makers = lead.get("founder_or_executive_names") or lead.get("ecommerce_or_marketplace_people") or []
    if decision_makers:
        score += 10
        reasons.append("Decision maker clue found")

    pain_points = lead.get("pain_points") or []
    if pain_points:
        score += 15
        reasons.append("Pain point identified")

    source_urls = lead.get("source_urls") or []
    if len(source_urls) > 1:
        score += 5
        reasons.append("Multiple source URLs")

    if not amazon_backlink and not amazon_links:
        score -= 35
        reasons.append("No Amazon evidence")

    if not public_emails and not contact_page:
        score -= 25
        reasons.append("No contact path")

    if not (lead.get("company_name") or lead.get("brand_name") or lead.get("website")):
        score -= 20
        reasons.append("Weak/unclear brand")

    score = max(0, min(100, score))
    if score >= 85:
        tier = "A"
    elif score >= 75:
        tier = "B"
    elif score >= 55:
        tier = "C"
    else:
        tier = "Reject"

    completeness = sum(
        1
        for field in ("website", "category", "amazon_evidence_summary", "public_emails", "contact_page_url", "pain_points")
        if lead.get(field)
    )
    confidence = min(0.99, 0.45 + completeness * 0.08 + (0.05 if amazon_backlink else 0))

    return {
        "score": score,
        "tier": tier,
        "confidence": round(confidence, 2),
        "score_reason": "; ".join(reasons),
        "status": "scored" if tier != "Reject" else "rejected",
    }


def classify_scored_lead(lead: dict, min_score_for_draft: int) -> dict:
    score = int(lead.get("score") or 0)
    has_email = bool((lead.get("public_emails") or []))
    has_contact_page = bool(lead.get("contact_page_url"))
    if score >= min_score_for_draft and has_email and lead.get("tier") in {"A", "B"}:
        return {
            "status": "approved",
            "review_status": "approved",
            "send_status": "pending",
            "email_status": "public_email",
            "lead_type": "lead",
        }
    if score >= min_score_for_draft and has_contact_page and not has_email:
        return {
            "status": "contact_form_queue",
            "review_status": "needs_contact_form",
            "send_status": "contact_form_queue",
            "email_status": "contact_form_only",
            "lead_type": "contact_form_queue",
        }
    if lead.get("tier") == "Reject" or score < min_score_for_draft:
        return {
            "status": "rejected",
            "review_status": "rejected",
            "send_status": "not_eligible",
            "email_status": "unknown",
            "lead_type": "lead",
        }
    return {
        "status": "scored",
        "review_status": "needs_review",
        "send_status": "pending",
        "email_status": "unknown",
        "lead_type": "lead",
    }


def run_scoring(config: dict, db_path: Path) -> list[dict]:
    conn = get_connection(db_path)
    scored: list[dict] = []
    try:
        leads = get_leads_for_scoring(conn, int(config["campaign"]["daily_discovery_limit"]))
        min_score_for_draft = int(config["campaign"]["minimum_score_for_draft"])
        for lead in leads:
            update = score_lead(lead)
            classification = classify_scored_lead({**lead, **update}, min_score_for_draft)
            merged = {**lead, **update, **classification}
            upsert_lead(conn, merged)
            record_outreach_event(conn, {"lead_id": lead["id"], "event_type": "scored", "metadata": update})
            record_outreach_event(
                conn,
                {
                    "lead_id": lead["id"],
                    "event_type": classification["status"],
                    "metadata": classification,
                },
            )
            scored.append(merged)
        conn.commit()
        return scored
    finally:
        conn.close()

