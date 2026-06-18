# Virtual Trader external context feeds

The Virtual Trader now uses an explainable decision stack:

`model prediction + confidence + valuation + trend + volatility + news/headlines + external context + risk guardrails`

External context is best-effort. If a feed is unavailable, the trader continues running and records the missing source in the trade metadata.

## Feeds used

| Feed | Purpose | Default |
| --- | --- | --- |
| Reddit search | Direct public-opinion / social discussion signal | Enabled |
| yfinance analyst data | Analyst consensus and upgrades/downgrades where available | Enabled |
| SEC EDGAR submissions | Official regulatory filing event risk | Enabled |
| Alpha Vantage News Sentiment | Extra provider sentiment signal | Requires `ALPHA_VANTAGE_API_KEY` |
| Alpha Vantage Earnings Call Transcript | Earnings-call transcript tone | Requires `ALPHA_VANTAGE_API_KEY` |

## Environment settings

```env
EXTERNAL_CONTEXT_ENABLED=true
EXTERNAL_CONTEXT_TIMEOUT_SECONDS=2.5

REDDIT_CONTEXT_ENABLED=true
REDDIT_CONTEXT_SUBREDDITS=stocks,investing
REDDIT_CONTEXT_LIMIT=8

SEC_CONTEXT_ENABLED=true
SEC_USER_AGENT="StockAssistantPiPi/1.0 your-email@example.com"

ALPHA_VANTAGE_API_KEY=
```

## How it affects actions

- A buy still needs a bullish model/fallback signal.
- The context score must be at least `55/100` before buying.
- If the ticker is already held and context score falls to `35/100` or below, the trader may reduce part of the holding.
- External context can add or reduce the score based on public-opinion tone, analyst revision tone, official filing risk, and earnings-call tone.

This is still virtual trading for educational use. The app should treat external feeds as helpful context, not as financial advice or guaranteed prediction.
