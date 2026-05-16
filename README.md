# stock-assistant (trial1)

Beginner-friendly Python project for a **stock analysis assistant**.

This project is a **decision-support tool**, not an auto-trader.  
It is designed to analyze ETFs/stocks (such as `VOO`, `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`) and later provide simple **buy / hold / reduce-risk** style suggestions based on trend + momentum rules.

## Project Structure

```text
stock-assistant/
+-- app/
|   +-- api/
|   +-- core/
|   +-- services/
|   +-- models/
|   +-- backtest/
|   +-- main.py
+-- tests/
+-- scripts/
+-- .env.example
+-- requirements.txt
+-- frontend/
+   +-- src/
+   +-- package.json
+-- README.md
```

## Prerequisites (Windows + VS Code)

1. Install Python 3.10+ from: https://www.python.org/downloads/windows/
2. Install VS Code: https://code.visualstudio.com/
3. Install VS Code Python extension (`ms-python.python`).
4. Install Node.js 18+ from: https://nodejs.org/

## Setup Steps (VS Code Terminal)

Open this folder in VS Code, then run:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

When the server starts, open:

- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Run Backend + Frontend Together

Use two terminals in VS Code.

Terminal 1 (backend):

```powershell
.\.venv312\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

Terminal 2 (frontend):

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Open the dashboard at:

- http://127.0.0.1:5173

The dashboard now also includes a shared settings page at:

- http://127.0.0.1:5173/settings

The dashboard home page also shows a shared current-alerts panel that reads from:

- `GET /user-alerts/scan?user_id=...`

To run backend, dashboard, and Discord bot together, use three terminals:

Terminal 1:

```powershell
.\.venv312\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload
```

Terminal 2:

```powershell
cd frontend
npm run dev
```

Terminal 3:

```powershell
.\.venv312\Scripts\Activate.ps1
python bot/main.py
```

## Discord Bot

The project includes a simple Python Discord bot in `bot/`.

Bot environment settings in `.env`:

- `DISCORD_BOT_TOKEN`
- `BACKEND_BASE_URL`
- `COMMAND_PREFIX`
- `ALLOWED_CHANNEL_IDS`
- `WATCHLIST_TICKERS`
- `REPLY_LANGUAGE`
- `USER_SETTINGS_PATH`

Run the bot:

```powershell
python bot/main.py
```

Per-user settings:

- Shared settings are primarily stored in backend SQLite at `PROFILE_DB_PATH`
- The bot still keeps local JSON fallback storage at `USER_SETTINGS_PATH` for safe offline behavior
- Settings are saved per user ID
- If a user has no saved settings yet, the backend creates a default profile on first access
- Per-user language overrides the global `REPLY_LANGUAGE` default

Shared profile model:

- The backend is now the main source of truth for user profiles in SQLite at `PROFILE_DB_PATH`
- Shared fields include language, compact mode, default watchlist, alert settings, and alert watchlist
- Discord reads shared settings from the backend first, then falls back to local bot storage if the profile API is unavailable
- The dashboard reads and writes the same shared backend profile

Available Discord commands:

- `!help`
- `!settings`
- `!setlang en`
- `!setlang zh`
- `!setlang bilingual`
- `!setcompact on`
- `!setcompact off`
- `!setwatchlist VOO,QQQ,AAPL`
- `!resetsettings`
- `!analyze VOO`
- `!forecast NVDA`
- `!watchlist`
- `!alerts`

Natural-language examples:

- `set my language to Chinese`
- `change language to English`
- `use bilingual mode`
- `show my settings`
- `turn on compact mode`
- `disable compact mode`
- `add Tesla to my watchlist`
- `add AAPL and NVDA to my watchlist`
- `remove TSLA from my watchlist`
- `show my watchlist`
- `analyze VOO`
- `check Apple`
- `what do you think about NVDA`
- `forecast QQQ`
- `what is the outlook for Tesla`

Supported language-setting phrases:

- `set my language to Chinese`
- `change language to English`
- `reply in Chinese`
- `use bilingual mode`
- `speak in English and Chinese`

Watchlist add/remove examples:

- `add Tesla to my watchlist`
- `add TSLA`
- `add AAPL and NVDA to my watchlist`
- `remove Tesla from my watchlist`
- `remove TSLA`
- `delete AAPL from my watchlist`

Natural-language limitations:

- Parsing is rule-based, not AI-based
- Explicit `!commands` still work first and are the most reliable
- The bot only responds to clear supported phrases
- If a company name is ambiguous, the bot will ask you to use the ticker symbol

How settings affect replies:

- `language`
  controls which action summary and explanation bullets are used
- `compact_mode`
  shortens `!analyze`, `!forecast`, and `!watchlist` output
- `default_watchlist`
  is used automatically by `!watchlist`; if empty, the bot falls back to the system default watchlist

Resetting settings:

- Use `!resetsettings` to clear your saved preferences and return to defaults

Watchlist sync:

- Discord `!watchlist`, `!addticker`, `!removeticker`, and `!setwatchlist` now use the shared backend profile watchlist
- The dashboard settings page and watchlist manager update the same watchlist
- If a user has no saved watchlist, the backend falls back to `WATCHLIST_TICKERS`

Alert sync:

- Alert preferences are stored with the shared user profile
- Shared alert fields include `alert_enabled`, `alert_threshold_high`, `alert_threshold_low`, and `alert_watchlist`
- Discord `!alerts` now tries the shared backend alert scan first, then falls back to local alert logic if needed
- Duplicate alert spam is reduced by storing the last triggered state per user/ticker/rule in SQLite

Current local-user limitation:

- There is no full authentication layer yet
- Discord uses the real Discord user ID as `user_id`
- The dashboard uses a profile ID you can edit in the Settings page
- To make dashboard and Discord share the exact same profile, use the same profile ID in both places

## Daily Scan + OpenClaw Placeholder

Watchlist config file:

- `config/watchlist.json`

Run a local scan:

```powershell
python scripts/daily_scan.py
```

Run scan and invoke OpenClaw adapter placeholder (log-only):

```powershell
python scripts/daily_scan.py --send-openclaw
```

Current behavior:

- Generates ranked watchlist summary
- Prints alert lines in an OpenClaw-friendly message format
- Does **not** place trades or connect to any broker
- Uses a modular adapter in `app/services/openclaw_adapter.py` for future webhook/channel integration

## Paper Trading Simulation (Simulation Only)

This module is for **simulation only**:

- No real-money trading
- No broker execution
- No automated order placement
- Only hypothetical ?ould buy / would sell??events

API endpoint:

- `GET /paper-status?ticker=VOO`

Script:

```powershell
python scripts/paper_run.py --ticker VOO --period 5y --initial-cash 10000
```

## Automation CLI

CLI entry script:

- `scripts/cli.py`

Exact commands:

```powershell
python scripts/cli.py analyze-ticker --ticker VOO
python scripts/cli.py analyze-watchlist
python scripts/cli.py backtest --ticker VOO --period 10y
python scripts/cli.py export-report --ticker VOO
```

Notes:

- `analyze-watchlist` reads `config/watchlist.json`
- `export-report` saves JSON files to the `reports/` folder

Extra useful variants:

```powershell
python scripts/cli.py analyze-ticker --ticker MSFT --period 5y --benchmark VOO
python scripts/cli.py analyze-watchlist --config config/watchlist.json --period 5y
python scripts/cli.py backtest --ticker QQQ --period 10y --transaction-cost-pct 0.001
python scripts/cli.py export-report --ticker NVDA --period 5y --transaction-cost-pct 0.001
```

## VS Code Tasks And Launch

Added files:

- `.vscode/tasks.json`
- `.vscode/launch.json`

Included run targets:

- Run API (FastAPI/Uvicorn)
- Run daily watchlist scan
- Run backtest (VOO 10y)

## Market Data API

Endpoint:

- `GET /price-history?ticker=VOO&period=5y`
- `GET /indicators?ticker=VOO&period=5y`
- `GET /analyze?ticker=VOO&period=5y`
- `GET /compare-to-benchmark?ticker=QQQ&benchmark=VOO&period=5y`
- `GET /watchlist-analyze?tickers=VOO,SPY,QQQ,AAPL,MSFT,NVDA`
- `GET /backtest?ticker=VOO&period=10y`
- `GET /chart-data?ticker=VOO&period=5y`
- `GET /summary-dashboard?tickers=VOO,SPY,QQQ,AAPL,MSFT,NVDA`
- `GET /paper-status?ticker=VOO`
- `GET /forecast?ticker=VOO&period=2y`
- `GET /forecast-history?ticker=VOO`
- `GET /user-profile?user_id=...`
- `POST /user-profile/settings`
- `GET /user-watchlist?user_id=...`
- `POST /user-watchlist/add`
- `POST /user-watchlist/remove`
- `GET /user-alert-settings?user_id=...`
- `POST /user-alert-settings/update`
- `GET /user-alerts/scan?user_id=...`
- `GET /user-alerts/enabled-users`
- `POST /user-profile/reset`
- `GET /virtual-account/monthly-contribution-input?user_id=...`
- `POST /virtual-account/monthly-contribution-input`

Example (PowerShell):

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/price-history?ticker=VOO&period=5y"
Invoke-RestMethod "http://127.0.0.1:8000/indicators?ticker=VOO&period=5y"
Invoke-RestMethod "http://127.0.0.1:8000/analyze?ticker=VOO&period=5y"
Invoke-RestMethod "http://127.0.0.1:8000/compare-to-benchmark?ticker=QQQ&benchmark=VOO&period=5y"
Invoke-RestMethod "http://127.0.0.1:8000/watchlist-analyze?tickers=VOO,SPY,QQQ,AAPL,MSFT,NVDA"
Invoke-RestMethod "http://127.0.0.1:8000/backtest?ticker=VOO&period=10y"
Invoke-RestMethod "http://127.0.0.1:8000/backtest?ticker=VOO&period=10y&transaction_cost_pct=0.001"
Invoke-RestMethod "http://127.0.0.1:8000/chart-data?ticker=VOO&period=5y"
Invoke-RestMethod "http://127.0.0.1:8000/summary-dashboard?tickers=VOO,SPY,QQQ,AAPL,MSFT,NVDA"
Invoke-RestMethod "http://127.0.0.1:8000/paper-status?ticker=VOO"
Invoke-RestMethod "http://127.0.0.1:8000/forecast?ticker=VOO&period=2y"
Invoke-RestMethod "http://127.0.0.1:8000/forecast-history?ticker=VOO"
Invoke-RestMethod "http://127.0.0.1:8000/user-profile?user_id=demo-user"
```

Unified profile endpoints:

- `GET /user-profile?user_id=demo-user`
  returns the shared profile row and creates it on first access if needed
- `POST /user-profile/settings`
  updates shared language, compact mode, and default watchlist
- `GET /user-watchlist?user_id=demo-user`
  returns the user watchlist or the system default fallback
- `POST /user-watchlist/add`
  adds one ticker to the shared watchlist
- `POST /user-watchlist/remove`
  removes one ticker from the shared watchlist
- `GET /user-alert-settings?user_id=demo-user`
  returns shared alert preferences
- `POST /user-alert-settings/update`
  updates alert enabled state, thresholds, alert watchlist, and delivery source
- `GET /user-alerts/scan?user_id=demo-user`
  returns user-specific deduplicated alert events for Discord-friendly delivery
- `GET /user-alerts/enabled-users`
  returns all alert-enabled users for scheduler or batch delivery integration
- `POST /user-profile/reset`
  resets a shared user profile back to default language, compact mode, watchlist, and alert preferences

Response includes:

- `summary`: ticker, period, row count, date range, latest close
- `latest_10_rows`: last 10 OHLCV rows with columns:
  `date`, `open`, `high`, `low`, `close`, `adj_close`, `volume`
- `/indicators` includes:
  `latest_close`, `latest_snapshot` (latest indicator values), and `latest_30_rows`
- `/analyze` includes:
  `ticker`, `latest_close`, `score_breakdown`, `label`, `action_summary`, `explanation_bullets`
- `/compare-to-benchmark` includes:
  1m/3m/6m/12m returns, benchmark returns, excess returns, and `benchmark_strength_score`
- `/watchlist-analyze` returns:
  all requested tickers ranked by score (descending), plus any per-ticker failures
- `/backtest` includes:
  `metrics_summary`, `trade_log_preview`, and `equity_curve`
- `/chart-data` includes:
  daily OHLCV + SMA/RSI/MACD series, plus a score-over-time series (ISO dates, downsampled for payload size)
- `/summary-dashboard` includes compact per-ticker fields:
  latest close, daily % change, score, label, action summary, above SMA200, RSI, and MACD bullish flag
- `/paper-status` includes simulation-only cash/position/PnL state and hypothetical event history
- `/forecast` includes scenario-based 5d/20d outlook, trend regime, expected range,
  support/resistance, confidence score, bilingual summaries, and explanation bullets
- `/forecast-history` returns stored forecast snapshots (timestamp, outlook, expected range, confidence)
  for later forecast-vs-actual evaluation

## Service Usage (Python)

You can call the market data service directly:

```python
from app.services.market_data import get_price_history, get_price_history_for_tickers
from app.services.indicators import add_technical_indicators

df = get_price_history("AAPL", period="5y")
df_with_indicators = add_technical_indicators(df)
batch = get_price_history_for_tickers(["VOO", "SPY", "QQQ"], period="1y")
```

`get_price_history_for_tickers(...)` is a small helper for multi-ticker fetches and skips invalid/empty symbols gracefully.

Indicator columns added by `add_technical_indicators(...)`:

- `sma_20`, `sma_50`, `sma_200`
- `ema_12`, `ema_26`
- `rsi_14`
- `macd_line`, `macd_signal`, `macd_histogram`
- `avg_volume_20`
- `distance_from_52w_high_pct`
- `rolling_volatility_20_pct`
- `drawdown_from_peak_pct`

## Research Dataset Pipeline

You can build a model-ready daily feature dataset with:

```python
from app.services.research_pipeline import build_and_save_feature_dataset

artifact = build_and_save_feature_dataset(
    ticker="AAPL",
    period="5y",
    benchmark="VOO",
)

print(artifact.dataset_path)
print(artifact.metadata_path)
```

Saved folder structure:

- `data/research/<TICKER>/<PERIOD>/features.csv`
- `data/research/<TICKER>/<PERIOD>/metadata.json`

Main dataset column groups:

- Raw price data:
  `date`, `ticker`, `benchmark`, `open`, `high`, `low`, `close`, `adj_close`, `volume`
- Return features:
  `return_1d_pct`, `return_5d_pct`, `return_20d_pct`, `return_1m_pct`, `return_3m_pct`, `return_6m_pct`, `return_12m_pct`
- Technical indicators:
  `sma_20`, `sma_50`, `sma_200`, `ema_12`, `ema_26`, `rsi_14`, `macd_line`, `macd_signal`, `macd_histogram`,
  `avg_volume_20`, `rolling_volatility_20_pct`, `distance_from_52w_high_pct`, `drawdown_from_peak_pct`
- Benchmark-relative features versus `VOO`:
  `benchmark_return_*_pct`, `excess_return_*_pct`, `benchmark_strength_score`
- News sentiment features:
  `news_article_count`, `news_sentiment_score`, `news_sentiment_3d_avg`, `news_sentiment_7d_avg`
- Prediction targets:
  `target_5d_return`, `target_5d_updown`, `target_20d_regime`

Target notes:

- `target_5d_return` is the forward 5-trading-day return in percent
- `target_5d_updown` is `1` if the forward 5-day return is positive, else `0`
- `target_20d_regime` uses a simple rule:
  `bullish` if future 20-day return >= 2%, `bearish` if <= -2%, otherwise `neutral`

News-sentiment note:

- The built-in sentiment layer uses recent Yahoo Finance news headlines and summaries through `yfinance`
- News coverage can be sparse, so older rows may have zero sentiment values
- This is meant as a lightweight research feature, not a production-grade sentiment feed

## What Exists Right Now

- FastAPI backend scaffold
- `GET /health` endpoint
- `GET /price-history?ticker=VOO&period=5y` endpoint
- `GET /indicators?ticker=VOO&period=5y` endpoint
- `GET /analyze?ticker=VOO&period=5y` endpoint
- `GET /compare-to-benchmark?ticker=QQQ&benchmark=VOO&period=5y` endpoint
- `GET /watchlist-analyze?tickers=VOO,SPY,QQQ,AAPL,MSFT,NVDA` endpoint
- `GET /backtest?ticker=VOO&period=10y` endpoint
- `GET /chart-data?ticker=VOO&period=5y` endpoint
- `GET /summary-dashboard?tickers=VOO,SPY,QQQ,AAPL,MSFT,NVDA` endpoint
- `GET /paper-status?ticker=VOO` endpoint
- `GET /forecast?ticker=VOO&period=2y` endpoint
- `GET /forecast-history?ticker=VOO` endpoint
- Typed settings loaded from `.env`
- Market data service using `yfinance`
- Technical indicator service with validation
- Explainable scoring engine (trend, momentum, confirmation, risk penalties)
- Benchmark-relative strength analysis vs VOO (or custom benchmark)
- Beginner-friendly long-only backtest engine with optional transaction cost
- Chart-ready and dashboard summary endpoints for frontend integration
- Minimal React + Vite dashboard (`frontend/`) connected to FastAPI
- Paper-trading simulator module (simulation only, no broker integration)
- Scenario-based forecast module (not a guaranteed prediction)
- Local SQLite forecast snapshot persistence for future evaluation

## Next Suggested Steps

1. Add a market data service (using `yfinance`) in `app/services/`.
2. Add simple trend/momentum rules and suggestion labels in `app/models/` + `app/services/`.
3. Expose an analysis endpoint in `app/api/`.
4. Add basic backtests in `app/backtest/`.
5. Later integrate dashboard UI and OpenClaw alerts.

## Notes

- This project provides educational/decision support outputs.
- It does **not** execute trades automatically.
- Paper trading features are simulation-only and do **not** place real orders.
- Forecast features are scenario-based and do **not** guarantee future prices.

## Raspberry Pi + Cloudflare Deployment

The low-cost production target is:

- Raspberry Pi for the FastAPI backend, SQLite data, model artifacts, and Discord bot
- Cloudflare Tunnel for the public HTTPS API hostname
- Cloudflare Pages for the static Vite frontend
- GitHub for version control

See `deploy/pi/README.md` for the full setup, including `systemd` services, Cloudflare Tunnel, Cloudflare Pages environment variables, data migration, backups, and smoke tests.

## Troubleshooting

- `ModuleNotFoundError` (for example `pandas`):
  activate your virtual environment and run `python -m pip install -r requirements.txt`.
- API returns `422` for query parameters:
  check ticker format and period format.
  Examples: `ticker=VOO`, `period=5y`, `period=1mo`, `period=max`.
- Frontend cannot reach backend:
  for local development, make sure backend is running on `http://127.0.0.1:8000`
  and frontend `.env` contains `VITE_API_BASE_URL=http://127.0.0.1:8000`.
  For Cloudflare Pages, set `VITE_API_BASE_URL` to your Cloudflare Tunnel API
  hostname, for example `https://api.your-domain.com`, and add the Pages domain
  to backend `CORS_ALLOW_ORIGINS`.
- `No price data returned`:
  verify ticker symbol exists in Yahoo Finance and retry with another period.
- Running scripts from VS Code tasks:
  ensure `.venv312` exists and dependencies are installed in that environment.

## Low-Resource Deployment (Railway-Friendly)

These optimizations were added for small instances (for example ~1 vCPU / 0.5 GB RAM):

- Lean container build for Railway image limits:
  - added `Dockerfile` that copies backend runtime files only (`app/`, `config/`, `scripts/`, `requirements.txt`)
  - added `.dockerignore` to exclude heavy local folders from image builds (`data/`, `.venv/`, `.git/`, frontend artifacts, caches)
  - this prevents local model/cache/venv files from inflating the deploy image

- Frontend request resilience:
  - shared fetch client with request timeout
  - retry for transient GET failures (network/502/503/504/429)
  - friendlier error messages such as:
    - `Server is starting or temporarily busy. Please retry in a moment.`
    - `Unable to reach backend API. Server may be restarting or unavailable.`
- Virtual Trader page load strategy:
  - loads core summary panels first
  - defers heavy sections (account history and historical replay) to on-demand buttons
  - does not collapse the whole page when one panel fails
- Reduced request pressure:
  - scheduler status polling slowed to 60 seconds and skips refresh when tab is hidden
  - lower default chart/history limits on heavy endpoints
- Backend payload controls:
  - `GET /virtual-account/history` supports `limit` + `offset`
  - `GET /virtual-account/ledger` supports `limit` + `offset`
  - responses include `has_more` for incremental loading
- Lightweight caching:
  - short TTL in-process cache for summary/holdings/equity-curve reads
  - short TTL latest-price cache inside account summary rebuild path
- Endpoint timing logs:
  - key virtual-account and trader-status endpoints now log elapsed milliseconds and row counts

Notes:
- Scheduler recent-runs storage remains in-memory (rolling buffer), so it resets on backend restart.
- For best stability on tiny hosts, keep watchlist size moderate and avoid loading large historical sections unless needed.


## Chart Guide (Dashboard)

The dashboard charts are optimized for beginner readability:

- clear chart titles and ticker subtitles
- axis titles on every chart
- top legend with matching line colors
- hover tooltip with date + formatted values
- light gridlines for easier value reading
- built-in range selector (1M, 3M, 6M, 1Y, MAX)
- friendly fallback when data is missing: No data available

Axis meaning:

- Date: x-axis timeline
- Price (USD): price-level charts
- Return (%): model prediction vs actual outcome charts
- Volume: trading volume charts
- Score: scoring and oscillator charts
- Confidence (%): model confidence trend charts
- Portfolio Value (USD): virtual trader equity charts

How to read prediction vs actual:

- Prediction line = model output from walk-forward evaluation
- Actual line = realized future outcome for the same horizon
- Prediction Confidence chart = model confidence trend over time
- If prediction and actual frequently move together and rolling hit rate is stable, model behavior is more consistent
- Treat all outputs as decision-support signals, not guaranteed results

## Web Model Evaluation And Monthly Contributions

The web settings page now lets you configure two shared simulation inputs:

- `Model Evaluation（模型評估）`
  choose the active trained model for the web model-evaluation pages and virtual-trader pages
- `Monthly Contribution Input（每月注資設定）`
  set one recurring monthly amount in USD

How model selection works:

- Open the dashboard settings page
- In `Model Evaluation（模型評估）`, choose a saved model such as:
  - `Logistic Regression`
  - `Random Forest`
  - `Gradient Boosting`
- The selected model is stored in backend SQLite and reused after reloads
- The current selected model is shown in both the Model Evaluation page and the Virtual Trader page

How the recurring monthly contribution input works:

- You only need to set one active monthly amount
- That amount is applied automatically on the first day of each month
- The same amount keeps recurring until you change it
- If you update the amount later, historical applied months are not rewritten
- A value of `0` means no new monthly auto-cash is added

How the virtual trader uses this input:

- On each scheduler cycle, the system checks the current month
- If the month has not been applied yet, one immutable `monthly_contribution` ledger event is created
- The same month is never applied twice
- Account Ledger remains the source of truth for monthly contribution history

New backend endpoints:

- `GET /model-evaluation/settings?user_id=...`
- `POST /model-evaluation/settings`
- `GET /virtual-account/monthly-contribution-input?user_id=...`
- `POST /virtual-account/monthly-contribution-input`

Device Profile ID default:

- The dashboard stores the last used `Profile ID` in browser localStorage
- Reopening the app on the same device auto-fills that previous profile ID
- Switching profile IDs updates this local device default

## Live Virtual Trader Mode

The project now supports two different trader views:

- Historical replay mode:
  uses saved walk-forward evaluation history for research comparison
- Live virtual trader mode:
  uses latest available market data and the selected saved model to decide
  simulated `buy`, `sell`, `hold`, or `no_action` now

Monthly contribution behavior in live mode:

- Monthly auto-cash starts from `2026-04`
- The active recurring monthly amount is used automatically every month
- If current month has not been applied yet, the new saved amount can apply immediately
- Duplicate runs in the same month do not double-count contributions
- If the amount changes after a month is already applied, the new value applies from the next cycle

Live virtual trader endpoints:

- `GET /virtual-trader/live-status?user_id=...`
- `POST /virtual-trader/run-now`
- `GET /virtual-trader/live-trades?user_id=...`

Live account consistency note:

- Total equity is always calculated as `cash + holdings_value`
- The live equity curve is rebuilt from immutable ledger events, then the latest point is appended from the current account snapshot
- The latest live equity-curve point should match the latest account summary for the same profile
- Historical replay charts are shown separately and should not be compared directly with the live account summary

Virtual Trader page workflow (top-to-bottom):

- Live Trader Status
- Current Holdings
- Monthly Contribution Input
- Trading Account summary/actions
- Recent decisions/trades, ledger, and historical charts

Monthly Contribution History chart behavior:

- The chart starts from the first month with actual contribution records
- Empty leading months are trimmed for readability

News sentiment pipeline and debugging:

- News is fetched from Yahoo provider metadata, scored (FinBERT with lexicon fallback),
  then aggregated to daily and recent-7-day features
- New debug endpoints:
  - `GET /news-sentiment/latest?ticker=VOO`
  - `GET /news-sentiment/debug?ticker=VOO&date=2026-04-01`
- These endpoints help separate:
  - no recent matched news
  - fetched but unmatched-by-date window
  - pipeline/fetch failures

## Immutable Virtual Account Ledger

The live simulator now uses an append-only account ledger as source-of-truth.

- Historical cash/trade events are immutable (no in-place edits)
- Account state is rebuilt from ledger history
- Corrections should be compensating events (new deposit/withdrawal/trade event)

Ledger event types:

- `monthly_contribution`
- `manual_deposit`
- `withdrawal`
- `buy_trade`
- `sell_trade`
- `fee` (reserved)

Monthly contribution behavior:

- Start month is `2026-04`
- Set or update recurring monthly amount via `POST /virtual-account/monthly-contribution-input`
- Auto-applied amounts are recorded as immutable `monthly_contribution` ledger events
- Additional cash changes remain separate ledger events (`manual_deposit` / `withdrawal`)

Virtual account APIs:

- `GET /virtual-account/summary?user_id=...`
- `GET /virtual-account/equity-curve?user_id=...`
- `GET /virtual-account/holdings?user_id=...`
- `GET /virtual-account/history?user_id=...`
- `GET /virtual-account/recent-trades?user_id=...`
- `GET /virtual-account/ledger?user_id=...`
- `POST /virtual-account/deposit`
- `POST /virtual-account/withdraw`

Live trader + account APIs:

- `POST /virtual-trader/run-now`
- `GET /virtual-trader/status?user_id=...`
- `GET /virtual-trader/decisions?user_id=...`
- `GET /virtual-trader/trades?user_id=...`
- `GET /market-data/live-snapshot?ticker=VOO`

Trading account model:

- `summary`
  is the canonical snapshot for one profile
- `holdings`
  shows only current open positions derived from immutable buy/sell ledger rows
- `recent-trades`
  shows executed buy/sell activity only
- `history`
  shows the full immutable account timeline, including monthly contributions, deposits, withdrawals, buys, and sells

Cash flow rules:

- `monthly_contribution` increases cash once when that month is auto-applied
- `manual_deposit` increases cash immediately
- `withdrawal` reduces cash immediately
- `buy_trade` reduces cash and increases holdings exposure
- `sell_trade` increases cash and reduces holdings exposure

How to interpret current holdings vs history:

- Current Holdings = what is still open right now
- Full Account History = every past account-impacting event, including closed trades and cash movements
- The latest equity-curve point should match the latest account summary because both are rebuilt from the same ledger path

Data freshness note:

- Market/news are near-live snapshots from latest available provider data
- This project does not claim exchange-grade true real-time streaming

Continuous local runner:

```powershell
.venv\Scripts\python scripts\live_trader_runner.py --once --user-id demo-user
.venv\Scripts\python scripts\live_trader_runner.py --interval-seconds 300
```

## Trader Scheduler (Auto-Run)

The backend now starts a background trader scheduler automatically at app startup.

Cadence rules:
- Market open (U.S. ET 9:30-16:00, weekdays): every 5 minutes
- Market closed (including weekends): every 1 hour

Key endpoints:
- GET /virtual-trader/scheduler-status
- GET /trader-status (alias)
- POST /virtual-trader/scheduler-run-now

Recent Runs panel behavior:
- Recent Runs now shows all scheduler/manual runs from the last 24 hours (time-based), newest first.
- It is no longer a fixed-count "last N runs" list.

The scheduler and manual run-now share the same lock to prevent overlapping execution.

Scheduler robustness notes:
- Data fetch retries: each user run attempts up to 2 times before marking an error.
- Per-run error logging: scheduler status includes error_count and error_messages for recent runs.
- Health endpoint: GET /virtual-trader/scheduler-health
- Clean restart: scheduler starts on backend startup and stops on backend shutdown via FastAPI lifespan.

Autonomous trader behavior update:
- Live trader no longer hard-fails when per-ticker model artifacts are missing.
- Decision priority is now:
  1) ticker model (if available)
  2) shared GLOBAL model (if available)
  3) built-in rule-based fallback strategy
- Fallback strategy keeps trading simulation running immediately, even with zero trained models.
- The virtual trader scans an active market universe automatically (via universe_service.get_active_universe).
- Missing-model fallback does not count as a scheduler error.
- Scheduler run status now reports:
  - users_processed
  - tickers_processed
  - tickers_failed
  - fallback_used
- Virtual Trader UI no longer exposes manual ticker selection for live mode.

