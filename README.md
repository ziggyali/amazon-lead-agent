# Amazon Lead Agent

Local-first lead generation engine for Zaigham Ali's Amazon account management services.

It finds DTC brands in:

- Beauty & Personal Care
- Pet Supplies
- Home & Kitchen
- Health / Supplements

It then enriches leads, stores canonical records in SQLite, mirrors results to Google Sheets, and creates Gmail drafts only for qualified leads.

## Operating model

- The Python repo is the lead engine.
- Hermes Agent on the local PC is the operator and scheduler.
- Hermes should run the local scripts and read the logs and reports.
- Hermes should not modify code unless explicitly asked.
- Hermes should never send emails.
- GitHub Actions is optional and not the main runtime.

## Quick start

1. Copy `config.example.yaml` to `config.yaml`.
2. Copy `.env.example` to `.env`.
3. Run the local installer.
4. Run the local campaign script.

Windows:

```powershell
scripts\install_local.ps1
scripts\run_local.ps1
```

macOS/Linux:

```bash
bash scripts/install_local.sh
bash scripts/run_local.sh
```

## LLM setup

- Primary: `MiniMax-M3`
- Fallback: `MiniMax-M2.7`
- Direct MiniMax API is supported.
- ScrapeGraphAI is optional and used when it works locally.

See `LOCAL_SETUP.md` for exact environment variables and verification commands.

## Safety rules

- Drafts only, no automatic sending.
- No LinkedIn scraping.
- No anti-bot bypassing.
- No CAPTCHA bypassing.
- No login-wall bypassing.
- No secret commits.

## Reports

Each run writes:

- `logs/YYYY-MM-DD-HHMMSS-run.log`
- `logs/latest.log`
- `campaign_report.md`

## Debugging

Use the CLI only for troubleshooting:

```bash
python run_campaign.py --config config.yaml --mode discover
python run_campaign.py --config config.yaml --mode enrich
python run_campaign.py --config config.yaml --mode score
python run_campaign.py --config config.yaml --mode draft --dry-run
python -m compileall amazon_lead_agent
```

## Setup docs

- `LOCAL_SETUP.md`
- `HERMES_SETUP.md`
- `scripts/create_google_sheet.py`

