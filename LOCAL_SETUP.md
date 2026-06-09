# Local Setup

## Fresh clone

1. Clone the repo.
2. Copy `config.example.yaml` to `config.yaml`.
3. Copy `.env.example` to `.env`.
4. Run the local installer script for your platform.

## Python and venv

Windows:

```powershell
scripts\install_local.ps1
```

macOS/Linux:

```bash
bash scripts/install_local.sh
```

## `.env` setup

Fill these values in `.env`:

- `STORAGE_MODE=sheets`
- `LOCAL_CACHE_ENABLED=false`
- `MINIMAX_API_KEY`
- `MINIMAX_API_STYLE=chatcompletion_v2`
- `MINIMAX_MODEL=MiniMax-M3`
- `MINIMAX_FALLBACK_MODEL=MiniMax-M2.7`
- `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_OAUTH_CREDENTIALS_FILE`
- `GOOGLE_OAUTH_TOKEN_FILE`
- `GOOGLE_SHEETS_AUTH_MODE=oauth`
- `GOOGLE_SHEET_ID`
- `GMAIL_SENDER_EMAIL`
- `GMAIL_CREDENTIALS_FILE`
- `GMAIL_TOKEN_FILE`
- `LLM_PROVIDER=minimax`
- `LLM_FALLBACK_PROVIDERS=minimax,gemini`
- `GEMINI_API_KEY` if you want Gemini as an optional provider
- `OPENAI_API_KEY` only if you later add a real OpenAI API integration

Use the fallback model only if the primary MiniMax-M3 request fails.

## `config.yaml` setup

Update:

- `storage.storage_mode`
- `storage.local_cache_enabled`
- `storage.sqlite_path`
- `storage.google_sheet_id`
- `campaign.daily_discovery_limit`
- `campaign.daily_draft_limit`
- `campaign.minimum_score_for_draft`

## MiniMax-M3 setup

Default direct API settings:

- `MINIMAX_API_STYLE=chatcompletion_v2`
- `MINIMAX_MODEL=MiniMax-M3`
- `MINIMAX_API_BASE=https://api.minimax.io/v1/text/chatcompletion_v2`

The direct client uses the Bearer token from `MINIMAX_API_KEY`.

## Gemini setup

Gemini is optional and can be used with a free-tier Gemini API key from Google AI Studio.

Set:

- `GEMINI_API_KEY`
- `GEMINI_MODEL=gemini-2.5-flash`
- `GEMINI_TIMEOUT_SECONDS=90`
- `GEMINI_MAX_OUTPUT_TOKENS=4096`

To make Gemini primary:

```bash
LLM_PROVIDER=gemini
```

To keep Gemini available as a fallback:

```bash
LLM_FALLBACK_PROVIDERS=minimax,gemini
```

Keys stay local in `.env`.

## MiniMax-M2.7 fallback setup

Fallback settings:

- `MINIMAX_FALLBACK_API_STYLE=anthropic_messages`
- `MINIMAX_FALLBACK_MODEL=MiniMax-M2.7`
- `MINIMAX_FALLBACK_API_BASE=https://api.minimax.io/anthropic/v1/messages`

## Verify MiniMax extraction

Run a quick direct check:

```bash
python -c "from amazon_lead_agent.llm.minimax_client import MiniMaxClient; print(MiniMaxClient().generate_text('Reply with the single word OK.', purpose='research'))"
```

If the output is not plain text, confirm `MINIMAX_API_KEY` and the API style values.

## Verify Gemini extraction

Run a quick direct check:

```bash
python scripts/test_llm_provider.py --provider gemini
```

If Gemini is skipped, confirm `GEMINI_API_KEY` is set and `google-genai` is installed.

## Verify ScrapeGraphAI vs fallback

The extractor records `extraction_method` in SQLite.

Methods you may see:

- `scrapegraphai_other`
- `minimax_direct_m3`
- `minimax_direct_m27`
- `gemini_direct`
- `openai_direct`
- `playwright_contact_scrape`
- `urllib_fallback`
- `heuristic_fallback`
- `blocked_or_error`

If ScrapeGraphAI is unavailable or fails, the direct MiniMax or fallback path is used and the note is written to the lead record.

## Google Sheet creation

Use the bootstrap script:

```bash
python scripts/create_google_sheet.py --title "Amazon Lead Agent CRM"
```

Then copy the printed spreadsheet ID into `config.yaml` or set `GOOGLE_SHEET_ID`.

If Hermes created the sheet with Workspace OAuth, set:

- `GOOGLE_SHEETS_AUTH_MODE=oauth`
- `GOOGLE_OAUTH_CREDENTIALS_FILE`
- `GOOGLE_OAUTH_TOKEN_FILE`

If you still use a service account, leave the auth mode on `auto` or set it to `service_account`.

## Gmail OAuth setup

For draft creation, configure:

- `GMAIL_CREDENTIALS_FILE`
- `GMAIL_TOKEN_FILE`

Then run the campaign once to complete OAuth token creation if needed.

## Normal operation

Windows:

```powershell
scripts\run_local.ps1
```

macOS/Linux:

```bash
bash scripts/run_local.sh
```

## Windows Task Scheduler example

Action:

```text
Program: powershell.exe
Arguments: -ExecutionPolicy Bypass -File C:\path\to\repo\scripts\run_local.ps1
```

## macOS/Linux cron example

```cron
0 9 * * * cd /path/to/repo && bash scripts/run_local.sh
```

## Hermes recommended routine

Hermes should be the preferred scheduler:

1. Launch the local runner script: `scripts/run_local.ps1` on Windows or `scripts/run_local.sh` on macOS/Linux.
2. Wait for completion.
3. Read `logs/latest.log`.
4. Read `campaign_report.md`.
5. Summarize any blocked pages or fallback usage.

## Debug commands

Use these for debugging only:

```bash
python run_campaign.py --config config.yaml --mode discover
python run_campaign.py --config config.yaml --mode enrich
python run_campaign.py --config config.yaml --mode score
python run_campaign.py --config config.yaml --mode draft --dry-run
python -m compileall amazon_lead_agent
```
