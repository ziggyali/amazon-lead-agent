# Hermes Setup

Hermes Agent is the local operator and scheduler.
The Python repo is the lead engine.

Hermes should:

- Run the local wrapper script in this repo: `scripts/run_local.ps1` on Windows or `scripts/run_local.sh` on macOS/Linux.
- Read `logs/latest.log` and `campaign_report.md`.
- Schedule recurring runs locally.
- Never modify code unless explicitly asked.
- Never send emails.
- Never commit secrets.

Hermes should not be the main runtime for the campaign logic.
It should simply launch the local scripts and review the outputs.

## One-click Hermes prompt

Use this when starting a fresh run:

> Run the Amazon Lead Agent locally from this repo. First make sure `.venv` exists, then run `scripts/run_local.sh` on macOS/Linux or `scripts/run_local.ps1` on Windows. After the run, read `logs/latest.log` and `campaign_report.md`, then summarize discovered, enriched, scored, approved, rejected, draft, and contact-form-queue counts. Do not edit code, do not send emails, and do not expose secrets.

## Scheduled Hermes prompt

Use this for scheduled execution:

> At the scheduled time, launch the local Amazon Lead Agent from this repo using the appropriate local runner script. Load `.env` if present, run the full campaign, wait for completion, then report the latest log path, the run status, and the summary counts from `campaign_report.md`. If the run fails, report the error from `logs/latest.log`. Never send email and never modify code.

## What Hermes watches

- `logs/latest.log`
- `campaign_report.md`
- `data/leads.db`

## Safety rules

- Gmail drafts only.
- No automatic sending.
- No LinkedIn scraping.
- No anti-bot bypassing.
- No secret handling beyond loading existing local env vars.
