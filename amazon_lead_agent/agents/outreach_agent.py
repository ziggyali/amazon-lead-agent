from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from amazon_lead_agent.tools.gmail_drafts import create_gmail_draft
from amazon_lead_agent.tools.storage_router import StorageRouter, get_storage_router


def compose_subject(lead: dict) -> str:
    brand = lead.get("brand_name") or lead.get("company_name") or "your brand"
    return f"Quick idea for {brand}'s Amazon growth"


def compose_body(lead: dict, sender_name: str, sender_offer: str) -> str:
    brand = lead.get("brand_name") or lead.get("company_name") or "your team"
    category = lead.get("category") or "your category"
    amazon_summary = lead.get("amazon_evidence_summary") or "I noticed a public Amazon presence."
    pain_points = ", ".join((lead.get("pain_points") or [])[:3]) or "Amazon operations"
    quote = (lead.get("source_quotes") or [""])[0]
    opt_out = "If I am off base, feel free to ignore this."
    parts = [
        f"Hi {brand} team,",
        "",
        f"I was looking at your {category} brand and noticed {amazon_summary}.",
        f"It looks like {pain_points} could be an area where we can help.",
        quote,
        "",
        sender_offer,
        "",
        opt_out,
        "",
        "Best,",
        sender_name,
    ]
    return "\n".join(part for part in parts if part is not None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage(config: dict, storage_or_path: Path | StorageRouter) -> StorageRouter:
    if isinstance(storage_or_path, StorageRouter):
        return storage_or_path
    return get_storage_router(config, storage_or_path)


def run_outreach(config: dict, db_path: Path | StorageRouter, dry_run: bool = False) -> list[dict]:
    storage = _storage(config, db_path)
    drafts: list[dict] = []
    try:
        min_score = int(config["campaign"]["minimum_score_for_draft"])
        limit = int(config["campaign"]["daily_draft_limit"])
        candidates = storage.get_leads_for_drafting(min_score=min_score, limit=limit)
        sender_name = config["sender"]["name"]
        sender_offer = config["sender"]["offer"]
        sender_email = __import__("os").environ.get("GMAIL_SENDER_EMAIL", "")
        mode_label = "DRY RUN" if dry_run else "LIVE"

        for lead in candidates:
            if lead.get("drafted"):
                continue
            if not lead.get("public_emails"):
                continue
            subject = compose_subject(lead)
            body = compose_body(lead, sender_name, sender_offer)
            recipient = (lead.get("public_emails") or [""])[0]
            if not recipient:
                continue
            if dry_run:
                preview_id = f"preview-{lead['id']}-{int(datetime.now().timestamp())}"
                merged = {
                    **lead,
                    "draft_preview_subject": subject,
                    "draft_preview_body": body,
                    "draft_subject": subject,
                    "draft_body": body,
                    "send_status": "draft_preview",
                    "status": "draft_preview",
                    "draft_id": preview_id,
                    "review_status": "previewed",
                    "updated_at": _now(),
                }
                storage.upsert_lead(merged, tab="Approved Leads")
                storage.record_outreach_event(
                    {
                        "lead_id": lead["id"],
                        "event_type": "draft_preview",
                        "subject": subject,
                        "body": body,
                        "draft_id": preview_id,
                        "metadata": {"recipient": recipient, "sender_email": sender_email, "mode": mode_label},
                    },
                )
                drafts.append({**lead, "draft_preview_subject": subject, "draft_preview_body": body, "send_status": "draft_preview", "draft_id": preview_id})
                continue

            draft_id = create_gmail_draft(recipient, subject, body)
            merged = {
                **lead,
                "draft_subject": subject,
                "draft_body": body,
                "send_status": "drafted",
                "draft_id": draft_id,
                "review_status": "drafted",
                "updated_at": _now(),
            }
            storage.upsert_lead(merged, tab="Approved Leads")
            storage.mark_draft_created(lead["id"], draft_id)
            storage.record_outreach_event(
                {
                    "lead_id": lead["id"],
                    "event_type": "draft_created",
                    "subject": subject,
                    "body": body,
                    "draft_id": draft_id,
                    "metadata": {"recipient": recipient, "sender_email": sender_email},
                },
            )
            drafts.append({**lead, "draft_id": draft_id, "draft_subject": subject, "draft_body": body, "send_status": "drafted"})
        storage.commit()
        return drafts
    finally:
        storage.close()
