# Discord alerts

The app sends Discord alerts for two real-market situations:

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

REAL_MARKET_DISCORD_ALERT_ENABLED=true
REAL_MARKET_ALERT_WINDOW_MINUTES=15
REAL_MARKET_LARGE_VALUE_THRESHOLD=10000000
REAL_MARKET_VOLUME_SPIKE_MULTIPLIER=3
REAL_MARKET_PRICE_MOVE_THRESHOLD_PCT=1.5
REAL_MARKET_SUDDEN_MOVE_THRESHOLD_PCT=10.0
REAL_MARKET_MIN_WINDOW_VOLUME=100000
REAL_MARKET_ALERT_TICKER_LIMIT=40
```

Duplicate alerts are suppressed through the existing alert history table, so repeated runs should not spam the same ticker/action state.
