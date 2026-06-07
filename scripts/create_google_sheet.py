"""Create the Amazon Lead Agent Google Sheet CRM.

Usage:
    python scripts/create_google_sheet.py --title "Amazon Lead Agent CRM"

Auth options:
1. Service account JSON path:
    export GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json

2. Service account JSON content:
    export GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'

Optional sharing:
    export GOOGLE_SHEET_SHARE_WITH=you@example.com
    python scripts/create_google_sheet.py --share-with you@example.com

The script prints the created spreadsheet ID. Add it to config.yaml under:
    storage.google_sheet_id
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Iterable

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TABS: dict[str, list[str]] = {
    "Campaign Config": ["key", "value", "notes"],
    "Lead Queue": [
        "lead_id", "company_name", "brand_name", "lead_type", "category", "country",
        "marketplace", "website", "amazon_evidence_url", "amazon_evidence_summary",
        "amazon_backlink_found", "decision_maker_name", "decision_maker_title",
        "decision_maker_source_url", "email_or_contact_path", "email_status",
        "source_urls", "pain_points", "lead_score", "lead_tier", "confidence",
        "outreach_angle", "draft_subject", "draft_body", "draft_preview_subject",
        "draft_preview_body", "review_status", "send_status", "extraction_method",
        "draft_created_at", "last_checked", "created_at",
    ],
    "Approved Leads": [
        "lead_id", "company_name", "website", "category", "amazon_evidence_url",
        "decision_maker_name", "email_or_contact_path", "lead_score", "lead_tier",
        "outreach_angle", "draft_subject", "draft_body", "draft_preview_subject",
        "draft_preview_body", "review_status", "send_status", "extraction_method",
        "created_at",
    ],
    "Contact Form Queue": [
        "lead_id", "company_name", "website", "category", "contact_page_url",
        "lead_score", "lead_tier", "review_status", "send_status", "extraction_method",
        "created_at",
    ],
    "Outreach Log": [
        "event_id", "lead_id", "company_name", "recipient", "subject", "gmail_draft_id",
        "event_type", "event_status", "notes", "created_at",
    ],
    "Rejected Leads": [
        "lead_id", "company_name", "website", "reason", "lead_score", "confidence",
        "source_urls", "created_at",
    ],
    "Daily Reports": [
        "report_date", "campaign", "discovered_count", "enriched_count", "scored_count",
        "approved_count", "rejected_count", "drafts_created", "contact_form_queue_count",
        "extraction_fallback_count", "errors", "top_leads", "notes",
    ],
}

CONFIG_ROWS = [
    ["target", "DTC brands that mention Amazon availability or link to Amazon storefronts", ""],
    ["categories", "beauty, pet, home, supplements", ""],
    ["daily_discovery_limit", "50", ""],
    ["daily_draft_limit", "10", ""],
    ["minimum_score_for_draft", "75", ""],
    ["minimum_score_for_auto_send", "90", "reserved; v1 creates drafts only"],
    ["sender_name", "Zaigham Ali", ""],
    ["sender_website", "https://zaighamali.com", ""],
    ["sender_linkedin", "https://linkedin.com/zaighamali-", ""],
    [
        "service_offer",
        "We take the messy, time-consuming operational work off your plate and keep your Amazon account clean, compliant, and conversion-ready.",
        "",
    ],
]


def credentials() -> Credentials:
    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    json_content = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_path:
        return Credentials.from_service_account_file(json_path, scopes=SCOPES)
    if json_content:
        return Credentials.from_service_account_info(json.loads(json_content), scopes=SCOPES)
    raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON.")


def format_header(sheet: gspread.Worksheet, headers: Iterable[str]) -> None:
    headers = list(headers)
    if not headers:
        return
    end_col = chr(64 + min(len(headers), 26))
    sheet.format(
        f"A1:{end_col}1",
        {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
        },
    )
    sheet.freeze(rows=1)


def create_sheet(title: str, share_with: str | None = None) -> str:
    client = gspread.authorize(credentials())
    spreadsheet = client.create(title)
    first = True
    for tab_name, headers in TABS.items():
        if first:
            sheet = spreadsheet.sheet1
            sheet.update_title(tab_name)
            first = False
        else:
            sheet = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=max(26, len(headers)))
        sheet.update([headers], "A1")
        format_header(sheet, headers)
    spreadsheet.worksheet("Campaign Config").append_rows(CONFIG_ROWS, value_input_option="USER_ENTERED")
    if share_with:
        spreadsheet.share(share_with, perm_type="user", role="writer")
    return spreadsheet.id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="Amazon Lead Agent CRM")
    parser.add_argument("--share-with", default=os.getenv("GOOGLE_SHEET_SHARE_WITH"))
    args = parser.parse_args()
    spreadsheet_id = create_sheet(args.title, args.share_with)
    print("Created Google Sheet CRM")
    print(f"Spreadsheet ID: {spreadsheet_id}")
    print(f"URL: https://docs.google.com/spreadsheets/d/{spreadsheet_id}")


if __name__ == "__main__":
    main()
