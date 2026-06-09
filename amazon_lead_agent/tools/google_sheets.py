from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import logging
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Any

LOGGER = logging.getLogger(__name__)

SPREADSHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
MAX_CELL_LENGTH = 5000
MAX_URL_CELL_LENGTH = 1000
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _new_io_stats() -> dict[str, Any]:
    return {
        "sheet_read_error_count": 0,
        "sheet_read_retry_count": 0,
        "sheet_connection_error_count": 0,
        "failed_sheet_reads": [],
    }


_LAST_IO_STATS = _new_io_stats()


def reset_io_stats() -> None:
    global _LAST_IO_STATS
    _LAST_IO_STATS = _new_io_stats()


def get_io_stats() -> dict[str, Any]:
    stats = dict(_LAST_IO_STATS)
    stats["failed_sheet_reads"] = list(stats.get("failed_sheet_reads", []))
    return stats


def _auth_mode() -> str:
    return os.environ.get("GOOGLE_SHEETS_AUTH_MODE", "auto").strip().lower() or "auto"


def _service_account_configured() -> bool:
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip())


def _oauth_configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CREDENTIALS_FILE", "").strip() or os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _header_limit(header: str, value: Any) -> int:
    header_lower = (header or "").lower()
    if any(token in header_lower for token in ("url", "link", "website", "domain", "uri", "source_urls", "contact_page")):
        return MAX_URL_CELL_LENGTH
    if isinstance(value, (list, tuple, set, dict)):
        return MAX_URL_CELL_LENGTH
    return MAX_CELL_LENGTH


def _compact_text(text: str, limit: int) -> str:
    cleaned = CONTROL_CHAR_RE.sub("", unescape(str(text)))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 3].rstrip() + "..."
    return cleaned


def _serialize_sheet_value(header: str, value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    limit = _header_limit(header, value)
    if isinstance(value, (list, tuple, set)):
        serialized = json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
        return _compact_text(serialized, limit)
    if isinstance(value, dict):
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return _compact_text(serialized, limit)
    return _compact_text(str(value), limit)


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: _serialize_sheet_value(key, value) for key, value in payload.items()}


def _load_service_account_credentials():
    from google.oauth2 import service_account

    file_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    json_blob = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    scopes = [SPREADSHEETS_SCOPE]
    if file_path:
        return service_account.Credentials.from_service_account_file(file_path, scopes=scopes)
    if json_blob:
        return service_account.Credentials.from_service_account_info(json.loads(json_blob), scopes=scopes)
    raise RuntimeError("Google service account credentials are not configured.")


def _load_oauth_credentials() -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    credentials_file = os.environ.get("GOOGLE_OAUTH_CREDENTIALS_FILE", "").strip()
    token_file = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "").strip()
    scopes = [SPREADSHEETS_SCOPE]

    creds = None
    token_path = Path(token_file) if token_file else None
    if token_path and token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        if creds and creds.valid:
            return creds

    if not credentials_file:
        raise RuntimeError("Google OAuth credentials are not configured.")

    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes=scopes)
    try:
        creds = flow.run_local_server(port=0)
    except Exception as exc:  # noqa: BLE001
        if hasattr(flow, "run_console"):
            try:
                creds = flow.run_console()
            except Exception as console_exc:  # noqa: BLE001
                raise RuntimeError(f"OAuth flow failed: {exc}; {console_exc}") from console_exc
        else:
            raise RuntimeError(f"OAuth flow failed: {exc}") from exc
    if token_path:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _load_credentials(auth_mode: str | None = None):
    mode = (auth_mode or _auth_mode()).strip().lower()
    if mode == "service_account":
        return _load_service_account_credentials()
    if mode == "oauth":
        return _load_oauth_credentials()

    service_configured = _service_account_configured()
    oauth_configured = _oauth_configured()

    if service_configured:
        try:
            return _load_service_account_credentials()
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("service account auth failed, trying OAuth fallback: %s", exc)

    if oauth_configured:
        return _load_oauth_credentials()

    if service_configured:
        return _load_service_account_credentials()
    raise RuntimeError("Google Sheets credentials are not configured for service account or OAuth auth modes.")


def _build_service(auth_mode: str | None = None):
    from googleapiclient.discovery import build

    creds = _load_credentials(auth_mode=auth_mode)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _is_auth_error(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
    lowered = str(exc).lower()
    if status is not None:
        try:
            if int(status) in {401, 403}:
                return True
        except Exception:  # noqa: BLE001
            pass
    return any(token in lowered for token in ("invalid_grant", "invalid_client", "unauthorized", "forbidden", "permission denied"))


def _is_retryable_read_error(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
    status_code = int(status) if status is not None else None
    if status_code in {429, 500, 502, 503, 504}:
        return True
    winerror = getattr(exc, "winerror", None) or getattr(getattr(exc, "__cause__", None), "winerror", None)
    err_no = getattr(exc, "errno", None) or getattr(getattr(exc, "__cause__", None), "errno", None)
    lowered = str(exc).lower()
    return any(
        [
            winerror in {10054, 10060, 10061, 10065},
            err_no in {10054, 10060, 10061, 10065},
            isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)),
            any(token in lowered for token in ("timed out", "timeout", "connection reset", "connection aborted", "network is unreachable", "temporary failure in name resolution")),
        ]
    )


def _record_read_failure(sheet_id: str, tab: str, exc: Exception) -> None:
    _LAST_IO_STATS["sheet_read_error_count"] += 1
    winerror = getattr(exc, "winerror", None) or getattr(getattr(exc, "__cause__", None), "winerror", None)
    err_no = getattr(exc, "errno", None) or getattr(getattr(exc, "__cause__", None), "errno", None)
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
    if winerror in {10054, 10060, 10061, 10065} or err_no in {10054, 10060, 10061, 10065} or isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        _LAST_IO_STATS["sheet_connection_error_count"] += 1
    _LAST_IO_STATS["failed_sheet_reads"].append({"sheet_id": sheet_id, "tab": tab, "status": status, "error": str(exc)})


def read_tab_rows(sheet_id: str, tab: str, auth_mode: str | None = None) -> list[dict[str, Any]]:
    delay = 0.5
    for attempt in range(5):
        try:
            service = _build_service(auth_mode=auth_mode)
            values = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"{tab}!A:ZZ").execute().get("values", [])
            if not values:
                return []
            headers = values[0]
            rows: list[dict[str, Any]] = []
            for row in values[1:]:
                item = {headers[index]: row[index] if index < len(row) else "" for index in range(len(headers))}
                rows.append(item)
            return rows
        except Exception as exc:  # noqa: BLE001
            if _is_auth_error(exc):
                raise RuntimeError(f"Google Sheets auth failed while reading {tab}: {exc}") from exc
            if _is_retryable_read_error(exc) and attempt < 4:
                _LAST_IO_STATS["sheet_read_retry_count"] += 1
                _record_read_failure(sheet_id, tab, exc)
                LOGGER.info("sheet read retry sheet_id=%s tab=%s attempt=%s error=%s", sheet_id, tab, attempt + 1, exc)
                time.sleep(delay + (delay * 0.2))
                delay *= 2
                continue
            _record_read_failure(sheet_id, tab, exc)
            LOGGER.warning("sheet read failed sheet_id=%s tab=%s error=%s", sheet_id, tab, exc)
            return []


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
    item = _sanitize_payload(item)
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
    event = _sanitize_payload(event)
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
    report = _sanitize_payload(report)
    payload = {
        "report_date": report.get("report_date", _utc_now()[:10]),
        "campaign": report.get("campaign", "Amazon Lead Agent"),
        "discovery_count": report.get("discovery_count", 0),
        "enrichment_count": report.get("enrichment_count", 0),
        "scoring_count": report.get("scoring_count", 0),
        "approved_count": report.get("approved_count", 0),
        "rejected_count": report.get("rejected_count", 0),
        "draft_count": report.get("draft_count", report.get("drafts_created", 0)),
        "contact_form_queue_count": report.get("contact_form_queue_count", 0),
        "extraction_fallback_count": report.get("extraction_fallback_count", 0),
        "errors": report.get("errors", 0),
        "notes": report.get("notes", ""),
        "created_at": report.get("created_at", _utc_now()),
    }
    _upsert_row(service, sheet_id, "Daily Reports", payload)
