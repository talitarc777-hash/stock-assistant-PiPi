# Discord alerts

The app can send Discord alerts for two different situations:

- large Virtual Trader simulated buys/sells
- unusual real-market buying/selling pressure from intraday volume and price movement

These alerts are for monitoring only. They do not place real trades and are not financial advice.

## Virtual Trader alert

By default, an alert fires when either condition is true:

- one simulated buy/sell trade is at least `HKD 50,000`
- the same ticker has at least `2` simulated buys or sells within `30` minutes, and the combined value is at least `HKD 50,000`

Only real virtual `buy` and `sell` rows are checked. `hold` and `no_action` rows do not alert.

## Real-market activity alert

The real-market alert uses intraday yfinance candles. Free market data does not show exact buyer-initiated or seller-initiated order flow, so the app detects directional pressure:

- estimated traded value is large
- volume is much higher than recent normal candles
- price moves meaningfully in the same short window

If price rises during the volume spike, the message says buying pressure. If price falls, it says selling pressure.

## Environment variables

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

VIRTUAL_TRADE_DISCORD_ALERT_ENABLED=true
VIRTUAL_TRADE_ALERT_WINDOW_MINUTES=30
VIRTUAL_TRADE_LARGE_VALUE_HKD_THRESHOLD=50000
VIRTUAL_TRADE_ALERT_MIN_TRADE_COUNT=2

REAL_MARKET_DISCORD_ALERT_ENABLED=true
REAL_MARKET_ALERT_WINDOW_MINUTES=15
REAL_MARKET_LARGE_VALUE_THRESHOLD=10000000
REAL_MARKET_VOLUME_SPIKE_MULTIPLIER=3
REAL_MARKET_PRICE_MOVE_THRESHOLD_PCT=1.5
REAL_MARKET_MIN_WINDOW_VOLUME=100000
REAL_MARKET_ALERT_TICKER_LIMIT=40
```

Duplicate alerts are suppressed through the existing alert history table, so repeated runs should not spam the same ticker/action state.
