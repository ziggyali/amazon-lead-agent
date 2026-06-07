from __future__ import annotations

from pathlib import Path

from amazon_lead_agent.tools.gmail_drafts import create_gmail_draft
from amazon_lead_agent.tools.google_sheets import append_or_update_lead, append_outreach_log
from amazon_lead_agent.tools.sqlite_store import get_connection, get_leads_for_drafting, mark_draft_created, record_outreach_event, upsert_lead


def compose_subject(lead: dict) -> str:
    brand = lead.get("brand_name") or lead.get("company_name") or "your brand"
    return f"Quick idea for {brand}'s Amazon growth"


def compose_body(lead: dict, sender_name: str, sender_offer: str) -> str:
    brand = lead.get("brand_name") or lead.get("company_name") or "your team"
    website = lead.get("website") or ""
    amazon_summary = lead.get("amazon_evidence_summary") or "I noticed you have a public Amazon presence."
    contact_note = "I found a public contact path on your site." if (lead.get("public_emails") or lead.get("contact_page_url")) else "I found a public way to get in touch."
    return "\n".join(
        [
            f"Hi {brand} team,",
            "",
            f"I came across {website} and noticed {amazon_summary}.",
            contact_note,
            "",
            sender_offer,
            "",
            f"Best,",
            sender_name,
        ]
    )


def run_outreach(config: dict, db_path: Path) -> list[dict]:
    conn = get_connection(db_path)
    drafts: list[dict] = []
    try:
        sheet_id = config["storage"].get("google_sheet_id", "")
        if sheet_id in {"", "REPLACE_ME"}:
            sheet_id = ""
        min_score = int(config["campaign"]["minimum_score_for_draft"])
        limit = int(config["campaign"]["daily_draft_limit"])
        candidates = get_leads_for_drafting(conn, min_score=min_score, limit=limit)
        sender_name = config["sender"]["name"]
        sender_offer = config["sender"]["offer"]
        sender_email = __import__("os").environ.get("GMAIL_SENDER_EMAIL", "")

        for lead in candidates:
            if lead.get("drafted"):
                continue
            subject = compose_subject(lead)
            body = compose_body(lead, sender_name, sender_offer)
            recipient = (lead.get("public_emails") or [""])[0]
            if not recipient:
                continue
            draft_id = create_gmail_draft(recipient, subject, body)
            mark_draft_created(conn, lead["id"], draft_id)
            record_outreach_event(
                conn,
                {
                    "lead_id": lead["id"],
                    "event_type": "draft_created",
                    "subject": subject,
                    "body": body,
                    "draft_id": draft_id,
                    "metadata": {"recipient": recipient, "sender_email": sender_email},
                },
            )
            if sheet_id:
                append_or_update_lead(sheet_id, "Approved Leads", {**lead, "draft_id": draft_id, "status": "drafted"})
                append_outreach_log(sheet_id, {"lead_id": lead["id"], "event_type": "draft_created", "subject": subject, "draft_id": draft_id})
            drafts.append({**lead, "draft_id": draft_id})
        conn.commit()
        return drafts
    finally:
        conn.close()
