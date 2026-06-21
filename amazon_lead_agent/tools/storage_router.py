from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from amazon_lead_agent.tools import google_sheets
from amazon_lead_agent.tools.sheet_store import SheetStore
from amazon_lead_agent.tools.sqlite_store import (
    get_connection,
    get_leads_for_drafting,
    get_leads_for_enrichment,
    get_leads_for_scoring,
    init_db,
    mark_draft_created,
    record_outreach_event,
    upsert_lead,
)
from amazon_lead_agent.normalization import validate_lead_identity_for_storage


LOGGER = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_text(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _storage_mode(config: dict[str, Any]) -> str:
    storage = config.get("storage", {}) if isinstance(config, dict) else {}
    return (_env_text("STORAGE_MODE") or str(storage.get("storage_mode") or storage.get("mode") or "sheets")).strip().lower() or "sheets"


def _local_cache_enabled(config: dict[str, Any]) -> bool:
    storage = config.get("storage", {}) if isinstance(config, dict) else {}
    if "LOCAL_CACHE_ENABLED" in os.environ:
        return _env_flag("LOCAL_CACHE_ENABLED", False)
    value = storage.get("local_cache_enabled")
    if value is None:
        return False
    return bool(value)


def _sheet_id(config: dict[str, Any]) -> str:
    storage = config.get("storage", {}) if isinstance(config, dict) else {}
    candidates = [
        _env_text("GOOGLE_SHEET_ID"),
        str(storage.get("google_sheet_id") or ""),
    ]
    for candidate in candidates:
        if candidate and candidate != "REPLACE_ME":
            return candidate
    return ""


class StorageRouter:
    def __init__(self, config: dict[str, Any], db_path: str | Path | None = None) -> None:
        self.config = config
        self.mode = _storage_mode(config)
        self.local_cache_enabled = _local_cache_enabled(config)
        self.sheet_id = _sheet_id(config)
        storage = config.get("storage", {}) if isinstance(config, dict) else {}
        self.db_path = Path(db_path or storage.get("sqlite_path") or "data/leads.db")
        self._sqlite_conn = None
        self._sheet_store = SheetStore(self.sheet_id) if self.sheet_id else None
        self.sheet_mirror_error_count = 0
        self.failed_sheet_rows: list[dict[str, Any]] = []
        self._initialized = False

        if self.mode in {"sqlite", "hybrid"} or self.local_cache_enabled or not self.sheet_id:
            if not self.sheet_id and self.mode == "sheets":
                self.mode = "sqlite"
            init_db(self.db_path)
            self._sqlite_conn = get_connection(self.db_path)

        if self._sheet_store:
            google_sheets.reset_io_stats()
            try:
                self._sheet_store.warm_tabs()
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("Sheet warm-up failed: %s", exc)

    @property
    def uses_sheets(self) -> bool:
        return self.mode in {"sheets", "hybrid"} and bool(self._sheet_store)

    @property
    def uses_sqlite(self) -> bool:
        return self._sqlite_conn is not None

    def _stage_tab(self, lead: dict[str, Any]) -> str:
        status = str(lead.get("status") or "").strip().lower()
        if status == "approved":
            return "Approved Leads"
        if status == "contact_form_queue":
            return "Contact Form Queue"
        if status == "rejected":
            return "Rejected Leads"
        return "Lead Queue"

    def _sheet_error_status(self, exc: Exception) -> int | None:
        resp = getattr(exc, "resp", None)
        status = getattr(resp, "status", None) or getattr(exc, "status_code", None)
        try:
            return int(status) if status is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _is_fatal_sheet_error(self, exc: Exception) -> bool:
        status = self._sheet_error_status(exc)
        if status in {401, 403}:
            return True
        lowered = str(exc).lower()
        return any(token in lowered for token in ("unauthorized", "forbidden", "invalid_grant", "invalid_client"))

    def _record_sheet_error(self, exc: Exception, *, tab: str, action: str, lead_id: str = "", payload: dict[str, Any] | None = None) -> None:
        context: dict[str, Any] = {
            "tab": tab,
            "action": action,
            "lead_id": lead_id,
            "status": self._sheet_error_status(exc),
            "error": str(exc),
        }
        if payload is not None:
            context["fields"] = list(payload.keys())
        self.sheet_mirror_error_count += 1
        if len(self.failed_sheet_rows) < 50:
            self.failed_sheet_rows.append(context)
        LOGGER.warning("sheet mirror failed: %s", context)

    def _upsert_sqlite(self, lead: dict[str, Any]) -> str:
        if not self._sqlite_conn:
            raise RuntimeError("SQLite cache is not enabled")
        return upsert_lead(self._sqlite_conn, lead)

    def _record_sqlite_event(self, event: dict[str, Any]) -> None:
        if not self._sqlite_conn:
            raise RuntimeError("SQLite cache is not enabled")
        record_outreach_event(self._sqlite_conn, event)

    def _get_sqlite_enrichment(self, limit: int) -> list[dict[str, Any]]:
        if not self._sqlite_conn:
            return []
        return get_leads_for_enrichment(self._sqlite_conn, limit)

    def _get_sqlite_scoring(self, limit: int) -> list[dict[str, Any]]:
        if not self._sqlite_conn:
            return []
        return get_leads_for_scoring(self._sqlite_conn, limit)

    def _get_sqlite_drafting(self, min_score: int, limit: int) -> list[dict[str, Any]]:
        if not self._sqlite_conn:
            return []
        return get_leads_for_drafting(self._sqlite_conn, min_score, limit)

    def get_all_leads(self) -> list[dict[str, Any]]:
        if self.uses_sheets:
            assert self._sheet_store is not None
            return self._sheet_store.get_all_leads()
        if not self._sqlite_conn:
            return []
        cursor = self._sqlite_conn.execute("SELECT * FROM leads")
        rows = cursor.fetchall()
        from amazon_lead_agent.tools.sqlite_store import _row_to_dict  # local import to avoid cycle

        return [_row_to_dict(row) for row in rows]

    def upsert_lead(self, lead: dict[str, Any], tab: str | None = None) -> str:
        lead, missing = validate_lead_identity_for_storage(lead)
        if missing:
            message = f"rejecting lead write: missing required fields {missing}"
            LOGGER.warning(message)
            raise ValueError(message)
        stage_tab = tab or self._stage_tab(lead)
        lead_id = str(lead.get("id") or "")
        if self.uses_sheets:
            assert self._sheet_store is not None
            try:
                lead_id = self._sheet_store.upsert_lead(lead, stage_tab) or lead_id
            except Exception as exc:  # noqa: BLE001
                self._record_sheet_error(exc, tab=stage_tab, action="upsert_lead", lead_id=lead_id, payload=lead)
                if self._is_fatal_sheet_error(exc):
                    raise
        if self.uses_sqlite:
            lead_id = self._upsert_sqlite(lead)
        return lead_id

    def record_outreach_event(self, event: dict[str, Any]) -> None:
        if self.uses_sheets:
            assert self._sheet_store is not None
            try:
                self._sheet_store.record_outreach_event(event)
            except Exception as exc:  # noqa: BLE001
                self._record_sheet_error(exc, tab="Outreach Log", action=str(event.get("event_type", "event")), lead_id=str(event.get("lead_id", "")), payload=event)
                if self._is_fatal_sheet_error(exc):
                    raise
        if self.uses_sqlite:
            self._record_sqlite_event(event)

    def mark_draft_created(self, lead_id: str, draft_id: str) -> None:
        if self._sheet_store:
            try:
                self._sheet_store.mark_draft_created(lead_id, draft_id)
            except Exception as exc:  # noqa: BLE001
                self._record_sheet_error(exc, tab="Approved Leads", action="mark_draft_created", lead_id=lead_id, payload={"draft_id": draft_id})
                if self._is_fatal_sheet_error(exc):
                    raise
        if self._sqlite_conn:
            mark_draft_created(self._sqlite_conn, lead_id, draft_id)

    def append_daily_report(self, report: dict[str, Any]) -> None:
        if self._sheet_store:
            try:
                self._sheet_store.append_daily_report(report)
            except Exception as exc:  # noqa: BLE001
                self._record_sheet_error(exc, tab="Daily Reports", action="append_daily_report", payload=report)
                if self._is_fatal_sheet_error(exc):
                    raise
        if self._sqlite_conn:
            # Daily report events are captured by record_outreach_event in sqlite mode.
            return None

    def get_leads_for_enrichment(self, limit: int) -> list[dict[str, Any]]:
        if self.uses_sheets:
            assert self._sheet_store is not None
            return self._sheet_store.get_leads_for_enrichment(limit)
        return self._get_sqlite_enrichment(limit)

    def get_leads_for_scoring(self, limit: int) -> list[dict[str, Any]]:
        if self.uses_sheets:
            assert self._sheet_store is not None
            return self._sheet_store.get_leads_for_scoring(limit)
        return self._get_sqlite_scoring(limit)

    def get_leads_for_drafting(self, min_score: int, limit: int) -> list[dict[str, Any]]:
        if self.uses_sheets:
            assert self._sheet_store is not None
            return self._sheet_store.get_leads_for_drafting(min_score, limit)
        return self._get_sqlite_drafting(min_score, limit)

    def snapshot(self) -> dict[str, Any]:
        snapshot = {
            "mode": self.mode,
            "local_cache_enabled": self.local_cache_enabled,
            "sheet_id": self.sheet_id,
            "uses_sheets": self.uses_sheets,
            "uses_sqlite": self.uses_sqlite,
            "sheet_mirror_error_count": self.sheet_mirror_error_count,
            "failed_sheet_rows": list(self.failed_sheet_rows),
        }
        if self._sheet_store:
            snapshot["sheet_store"] = self._sheet_store.snapshot()
            snapshot["sheet_flush_errors"] = list(getattr(self._sheet_store, "flush_errors", []))
        snapshot.update(google_sheets.get_io_stats())
        return snapshot

    def commit(self) -> None:
        if self._sheet_store:
            try:
                self._sheet_store.commit()
            except Exception as exc:  # noqa: BLE001
                self._record_sheet_error(exc, tab="flush", action="commit")
                if self._is_fatal_sheet_error(exc):
                    raise
        if self._sqlite_conn:
            self._sqlite_conn.commit()

    def close(self) -> None:
        if self._sqlite_conn:
            self._sqlite_conn.close()
            self._sqlite_conn = None


def get_storage_router(config: dict[str, Any], db_path: str | Path | None = None) -> StorageRouter:
    return StorageRouter(config=config, db_path=db_path)
