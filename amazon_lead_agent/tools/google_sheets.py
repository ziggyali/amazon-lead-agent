from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_credentials():
    import os
    from google.oauth2 import service_account

    file_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    json_blob = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if file_path:
        return service_account.Credentials.from_service_account_file(file_path, scopes=scopes)
    if json_blob:
        return service_account.Credentials.from_service_account_info(json.loads(json_blob), scopes=scopes)
    raise RuntimeError("Google service account credentials are not configured.")


def _build_service():
    from googleapiclient.discovery import build

    creds = _load_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _ensure_tab_exists(service, sheet_id: str, tab: str) -> None:
    spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    for existing in spreadsheet.get("sheets", []):
        properties = existing.get("properties", {})
        if properties.get("title") == tab:
            return
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
    ).execute()


def _rows_to_dicts(headers: list[str], rows: list[list[str]]) -> list[dict]:
    results: list[dict] = []
    for row in rows:
        item = {headers[index]: row[index] if index < len(row) else "" for index in range(len(headers))}
        results.append(item)
    return results


def _ensure_headers(service, sheet_id: str, tab: str, headers: list[str]) -> None:
    _ensure_tab_exists(service, sheet_id, tab)
    values_api = service.spreadsheets().values()
    range_name = f"{tab}!1:1"
    existing = values_api.get(spreadsheetId=sheet_id, range=range_name).execute().get("values", [])
    if existing:
        return
    values_api.update(
        spreadsheetId=sheet_id,
        range=range_name,
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def _upsert_row(service, sheet_id: str, tab: str, item: dict) -> None:
    key_name = "id" if "id" in item else "lead_id" if "lead_id" in item else None
    headers = list(item.keys())
    if key_name and key_name in headers:
        headers = [key_name] + [header for header in headers if header != key_name]
    _ensure_headers(service, sheet_id, tab, headers)
    values_api = service.spreadsheets().values()
    existing = values_api.get(spreadsheetId=sheet_id, range=f"{tab}!A:ZZ").execute().get("values", [])
    if not existing:
        values_api.append(
            spreadsheetId=sheet_id,
            range=tab,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[item.get(header, "") for header in headers]]},
        ).execute()
        return
    header_row = existing[0]
    rows = existing[1:]
    key = str(item.get("id") or item.get("lead_id") or "")
    if not key:
        values_api.append(
            spreadsheetId=sheet_id,
            range=tab,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[item.get(header, "") for header in header_row]]},
        ).execute()
        return
    for index, row in enumerate(rows, start=2):
        if row and row[0] == key:
            values_api.update(
                spreadsheetId=sheet_id,
                range=f"{tab}!A{index}",
                valueInputOption="RAW",
                body={"values": [[item.get(header, "") for header in header_row]]},
            ).execute()
            return
    values_api.append(
        spreadsheetId=sheet_id,
        range=tab,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[item.get(header, "") for header in header_row]]},
    ).execute()


def append_or_update_lead(sheet_id: str, tab: str, lead: dict) -> None:
    service = _build_service()
    _upsert_row(service, sheet_id, tab, lead)


def append_outreach_log(sheet_id: str, event: dict) -> None:
    service = _build_service()
    payload = {
        "lead_id": event.get("lead_id", ""),
        "event_type": event.get("event_type", ""),
        "subject": event.get("subject", ""),
        "draft_id": event.get("draft_id", ""),
        "metadata": json.dumps(event.get("metadata", {}), sort_keys=True),
        "created_at": event.get("created_at", _utc_now()),
    }
    _upsert_row(service, sheet_id, "Outreach Log", payload)


def append_daily_report(sheet_id: str, report: dict) -> None:
    service = _build_service()
    payload = {
        "report_date": report.get("report_date", _utc_now()[:10]),
        "discovery_count": report.get("discovery_count", 0),
        "enrichment_count": report.get("enrichment_count", 0),
        "scoring_count": report.get("scoring_count", 0),
        "draft_count": report.get("draft_count", 0),
        "notes": report.get("notes", ""),
        "created_at": report.get("created_at", _utc_now()),
    }
    _upsert_row(service, sheet_id, "Daily Reports", payload)
