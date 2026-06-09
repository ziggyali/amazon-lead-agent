from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any
import socket

from amazon_lead_agent.normalization import make_lead_id
from amazon_lead_agent.tools import google_sheets


LOGGER = logging.getLogger(__name__)

LEAD_TABS = ("Lead Queue", "Approved Leads", "Contact Form Queue", "Rejected Leads")
REPORT_TABS = ("Daily Reports", "Outreach Log", "Campaign Config")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lead_key(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("lead_id") or "").strip()


def _lead_domain(row: dict[str, Any]) -> str:
    value = str(row.get("normalized_domain") or row.get("website") or "").strip().lower()
    return value


def _canonical_tab_name(tab: str) -> str:
    return tab.strip()


@dataclass
class SheetStore:
    sheet_id: str
    auth_mode: str | None = None
    _tab_cache: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _headers_cache: dict[str, list[str]] = field(default_factory=dict)
    _lead_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    _domain_index: dict[str, str] = field(default_factory=dict)
    _dirty_tabs: set[str] = field(default_factory=set)
    _pending_leads: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _pending_events: list[dict[str, Any]] = field(default_factory=list)
    _pending_reports: list[dict[str, Any]] = field(default_factory=list)
    flush_errors: list[dict[str, Any]] = field(default_factory=list)

    def warm_tabs(self, tabs: list[str] | tuple[str, ...] | None = None) -> None:
        for tab in tabs or (*LEAD_TABS, *REPORT_TABS):
            self.read_tab(tab, refresh=True)

    def read_tab(self, tab: str, refresh: bool = False) -> list[dict[str, Any]]:
        tab = _canonical_tab_name(tab)
        if not refresh and tab in self._tab_cache:
            return [dict(row) for row in self._tab_cache[tab]]
        rows = google_sheets.read_tab_rows(self.sheet_id, tab)
        self._tab_cache[tab] = [dict(row) for row in rows]
        if rows:
            self._headers_cache[tab] = list(rows[0].keys())
        for row in rows:
            self._index_row(tab, row)
        return [dict(row) for row in rows]

    def _index_row(self, tab: str, row: dict[str, Any]) -> None:
        lead_id = _lead_key(row)
        if lead_id:
            self._lead_index[lead_id] = {**self._lead_index.get(lead_id, {}), **row, "_tab": tab}
        domain = _lead_domain(row)
        if lead_id and domain:
            self._domain_index[domain] = lead_id

    def _remember_row(self, tab: str, row: dict[str, Any]) -> None:
        tab = _canonical_tab_name(tab)
        cached = self._tab_cache.setdefault(tab, [])
        lead_id = _lead_key(row)
        if lead_id:
            for index, existing in enumerate(cached):
                if _lead_key(existing) == lead_id:
                    cached[index] = dict(row)
                    self._index_row(tab, row)
                    self._dirty_tabs.add(tab)
                    return
        cached.append(dict(row))
        self._index_row(tab, row)
        self._dirty_tabs.add(tab)

    def _queue_lead(self, tab: str, row: dict[str, Any]) -> None:
        lead_id = _lead_key(row)
        queue = self._pending_leads.setdefault(tab, {})
        if lead_id:
            queue[lead_id] = dict(row)
        else:
            queue[f"{len(queue)}-{tab}"] = dict(row)

    def _queue_event(self, event: dict[str, Any]) -> None:
        self._pending_events.append(dict(event))

    def _queue_report(self, report: dict[str, Any]) -> None:
        self._pending_reports.append(dict(report))

    def get_all_leads(self) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for tab in LEAD_TABS:
            for row in self.read_tab(tab):
                lead_id = _lead_key(row)
                if not lead_id:
                    continue
                merged[lead_id] = {**merged.get(lead_id, {}), **row}
        return list(merged.values())

    def get_lead(self, lead_id: str) -> dict[str, Any] | None:
        if not lead_id:
            return None
        if lead_id in self._lead_index:
            return dict(self._lead_index[lead_id])
        for row in self.get_all_leads():
            if _lead_key(row) == lead_id:
                return dict(row)
        return None

    def upsert_lead(self, lead: dict[str, Any], tab: str = "Lead Queue") -> str:
        canonical = _canonical_tab_name(tab)
        payload = dict(lead)
        if not payload.get("id"):
            company_name = payload.get("company_name") or payload.get("brand_name") or payload.get("website") or ""
            website = payload.get("website") or ""
            source = (payload.get("source_urls") or [payload.get("primary_source_url") or website or ""])[0]
            payload["id"] = make_lead_id(str(company_name), str(website), str(source))
        if not payload.get("updated_at"):
            payload["updated_at"] = _utc_now()
        self._remember_row(canonical, payload)
        self._queue_lead(canonical, payload)
        status = str(payload.get("status") or "").strip().lower()
        if canonical != "Lead Queue":
            self._remember_row("Lead Queue", payload)
            self._queue_lead("Lead Queue", payload)
        if status == "approved":
            self._remember_row("Approved Leads", payload)
            self._queue_lead("Approved Leads", payload)
        elif status == "contact_form_queue":
            self._remember_row("Contact Form Queue", payload)
            self._queue_lead("Contact Form Queue", payload)
        elif status == "rejected":
            self._remember_row("Rejected Leads", payload)
            self._queue_lead("Rejected Leads", payload)
        return str(payload.get("id") or "")

    def record_outreach_event(self, event: dict[str, Any]) -> None:
        self._remember_row(
            "Outreach Log",
            {
                "lead_id": event.get("lead_id", ""),
                "event_type": event.get("event_type", ""),
                "subject": event.get("subject", ""),
                "draft_id": event.get("draft_id", ""),
                "metadata": event.get("metadata", {}),
                "created_at": event.get("created_at", _utc_now()),
            },
        )
        self._queue_event(event)

    def append_daily_report(self, report: dict[str, Any]) -> None:
        self._remember_row("Daily Reports", report)
        self._queue_report(report)

    def mark_draft_created(self, lead_id: str, draft_id: str) -> None:
        lead = self.get_lead(lead_id) or {"id": lead_id}
        lead.update(
            {
                "draft_id": draft_id,
                "drafted": 1,
                "status": "drafted",
                "send_status": "drafted",
                "updated_at": _utc_now(),
            }
        )
        self.upsert_lead(lead, tab="Approved Leads")

    def get_leads_for_enrichment(self, limit: int) -> list[dict[str, Any]]:
        leads = [lead for lead in self.get_all_leads() if str(lead.get("status", "")).lower() in {"new", "discovered", "needs_enrichment", "extraction_error"}]
        leads.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
        return leads[:limit]

    def get_leads_for_scoring(self, limit: int) -> list[dict[str, Any]]:
        leads = [lead for lead in self.get_all_leads() if str(lead.get("status", "")).lower() in {"enriched", "extraction_error", "discovered", "scoring_error"}]
        leads.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
        return leads[:limit]

    def get_leads_for_drafting(self, min_score: int, limit: int) -> list[dict[str, Any]]:
        leads = [
            lead
            for lead in self.get_all_leads()
            if int(lead.get("drafted") or 0) == 0
            and int(lead.get("score") or 0) >= min_score
            and str(lead.get("extraction_method") or "").strip().lower() != "blocked_or_error"
            and bool(lead.get("has_email") or lead.get("public_emails"))
            and bool(lead.get("contact_path_exists") or lead.get("contact_page_url"))
            and bool(lead.get("amazon_backlink_found") or lead.get("amazon_links"))
            and str(lead.get("status", "")).lower() in {"scored", "enriched", "approved", "contact_form_queue"}
        ]
        leads.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("updated_at") or item.get("created_at") or "")))
        return leads[:limit]

    def _write_with_backoff(self, writer, *, label: str) -> None:
        delay = 0.5
        for attempt in range(5):
            try:
                writer()
                return
            except Exception as exc:  # noqa: BLE001
                status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
                status_code = int(status) if status is not None else None
                if status_code in {429, 500, 502, 503, 504} and attempt < 4:
                    LOGGER.info("sheet write retry label=%s attempt=%s status=%s", label, attempt + 1, status)
                    import random
                    import time

                    time.sleep(delay + random.random() * 0.25)
                    delay *= 2
                    continue
                winerror = getattr(exc, "winerror", None) or getattr(getattr(exc, "__cause__", None), "winerror", None)
                err_no = getattr(exc, "errno", None) or getattr(getattr(exc, "__cause__", None), "errno", None)
                message = str(exc).lower()
                retryable_connection_error = any(
                    [
                        winerror in {10054, 10060, 10061, 10065},
                        err_no in {10054, 10060, 10061, 10065},
                        isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)),
                        any(token in message for token in ("timed out", "timeout", "connection reset", "connection aborted", "network is unreachable", "temporary failure in name resolution")),
                    ]
                )
                if retryable_connection_error and attempt < 4:
                    LOGGER.info("sheet connection retry label=%s attempt=%s error=%s", label, attempt + 1, exc)
                    import random
                    import time

                    time.sleep(delay + random.random() * 0.25)
                    delay *= 2
                    continue
                raise

    def flush(self) -> None:
        if not self.sheet_id:
            return
        for tab, rows in list(self._pending_leads.items()):
            for row in list(rows.values()):
                try:
                    self._write_with_backoff(
                        lambda row=row, tab=tab: google_sheets.append_or_update_lead(self.sheet_id, tab, row),
                        label=f"{tab}:lead",
                    )
                except Exception as exc:  # noqa: BLE001
                    context = {"tab": tab, "kind": "lead", "error": str(exc), "lead_id": _lead_key(row)}
                    self.flush_errors.append(context)
                    LOGGER.warning("sheet flush failed: %s", context)
        for event in list(self._pending_events):
            try:
                self._write_with_backoff(
                    lambda event=event: google_sheets.append_outreach_log(self.sheet_id, event),
                    label="Outreach Log:event",
                )
            except Exception as exc:  # noqa: BLE001
                context = {"tab": "Outreach Log", "kind": "event", "error": str(exc), "lead_id": str(event.get("lead_id", ""))}
                self.flush_errors.append(context)
                LOGGER.warning("sheet flush failed: %s", context)
        for report in list(self._pending_reports):
            try:
                self._write_with_backoff(
                    lambda report=report: google_sheets.append_daily_report(self.sheet_id, report),
                    label="Daily Reports:report",
                )
            except Exception as exc:  # noqa: BLE001
                context = {"tab": "Daily Reports", "kind": "report", "error": str(exc)}
                self.flush_errors.append(context)
                LOGGER.warning("sheet flush failed: %s", context)
        self._pending_leads.clear()
        self._pending_events.clear()
        self._pending_reports.clear()

    def commit(self) -> None:
        self.flush()
        return None

    def close(self) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "lead_count": len(self._lead_index),
            "dirty_tabs": sorted(self._dirty_tabs),
            "tabs": {tab: len(rows) for tab, rows in self._tab_cache.items()},
        }
