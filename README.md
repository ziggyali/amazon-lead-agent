# Amazon Lead Agent

Automated lead discovery, enrichment, scoring, and Gmail draft creation for DTC brands that mention Amazon availability or link to Amazon storefronts.

## Campaign v1

- Target: DTC brands whose public websites mention Amazon availability or link to Amazon storefronts
- Categories: beauty, pet, home, supplements
- Daily discovery limit: 50
- Gmail mode: drafts only
- Daily draft limit: 10
- Minimum score for draft: 75
- Reserved auto-send threshold: 90
- Extraction harness: ScrapeGraphAI
- Extraction LLM: Minimax
- Sender: Zaigham Ali

## What the agent does

1. Generates safe web-search queries for DTC brands with Amazon evidence.
2. Discovers candidate company URLs from public search results.
3. Uses ScrapeGraphAI to extract structured data from public brand websites.
4. Scores leads based on Amazon evidence, contactability, ICP fit, and pain-point strength.
5. Writes qualified leads to Google Sheets.
6. Creates Gmail drafts for A-tier leads only.

## What the agent does not do

- It does not bypass CAPTCHAs, login walls, robots restrictions, Amazon anti-bot systems, or LinkedIn protections.
- It does not scrape logged-in LinkedIn pages.
- It does not send emails automatically in v1.
- It does not claim guessed emails are verified.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
cp .env.example .env
cp config.example.yaml config.yaml
```

Fill `.env` with your Minimax key and Google credentials configuration.

## Run

```bash
python run_campaign.py --config config.yaml --mode full
```

Other modes:

```bash
python run_campaign.py --config config.yaml --mode discover
python run_campaign.py --config config.yaml --mode enrich
python run_campaign.py --config config.yaml --mode score
python run_campaign.py --config config.yaml --mode draft
```

## Google Sheets

Create a Google Sheet and add its ID to `config.yaml`. The expected tabs are:

- Campaign Config
- Lead Queue
- Approved Leads
- Outreach Log
- Rejected Leads
- Daily Reports

The app can create missing headers when it first writes rows.

## GitHub Actions scheduling

A starter workflow is included at `.github/workflows/daily_campaign.yml`. Add required secrets before enabling scheduled runs:

- `MINIMAX_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GMAIL_SENDER_EMAIL`

For Gmail drafts, the Google account must have appropriate Gmail API access and OAuth/service delegation configured. If that is not configured yet, run locally first.

## Safety gates

Drafts are created only when:

- lead score is at least `minimum_score_for_draft`
- lead tier is A or B
- there is at least one public source URL
- there is a visible business email or contact path
- the lead has not already been drafted
- daily draft cap has not been reached

## Next implementation tasks

See `IMPLEMENTATION_PLAN.md`.
