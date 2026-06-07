# Setup Instructions

1. Create a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Optional: install ScrapeGraphAI with `pip install scrapegraphai` if you want the live scraper path.
4. Copy `.env.example` to `.env` and fill in your Minimax and Google credentials.
5. Copy `config.example.yaml` to `config.yaml` and update the SQLite path and Google Sheet ID.
6. Run `python run_campaign.py --config config.yaml --mode full`.

For development:

1. Run `python -m unittest discover -s tests -v`.
2. Run a syntax pass with `python -m py_compile` or `python -m compileall`.
