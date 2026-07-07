# Model feedback and promotion

The Virtual Trader now keeps an auditable outcome loop for its model predictions.

## Feedback flow

1. One prediction is stored per ticker, model, model version, period, and market date.
2. The stored snapshot includes the model prediction, action, confidence, price,
   context score, news/social/analyst/regulatory factors, valuation, and risk data.
3. After five later trading sessions are available, the lifecycle scheduler records:
   - actual ticker return
   - benchmark return
   - direction correctness
   - estimated strategy return after the HKD 50 entry and exit fees
   - benchmark excess return
4. The observation receives a bounded outcome score from direction accuracy,
   profitability after cost, net return, and benchmark-relative return.

Repeated five-minute scheduler runs do not create repeated feedback for the same
model and trading date.

## Promotion rules

Walk-forward validation remains the main promotion evidence.

- Before the minimum live sample count, feedback cannot change promotion score.
- After the minimum sample count, live feedback receives at most 35% weight.
- Weak live feedback can trigger retraining.
- Runtime model candidates are ranked using the same blended score.
- A candidate must still meet the minimum production score before promotion.

This is a champion/challenger-style safeguard. A small number of lucky outcomes
cannot immediately replace the validated model.

## Broad contextual reasoning

The saved context includes price/technical state, news and public sentiment,
analyst and earnings tone, regulatory context, valuation, company size,
volatility, and benchmark strength.

For context factors seen at least three times, the system measures their later
five-day returns. Matching factors may adjust future context scores, but the
combined adjustment is capped. This lets context improve gradually without
allowing noisy text or one unusual event to control a trade.

## Configuration

```env
MODEL_FEEDBACK_ENABLED=true
MODEL_FEEDBACK_HORIZON_DAYS=5
MODEL_FEEDBACK_MIN_SAMPLES=8
MODEL_FEEDBACK_PROMOTION_WEIGHT=0.35
CONTEXT_FEEDBACK_MAX_ADJUSTMENT=8
```

## API

- `GET /model-lifecycle/feedback`
- `POST /model-lifecycle/feedback/evaluate`

The system is for virtual trading and educational monitoring. Better historical
scores do not guarantee future performance.
