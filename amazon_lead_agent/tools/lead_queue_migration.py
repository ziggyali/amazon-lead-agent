from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any

from amazon_lead_agent.lead_filters import is_blocked_domain, is_tracking_or_search_domain
from amazon_lead_agent.normalization import infer_brand_name_from_domain, make_lead_id, normalize_company_name, normalize_domain, validate_lead_identity_for_storage


CANONICAL_BRAND_WEBSITES = {
    "Glossier": "glossier.com",
    "Cocokind": "cocokind.com",
    "Tatcha": "tatcha.com",
    "The Honest Kitchen": "thehonestkitchen.com",
    "Wild One": "wildone.com",
    "Fable Pets": "fablepets.com",
    "Brooklinen": "brooklinen.com",
    "Parachute Home": "parachutehome.com",
}

LEAD_QUEUE_STATUSES = {"", "new", "discovered", "needs_enrichment", "extraction_error", "scored", "scoring_error"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_domainish(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        return raw
    if " " in raw and "." not in raw:
        raw = raw.replace(" ", ".")
    return raw


def _brand_from_value(value: str | None) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    if cleaned in CANONICAL_BRAND_WEBSITES:
        return cleaned
    domainish = _coerce_domainish(cleaned)
    inferred = infer_brand_name_from_domain(domainish or cleaned)
    if inferred:
        return inferred
    return cleaned


def _is_available_variant(value: str | None) -> bool:
    normalized = normalize_company_name(value)
    if normalized == "available":
        return True
    raw = str(value or "").strip().lower()
    return raw.startswith("available") or raw == "available"


def _row_junk_reason(row: dict[str, Any]) -> str:
    values = [
        row.get("seed_label"),
        row.get("canonical_brand_name"),
        row.get("brand_name"),
        row.get("company_name"),
    ]
    if any("testbrand" in normalize_company_name(value) or "testbrand" in str(value or "").strip().lower() for value in values):
        return "testbrand_row"
    if any(_is_available_variant(value) for value in values):
        return "available_row"
    website = str(row.get("website") or row.get("primary_source_url") or "").strip()
    if website and (is_blocked_domain(website) or is_tracking_or_search_domain(website)):
        return "blocked_domain_row"
    brand_key = normalize_company_name(row.get("canonical_brand_name") or row.get("brand_name") or row.get("company_name") or "")
    if not brand_key and not website:
        return "empty_row"
    return ""


def _brand_key(row: dict[str, Any]) -> str:
    brand = (
        row.get("canonical_brand_name")
        or row.get("seed_label")
        or row.get("brand_name")
        or row.get("company_name")
        or infer_brand_name_from_domain(row.get("website") or row.get("primary_source_url") or "")
    )
    return normalize_company_name(brand)


def _selection_score(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    manual = 1 if str(row.get("manual_amazon_evidence_url") or "").strip() else 0
    cached = 1 if str(row.get("best_evidence_url") or "").strip() else 0
    needs = 1 if str(row.get("status") or "").strip().lower() == "needs_enrichment" else 0
    website = 1 if str(row.get("website") or "").strip() else 0
    completeness = sum(1 for field in ("lead_id", "brand_name", "canonical_brand_name", "website", "category", "status") if str(row.get(field) or "").strip())
    updated_at = str(row.get("updated_at") or row.get("created_at") or "")
    return (manual, cached, needs, website, f"{completeness:02d}|{updated_at}")


def _mark_row_rejected(row: dict[str, Any], reason: str) -> dict[str, Any]:
    repaired = dict(row)
    repaired["status"] = "rejected"
    repaired["send_status"] = "not_eligible"
    repaired["tier"] = "Reject"
    repaired["cleanup_reason"] = reason
    repaired["review_status"] = "rejected"
    repaired["updated_at"] = _utc_now()
    return repaired


def _website_from_row(row: dict[str, Any], brand_name: str) -> str:
    candidates = [
        row.get("website"),
        row.get("normalized_domain"),
        row.get("primary_source_url"),
    ]
    for field in ("canonical_brand_name", "brand_name", "company_name", "seed_label"):
        value = row.get(field)
        if value:
            candidates.append(value)
    for candidate in candidates:
        if not candidate:
            continue
        raw = str(candidate).strip()
        if not raw:
            continue
        if raw in CANONICAL_BRAND_WEBSITES:
            return f"https://{CANONICAL_BRAND_WEBSITES[raw]}"
        if "://" in raw or "." in raw:
            normalized = normalize_domain(_coerce_domainish(raw) or raw)
            if normalized and normalized not in {"", "com"}:
                return f"https://{normalized}"
    slug = CANONICAL_BRAND_WEBSITES.get(brand_name, "")
    if slug:
        return f"https://{slug}"
    inferred = infer_brand_name_from_domain(brand_name)
    if inferred and inferred in CANONICAL_BRAND_WEBSITES:
        return f"https://{CANONICAL_BRAND_WEBSITES[inferred]}"
    return ""


def repair_lead_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    payload, _ = validate_lead_identity_for_storage(row)
    candidate_brand = (
        str(payload.get("canonical_brand_name") or "").strip()
        or str(payload.get("seed_label") or "").strip()
        or str(payload.get("brand_name") or "").strip()
        or str(payload.get("company_name") or "").strip()
        or str(payload.get("website") or "").strip()
        or str(payload.get("normalized_domain") or "").strip()
    )
    brand_name = _brand_from_value(candidate_brand)
    website = str(payload.get("website") or "").strip()
    if not website:
        website = _website_from_row(payload, brand_name)
    if not website and brand_name:
        website = _website_from_row({"canonical_brand_name": brand_name}, brand_name)
    lead_id = str(payload.get("lead_id") or payload.get("id") or "").strip()
    if not lead_id:
        source = str((payload.get("source_urls") or [payload.get("primary_source_url") or website or ""])[0] or "")
        lead_id = make_lead_id(brand_name or payload.get("company_name") or payload.get("website") or "", website or payload.get("website") or "", source)
    repaired = dict(payload)
    repaired["id"] = lead_id
    repaired["lead_id"] = lead_id
    repaired["brand_name"] = brand_name or repaired.get("brand_name") or ""
    repaired["company_name"] = brand_name or repaired.get("company_name") or ""
    repaired["canonical_brand_name"] = brand_name or repaired.get("canonical_brand_name") or ""
    repaired["website"] = website or repaired.get("website") or ""
    repaired["status"] = str(repaired.get("status") or "").strip() or "needs_enrichment"
    repaired["updated_at"] = _utc_now()
    if not repaired.get("category"):
        repaired["category"] = str(row.get("category") or "").strip()
    if not repaired.get("seed_label") and repaired.get("canonical_brand_name"):
        repaired["seed_label"] = repaired["canonical_brand_name"]
    if not repaired.get("send_status"):
        repaired["send_status"] = "not_eligible"
    return repaired


@dataclass
class MigrationSummary:
    rows_seen: int = 0
    rows_changed: int = 0
    rows_skipped: int = 0
    repaired_rows: list[dict[str, Any]] = field(default_factory=list)
    junk_rows: int = 0
    duplicate_rows: int = 0


def _migration_signature(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("lead_id") or row.get("id") or "").strip(),
        str(row.get("brand_name") or "").strip(),
        str(row.get("canonical_brand_name") or "").strip(),
        str(row.get("website") or "").strip(),
        str(row.get("status") or "").strip(),
    )


def migrate_lead_queue_rows(storage: Any, dry_run: bool = True, delete_junk: bool = False) -> MigrationSummary:
    summary = MigrationSummary()
    if hasattr(storage, "read_lead_queue_rows"):
        rows = list(storage.read_lead_queue_rows(refresh=True))
    elif hasattr(storage, "get_all_leads"):
        rows = list(storage.get_all_leads())
    else:
        rows = []
    repaired_rows: list[dict[str, Any]] = []
    for row in rows:
        summary.rows_seen += 1
        status = str(row.get("status") or "").strip().lower()
        if status and status not in LEAD_QUEUE_STATUSES:
            summary.rows_skipped += 1
            continue
        repaired = repair_lead_queue_row(row)
        junk_reason = _row_junk_reason(repaired)
        if junk_reason:
            if delete_junk and hasattr(storage, "delete_lead"):
                summary.junk_rows += 1
                if not dry_run:
                    storage.delete_lead(repaired.get("lead_id") or repaired.get("id") or "")
                continue
            repaired = _mark_row_rejected(repaired, junk_reason)
            summary.junk_rows += 1
        else:
            repaired["status"] = "needs_enrichment"
            repaired["send_status"] = "not_eligible"
            repaired["review_status"] = "needs_enrichment"
            repaired["tier"] = repaired.get("tier") or "C"
            repaired.setdefault("cleanup_reason", "")
        if not all(str(repaired.get(field) or "").strip() for field in ("lead_id", "brand_name", "website", "status")):
            summary.rows_skipped += 1
            continue
        repaired_rows.append(repaired)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in repaired_rows:
        grouped[_brand_key(row) or normalize_domain(row.get("website") or row.get("primary_source_url") or "")].append(row)

    final_rows: list[dict[str, Any]] = []
    for brand_key, group in grouped.items():
        active = [row for row in group if str(row.get("status") or "").strip().lower() != "rejected"]
        if not active:
            final_rows.extend(group)
            continue
        active.sort(key=_selection_score, reverse=True)
        selected = active[0]
        final_rows.append(selected)
        for duplicate in active[1:]:
            summary.duplicate_rows += 1
            final_rows.append(_mark_row_rejected(duplicate, f"duplicate_canonical_brand:{brand_key or 'unknown'}"))
        rejected_only = [row for row in group if str(row.get("status") or "").strip().lower() == "rejected"]
        final_rows.extend(rejected_only)

    for repaired in final_rows:
        summary.rows_changed += 1
        summary.repaired_rows.append(
            {
                "lead_id": repaired.get("lead_id", ""),
                "brand_name": repaired.get("brand_name", ""),
                "website": repaired.get("website", ""),
                "status": repaired.get("status", ""),
            }
        )
        if not dry_run:
            if hasattr(storage, "replace_lead_row"):
                storage.replace_lead_row(repaired, tab="Lead Queue")
            else:
                storage.upsert_lead(repaired, tab="Lead Queue")
    if not dry_run and summary.rows_changed:
        storage.commit()
    return summary
