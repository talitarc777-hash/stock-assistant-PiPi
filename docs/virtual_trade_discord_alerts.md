# Discord alerts

The API runs an independent Discord alert scheduler. Alert delivery no longer
depends on a successful Virtual Trader cycle. The app sends Discord alerts for:

- an explainable overall score meeting the user's high threshold

- unusual real-market buying/selling pressure from intraday volume and price movement
- sudden real-market price rises or falls, even when volume is not exceptional

These alerts are for monitoring only. They do not place real trades and are not financial advice.

Simulated Virtual Trader buy and sell orders do not send Discord notifications.

## Real-market activity alert

The real-market alert uses intraday yfinance candles. Free market data does not show exact buyer-initiated or seller-initiated order flow, so the app detects directional pressure:

- estimated traded value is large
- volume is much higher than recent normal candles
- price moves meaningfully in the same short window

If price rises during the volume spike, the message says buying pressure. If price falls, it says selling pressure.

A separate sudden-price alert fires when the ticker moves by the configured percentage
within the same short window. Its default is 10% within 15 minutes and it does not
require the traded-value or volume-spike conditions.

Messages follow the user's profile language. Chinese (zh) alerts use Traditional
Chinese only; bilingual profiles receive English and Traditional Chinese.

## Environment variables

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_ALERT_SCHEDULER_ENABLED=true
DISCORD_ALERT_MESSAGE_LIMIT=1900
DISCORD_WEBHOOK_MAX_ATTEMPTS=3
DISCORD_WEBHOOK_RETRY_BASE_SECONDS=0.5

REAL_MARKET_DISCORD_ALERT_ENABLED=true
REAL_MARKET_ALERT_WINDOW_MINUTES=15
REAL_MARKET_LARGE_VALUE_THRESHOLD=10000000
REAL_MARKET_VOLUME_SPIKE_MULTIPLIER=3
REAL_MARKET_PRICE_MOVE_THRESHOLD_PCT=1.5
REAL_MARKET_SUDDEN_MOVE_THRESHOLD_PCT=10.0
REAL_MARKET_MIN_WINDOW_VOLUME=100000
REAL_MARKET_ALERT_TICKER_LIMIT=40
```

Duplicate alerts are suppressed only after Discord confirms delivery. Transient
network errors, HTTP 429 rate limits, and Discord 5xx responses use bounded
retries. Failed states remain eligible for a later scheduler cycle.

Multiple alerts are batched below Discord's message-size limit. Every delivery
batch is stored in `discord_alert_delivery_audit` with `pending`, `sent`, or
`failed` status, attempt count, HTTP status, and a secret-free error summary.
Neither the webhook URL nor message body is stored in the audit table.

## Health and alert-only testing

Read scheduler and delivery health without exposing the webhook:

```bash
curl -sS http://127.0.0.1:8000/discord-alerts/health
```

Send one harmless webhook test from the NanoPi:

```bash
.venv/bin/python scripts/run_discord_alerts.py --user-id demo-user --test-webhook
```

Run a real alert-only scan without executing the Virtual Trader:

```bash
.venv/bin/python scripts/run_discord_alerts.py --user-id demo-user
```
