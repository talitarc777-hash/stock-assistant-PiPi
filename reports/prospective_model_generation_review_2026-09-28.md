# Prospective Model Trader generation — 2026-09-28

Implemented locally; not deployed, enabled or proven more accurate. Existing
uncommitted work from the previous audit was preserved.

## 1. Architecture chosen

Two matched, pooled-by-market ridge regressors: **PRICE ONLY** versus **PRICE +
CONTEXT**, using the existing stationary features, fold-local preprocessing,
purged chronological validation, immutable artifacts and five-session settlement.
The new service orchestrates a frozen experiment; it does not implement a second
feature calculator, estimator or feedback engine.

Ridge is a deliberately simple, regularized starting point, not an empirically
proven best model. With no local prospective context history, an ensemble, separate
context/alpha/risk estimators or regime-specific estimators would add parameters
without evidence. Keep risk controls separate from prediction. A relative-return
target is worth a separately preregistered future study, not a mid-test target
change. No architecture superiority is claimed from this implementation.

Availability-aware joins follow the documented distinction between event time
and information actually available at prediction time; late corrections must not
leak backward. [Feast point-in-time documentation](https://docs.feast.dev/getting-started/concepts/point-in-time-joins).

## 2. How external information enters

After the current market day's completed close, freeze canonical technical inputs
and the existing archive's latest nonexpired as-of context. Availability is recorded
after feature acquisition, not backdated to the candle. No historical backfill.
Attach realized five-session labels only after canonical feedback settles.

Eligible candidate inputs, in fixed priority order: news tone, analyst consensus,
public mention count. Admit at most two with at least 80% overall coverage, 50%
per-ticker coverage and three distinct values. This is coverage-based admission,
not outcome-based feature mining. Both arms use identical dates, tickers and labels.
The experiment can test their combined incremental value, not attribute a causal
effect to each individual source.

## 3. What is usable today

Code supports the existing Yahoo/Reddit-derived aggregates, when providers return
usable values. **The local read-only audit found zero archived observations in
both US and HK**, no research rounds and no training-admission records. Therefore
no external feature is currently demonstrated usable for experimental fitting in
this database. Local absence does not establish NanoPi production absence.

Coverage reporting now includes field missingness, ticker coverage, source
availability, freshness and depth separately by market. Source availability is
not verified vendor uptime, factual correctness or sentiment quality.

## 4. What needs collection

At least 120 matured observation dates and 90 dates per cohort ticker before a
fit; missing context can delay it indefinitely. Earnings, regulatory, extra social
and Alpha Vantage fields may be archived but are not automatically fed into this
generation. No reliable new macro series or comprehensive sector feed was added.
Existing benchmark-relative technical features remain in both arms. HK language
coverage and the headline lexicon need evaluation; do not assume reliable Chinese
sentiment. SEC coverage is not an HK regulatory feed.

## 5. Automatic learning flow

Once separately deployed and explicitly enabled:

`collect → freeze inputs → settle real outcomes → coverage gate → paired fits →
locked research shadows → matched evaluation → next fixed-protocol round`

The canonical lifecycle scheduler owns this work. It recovers across restarts,
isolates per-ticker collection failures and leaves production running on research
errors. Parameters, relevant source code and library versions are hashed on first
registration. A mismatch stops the study rather than rewriting its hypothesis.

After both models exist, freeze them for **120 future prediction dates**, with at
least 90 matched dates per ticker. Release results only when all recorded outcomes
settle; incomplete pairs or coverage produce an inconclusive report. Next-round
training uses the newest 120 matured dates and the same protocol. No mid-test
refit or manual redesign based on interim scores.

## 6. Identical-data retraining prevention

Automatic lifecycle, pooled and lazy-activation training share a durable hash of
the actual labeled inputs, schema, model configuration, training code and library
versions. Identical data is skipped; changed data normally requires five newly
matured dates beyond the last successful dataset. Old-history revisions alone
are insufficient. A six-hour lease prevents duplicate concurrent work; failures
back off for an hour. Completed fits missing lifecycle registration are replayed
from their immutable artifacts, not fitted again. Missing artifacts can be repaired.

The first guarded run establishes this fingerprint; old artifacts without input
lineage cannot establish that an incoming dataset is identical. Explicit manual
research training remains ungated. Calendar schedules now provide opportunities
to check evidence, not unconditional permission to refit. Five new dates are an
admission heuristic, not proof of improved accuracy.

## 7. US versus HK and frozen contract

| Contract | US | HK |
|---|---|---|
| Cohort | AAPL, MSFT, JPM, XOM | 0005, 0700, 1810, 9988 |
| Benchmark | VOO | 2800 |
| Collection | After 17:00 New York | After 17:00 Hong Kong |
| Diagnostic round-trip cost | 0.25 percentage points | 0.75 percentage points |

Separate market fits; no user-watchlist mutation or US/HK portfolio mixing.
Both use ridge alpha 10 and raw-close five-session percentage-return labels,
matching canonical live feedback. This is not dividend-adjusted total return;
the existing historical production target remains unchanged.

Baselines: cash, always-long, benchmark and positive-20-day-momentum. Research
long/cash threshold: predicted return greater than 1%. Metrics: direction and
balanced accuracy, MAE, mean net return per opportunity, nonoverlapping paths and
the existing paired confidence intervals. Brier score is N/A, because the models
do not supply calibrated class probabilities.

## 8. Duplication and legacy isolation

One as-of store, canonical trainer, version identity and feedback settlement are
reused. The same training guard now covers scheduled and background activation
paths. Experimental feedback is excluded from production calibration and generic
feedback listings; experimental artifacts cannot overwrite the family registry,
be selected as ordinary challengers, activate or become rollback targets.

Compatibility registry/artifact paths and legacy/manual training functions were
not deleted in this step. Existing production active/shadow/probation/rollback
behavior remains separate. No validation standard or fallback rule was relaxed.

## 9. Price-only versus context results

**Not available.** No actual prospective training panel or locked-test results
exist in the inspected local database. Synthetic tests exercise real ridge fits,
matching, costs, 20 independent-block bookkeeping and activation barriers; their
numbers are not market performance. No historical before/after experiment was
substituted for missing future evidence.

## 10. Forward evidence and visibility

On September 28, the public `/model-lifecycle` page returned **HTTP 200** via curl.
The web-reading tool could not render that route. HTTP availability verifies the
frontend shell, not its browser's authenticated/private backend connection or live
model counts. No private NanoPi database was inspected this step. Audit date is
September 28, not the date of old local trade records.

`GET /model-lifecycle/research-status` exposes readiness, registered/current
protocol hashes and released results. Intermediate experiment results are hidden
there and from generic feedback listings; database administrators can still read
raw tables, so this is not an access-control boundary.

## 11. Files changed in this continuation

- `.env.example`
- `app/core/settings.py`
- `app/api/model_lifecycle.py`
- `app/services/context_observation_store.py`
- `app/services/live_virtual_trader.py`
- `app/services/model_feedback_service.py`
- `app/services/model_lifecycle_scheduler.py`
- `app/services/model_lifecycle_service.py`
- `app/services/model_training.py`
- `app/services/model_version_service.py`
- `app/services/prospective_model_research.py` (new)
- `app/services/training_evidence.py` (new)
- `scripts/audit_prospective_research.py` (new, read-only)
- `tests/test_prospective_model_research.py` (new)
- `docs/model_feedback_and_promotion.md`
- This report.

Additional dirty files shown by git belong to the preserved earlier review.
Additive SQLite tables in configured `PROFILE_DB_PATH` (default
`data/user_profiles.db`): `model_training_evidence`, `research_protocols`,
`research_feature_snapshots`, `research_model_rounds`. Existing context, model
versions and feedback tables are reused. No destructive migration.

## 12. Verification

- Backend: **363 tests passed**, final run 33.759 seconds, with `APP_ENV=test`,
  isolated `PROFILE_DB_PATH`, and unittest discovery.
- Focused research tests cover actual paired ridge fitting, PIT availability,
  identical/unlabeled-data skipping, recovery, source isolation, frozen protocol,
  after-close capture, five-session settlement, locked release and activation denial.
- Frontend: **29 tests passed**. Initial sandbox run hit Node `spawn EPERM`;
  permitted rerun outside that restriction passed.
- Frontend production build: **passed**, 66 modules transformed.
- Python compilation and `git diff --check`: passed during verification.
- No configured frontend type-check/lint scripts or standalone Python type/lint
  configuration were found; no claim that those checks passed.
- Read-only local coverage audit: completed; zero observations/rounds.

## 13. What happens over coming months

Nothing new runs in production until a separately approved deployment and opt-in.
`PROSPECTIVE_RESEARCH_ENABLED=false` remains the default. Once enabled with archive,
external context, five-session feedback and lifecycle scheduling enabled, collect
daily cohort snapshots, settle outcomes, wait for coverage, fit the matched pair,
then observe the locked period. From an empty archive, roughly six months of
training collection plus six months of testing are needed; missing observations
and settlement delays extend this. Do not promise improvement after five days.

Inspection commands after an approved deployment are in the workflow document.
The local audit can be run now without mutations:

```text
python scripts/audit_prospective_research.py --db data/user_profiles.db
```

## 14. Production readiness and direct answer

**Structurally yes for autonomous collection, evidence-gated fitting, future
evaluation and repeated fixed-protocol research. Not yet for autonomous adoption
of these new external-context models into trading, and not a guarantee of better
accuracy or freedom from overfitting.**

Every new experimental version stays `research_shadow`. A favorable result still
requires a separate adoption review and runtime context-input integration through
the existing matched-evidence/probation/rollback workflow, not a direct promotion.
The research label starts at the close even though inputs are captured after it:
diagnostic returns are **not executable account P&L**. Before adoption validate
next-tradable-price execution, corporate actions, actual costs, lots and portfolio
risk. No new external-context model is ready for production, and nothing was deployed.
