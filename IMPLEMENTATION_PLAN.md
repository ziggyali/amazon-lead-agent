# Implementation Plan

## Objective

Build a fully automated, free-first lead generation agent for Zaigham Ali's Amazon account management services.

The system discovers DTC brands in beauty, pet, home, and supplements whose public websites mention Amazon availability or link to Amazon storefronts. It enriches those leads using ScrapeGraphAI with Minimax, stores canonical data in SQLite, mirrors lead records to Google Sheets, and creates Gmail drafts for qualified leads.

## Non-negotiable constraints

- Use free infrastructure where possible.
- Only LLM usage can be paid.
- Use Minimax API as the ScrapeGraphAI LLM.
- Use SQLite as source of truth.
- Mirror records to Google Sheets.
- Create Gmail drafts only in v1; do not send emails.
- Do not bypass CAPTCHAs, login walls, LinkedIn protections, Amazon anti-bot controls, or robots restrictions.
- Do not claim guessed emails are verified.
- Do not use fake free-credit rotation or terms-violating enrichment tactics.

## Campaign configuration

- Target: DTC brands whose websites mention Amazon availability or link to Amazon storefronts.
- Categories: beauty, pet, home, supplements.
- Daily discovery limit: 50.
- Daily draft limit: 10.
- Minimum score for Gmail draft: 75.
- Reserved auto-send threshold: 90.
- Sender: Zaigham Ali.
- Brand: personal brand.
- Website: https://zaighamali.com.
- LinkedIn: https://linkedin.com/zaighamali-.
- Offer: We take the messy, time-consuming operational work off your plate and keep your Amazon account clean, compliant, and conversion-ready.

## Architecture

```text
run_campaign.py
  -> discovery_agent.py
  -> extraction_agent.py
  -> scoring_agent.py
  -> outreach_agent.py
  -> sqlite_store.py
  -> google_sheets.py
  -> gmail_drafts.py
```

## Core pipeline

1. Generate discovery queries for each category.
2. Search the public web for DTC brand pages containing Amazon buying signals.
3. Normalize candidate company/domain records.
4. Dedupe by domain, Amazon URL, and normalized company name.
5. Extract/enrich public website data via ScrapeGraphAI + Minimax.
6. Identify Amazon evidence, contact path, likely category, and decision maker clues.
7. Score each lead.
8. Store lead in SQLite.
9. Mirror lead to Google Sheets.
10. For qualifying leads, create Gmail drafts and log the draft event.

## Required modules

### `tools/sqlite_store.py`

Implement:

- `init_db(path: str) -> None`
- `upsert_lead(conn, lead: dict) -> str`
- `get_leads_for_enrichment(conn, limit: int) -> list[dict]`
- `get_leads_for_scoring(conn, limit: int) -> list[dict]`
- `get_leads_for_drafting(conn, min_score: int, limit: int) -> list[dict]`
- `mark_draft_created(conn, lead_id: str, draft_id: str) -> None`
- `record_outreach_event(conn, event: dict) -> None`

Tables:

- `leads`
- `source_urls`
- `outreach_events`
- `daily_reports`

### `tools/search.py`

Free-first web discovery.

Implement:

- `generate_queries(categories: list[str]) -> list[str]`
- `search_web(query: str, limit: int) -> list[dict]`
- `discover_candidates(categories: list[str], limit: int) -> list[dict]`

Use public search result pages carefully. Prefer DuckDuckGo HTML or other accessible free search methods. Add rate limits and user-agent. If blocked, log and continue.

### `tools/amazon_backlink_discovery.py`

Implement:

- `extract_amazon_links(html: str, base_url: str) -> list[str]`
- `contains_amazon_buying_signal(text: str) -> bool`
- `summarize_amazon_evidence(links: list[str], text: str) -> str`

Signals:

- `amazon.com`
- `/stores/`
- `available on amazon`
- `shop our amazon store`
- `buy on amazon`
- `amazon storefront`
- `amazon store`

### `tools/scrapegraph_runner.py`

Implement a wrapper around ScrapeGraphAI `SmartScraperGraph`.

Function:

- `extract_brand_profile(url: str, minimax_api_key: str) -> dict`

Use `prompts/extract_brand.md`.

Expected output JSON fields:

- `company_name`
- `brand_name`
- `website`
- `category`
- `country`
- `description`
- `amazon_links`
- `amazon_evidence_summary`
- `amazon_backlink_found`
- `founder_or_executive_names`
- `ecommerce_or_marketplace_people`
- `public_emails`
- `contact_page_url`
- `decision_maker_source_url`
- `pain_points`
- `confidence`
- `source_quotes`

### `tools/google_sheets.py`

Implement Google Sheets mirror using service account credentials.

Auth env vars:

- `GOOGLE_SERVICE_ACCOUNT_FILE`, or
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Functions:

- `append_or_update_lead(sheet_id: str, tab: str, lead: dict) -> None`
- `append_outreach_log(sheet_id: str, event: dict) -> None`
- `append_daily_report(sheet_id: str, report: dict) -> None`

### `tools/gmail_drafts.py`

Create Gmail drafts only.

Auth options can be OAuth desktop credentials or service account with domain-wide delegation where available.

Function:

- `create_gmail_draft(to: str, subject: str, body: str) -> str`

Do not send email in v1.

### `agents/discovery_agent.py`

Uses `tools/search.py`, `tools/amazon_backlink_discovery.py`, and `tools/sqlite_store.py`.

### `agents/extraction_agent.py`

Uses ScrapeGraphAI to enrich candidates.

### `agents/scoring_agent.py`

Uses deterministic scoring plus optional LLM scoring prompt from `prompts/score_lead.md`.

### `agents/outreach_agent.py`

Uses `prompts/write_outreach.md` to create draft subject/body. In v1 this can use deterministic templating if no outreach LLM is configured.

## Scoring model

- Amazon backlink or Amazon evidence URL: +30
- Strong buying signal text such as "available on Amazon": +15
- Company website found: +10
- Relevant category: +10
- Public business email or contact path: +15
- Decision maker clue found: +10
- Clear operational pain point: +15
- Multiple source URLs: +5
- No Amazon evidence: -35
- No contact path: -25
- Weak/unclear brand: -20

Tiers:

- A: 85-100
- B: 75-84
- C: 55-74
- Reject: below 55

Draft eligibility:

- score >= 75
- tier A or B
- contact path exists
- Amazon evidence exists
- no prior draft
- daily draft cap not exceeded

## CLI

`run_campaign.py` should support:

```bash
python run_campaign.py --config config.yaml --mode full
python run_campaign.py --config config.yaml --mode discover
python run_campaign.py --config config.yaml --mode enrich
python run_campaign.py --config config.yaml --mode score
python run_campaign.py --config config.yaml --mode draft
```

## Environment variables

- `MINIMAX_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GMAIL_SENDER_EMAIL`
- `GMAIL_CREDENTIALS_FILE` if OAuth is used
- `GMAIL_TOKEN_FILE` if OAuth is used

## Testing

Add smoke tests where possible:

- database init
- scoring function
- Amazon link extraction
- dedupe normalization
- prompt file loading

## Acceptance criteria

- Running `python run_campaign.py --config config.yaml --mode discover` stores candidate leads in SQLite.
- Running `--mode enrich` enriches candidates with ScrapeGraphAI or marks extraction errors cleanly.
- Running `--mode score` assigns score/tier/confidence.
- Running `--mode draft` creates Gmail drafts only for eligible leads.
- Running `--mode full` executes the whole pipeline with caps.
- No module sends emails automatically.
- Google Sheets mirror works when `storage.google_sheet_id` is configured.
