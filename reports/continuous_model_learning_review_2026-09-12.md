# Controlled model learning review — 2026-09-12

## 1. Conclusion and evidence boundaries

I would build this as a small, governed research-and-deployment system, not an
algorithm that continuously changes its own rules. Improve information quality and
evaluation first; let a demonstrably useful model earn deployment afterwards.

**Implemented workflow safeguards and prospective collection. Did not establish
higher future predictive accuracy, change trained model parameters, relax historical
validation, force trades, or deploy anything.**

This review is dated September 12, 2026. The local database's newest decision is
May 7, 2026; that is a data cutoff, not the audit date. The public Pages frontend
is reachable and its deployed bundle points to the private NanoPi Tailscale API.
Private API access failed from this environment; the precise access restriction
was not established. Production values changing in your browser can therefore be
real without being observable here. No local statistic below is today's NanoPi rate.

## 2. Actual executable flow and limitations

`model_lifecycle_scheduler.run_cycle` → artifact reconciliation/feedback settlement
→ scheduled or triggered `model_lifecycle_service.run_training_workflow`
→ `model_training.train_baseline_models_for_ticker` / pooled training
→ chronological validation and lifecycle gates → immutable `model_versions`
→ initial activation or shadow collection → forward comparison
→ `active_model_deployments` → `resolve_runtime_model_candidates`
→ `live_virtual_trader.run_live_virtual_trader_now` → account/risk checks and ledger.

The return-model identity is market + issuer/GLOBAL + history window + target +
algorithm + immutable version. Five-day feedback changes evidence/ranking and
confidence/context adjustments; it does not periodically rewrite every model's
feature weights. The existing initial-incumbent path can activate on historical
validation without first proving superiority to a prior live model. Replacement
requires forward evidence. These are different events.

Limitations: weak technical-only signal, repeated candidate selection, small inner
calibration samples, overlapping five-session outcomes, uneven universe coverage,
and proxy trading economics. Historical adjusted prices and current provider
snapshots are not a complete vintage-controlled market dataset. There is no basis
for promising continuously rising accuracy or profits.

## 3. Why fallback remains high

Read-only rerun: local database has 31,386 US decisions and no HK decisions. Last
10,000: 8,497 fallback (**84.97%**), 835 production, 250 candidate, 418 requested.
All 8,497 fallback records include missing legacy `2y/target_5d_updown/
logistic_regression` artifact errors. All 10,000 actions are `no_action`.

The snapshot has 1,991 registry rows, zero version/deployment/feedback records,
and no current gate-9 validated registry evidence. Stored rejection reasons overlap:
1,903 balanced-accuracy failures; 1,805 majority-baseline failures; 1,626 unstable
folds. Disk has 1,998 model files; 1,993 summaries lack validation-scheme provenance.
The artifact fingerprint remained
`1f1f093f2d0659fcbe1cb50aa2a4b3d6e6243e7801d64dea0e7106a9a0465383`.

Current code also legitimately falls back when no eligible active exact/pooled
model exists or loading/inference fails. Bounded training does not cover every
runtime ticker. The current production percentages and causes require a fresh
NanoPi snapshot; an artifact count or changing validated count cannot establish them.

## 4. Duplication, contradictions and legacy

The preceding review corrected execution that could bypass active pointers through
family-registry candidates, lost lifecycle gate provenance, unsafe deployment joins,
and US-first/newest-first feedback-review starvation. Those changes are preserved.

This review additionally corrected:

- Claims of overlapping comparison without actually matching forward dates/prices.
- Single priority shadow selection, with repeated training displacing evidence collection.
- Static backtest reports being used as if they were new live deterioration evidence.
- Permanent missing histories repeatedly occupying the five-day settlement batch.
- SQLite context-manager use that committed but did not explicitly close connections.
- Unavailable feed default zeros being indistinguishable from observed zero counts in the new archive.

Canonical ownership: version/deployment service owns executable identity; feedback
service owns outcome observations; paired evidence owns like-for-like replacement
diagnostics; registry is compatibility/reporting, not a second execution authority.

Retained deliberately: historical classification/evaluation APIs and
`model_selection_service`, mutable-family compatibility bootstrap, benchmark-specific
shadow objective, and historical portfolio simulator. They are not equivalent to
active five-day return models. Delete only after production references are audited.
The existing context score is still a separate heuristic layer, not a learned
external-data alpha model.

## 5. Relevant public professional practices

- Start with hypotheses and clean data rather than more complexity. Man describes
  hypothesis-driven research and low signal-to-noise constraints in
  [The Good New Days](https://www.man.com/insights/the-good-new-days) and
  [Introduction to Machine Learning](https://www.man.com/insights/intro-machine-learning).
- Reconstruct what was actually available. [Feast's point-in-time joins](https://docs.feast.dev/getting-started/concepts/point-in-time-joins)
  distinguish event-time matching from filtering by creation/availability time to
  prevent late corrections leaking backwards. We use local SQLite, not a new feature-store platform.
- Preserve release vintages. [FRED real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
  distinguish today's revised series from historical knowledge. A vintage date alone
  does not establish intraday release availability.
- Use identifiable original disclosures. [SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
  expose submissions/XBRL; accession and dissemination/acceptance information matter,
  not just filing dates.
- Shadow models observe without acting, as illustrated by [AWS shadow testing](https://docs.aws.amazon.com/sagemaker/latest/dg/shadow-tests.html).
  Its documented latency/operational checks do not establish financial alpha; our
  application additionally needs matched future-outcome and portfolio evidence.
- Professional news products distinguish relevance, novelty and sentiment; see
  [LSEG's text-analytics overview](https://www.lseg.com/content/dam/data-analytics/en_us/documents/brochures/lseg-harvesting-insights-from-text-unstructured-text-and-analytics-brochure.pdf).
  Current headline averages are not equivalent to a licensed historical news panel.
- Historical LLM sentiment may leak later knowledge through pretraining; see
  [Glasserman and Lin](https://arxiv.org/abs/2309.17322). Do not assume an LLM labels
  old articles without knowing their future.

These support governance and data design, not a claim to reproduce a firm's strategy.

## 6. Recommended canonical architecture

```text
Price/event/context observations + first-known timestamps + data quality
                       ↓
Versioned as-of research panel and fixed outcome definitions
                       ↓
Simple baselines + separate alpha / risk / context experiments
                       ↓
Purged chronological development → untouched test → immutable challenger
                       ↓
Bounded paired shadow predictions → matured outcomes → comparison
                       ↓
One active deployment pointer → risk/sizing/execution → actual ledger
                       └── outcome/drift monitoring → bounded new research
```

Keep five-day return regression as a baseline while testing alternatives, not as
the mandatory centerpiece. A downside/volatility model can be useful without
predicting direction. An external-context overlay should prove incremental value
over price/risk baselines. An ensemble is justified only by independent OOS gains,
not by having many model files. Preserve separate US/HK contracts and cost models.

## 7. External data implemented and what remains to collect

New additive tables in the **resolved `PROFILE_DB_PATH`**:

- `external_context_observations`: normalized market/ticker, source-observation and
  actual availability timestamps, 24-hour expiry, schema version, numeric features,
  source/missingness lists and content hash. No raw article bodies or exception text.
- `context_collection_cursor`: persisted hourly market-specific sampling budget.

`CONTEXT_ARCHIVE_ENABLED` defaults on outside tests. Existing provider adapters are
reused; current Yahoo headlines are additionally summarized with the existing
lexicon. Fields cover news, public mentions/engagement, analyst context, SEC-derived
events and optional Alpha Vantage news/earnings tone. Disabled/unavailable sources
do not become fabricated historical data. Collection errors do not block the
lifecycle. The archive is idempotent; a later correction is not visible at an earlier
as-of timestamp. Expiry controls use, not deletion of historical observations.

Independent collection samples **at most two US and two marked HK tickers per hour**
when the lifecycle scheduler runs. US uses its active universe plus saved watchlists;
HK uses saved effective watchlists. Live context requests also archive snapshots.
This reduces BUY-conditioned sampling but does not guarantee complete daily
coverage: a 120-symbol universe takes at least 60 hourly batches at that budget.
Provider permissions, quotas, credentials and availability still apply. No paid
subscriptions were purchased, and no successful live feed collection is claimed.

`GET /model-lifecycle/context-coverage` exposes snapshot and per-feature counts.
`ContextObservationStore.as_of` is the prospective join boundary. **The trainers do
not consume the archive yet**; the endpoint explicitly says so. Start by measuring
coverage, missingness, latency and stability before training on it.

Next data work: SEC accession-based events and original financial vintages; HKEX
announcement release times with licensed/allowed retrieval; FRED/ALFRED macro
vintages with release-time delays; earnings calendars as known before announcements;
sector/market features based only on available prices. No new historical SEC,
HKEX-announcement or macro ingestion was implemented. Existing HKEX board-lot/name
metadata is not an earnings/regulatory-event dataset. Current analyst heuristics,
English lexicon tone, and mixed news/social aggregates need independent quality
validation before being treated as new predictive features.

## 8. Periodic learning, drift and replacement implemented

Matured outcomes are recorded whether or not a prediction led to a trade. Queue
retry timestamps prevent one missing history from monopolizing five-day settlement.
The new `last_evaluation_attempt_utc` column is an additive, idempotent migration.

One challenger and its own incumbent are inferred on the same input. A stable daily
rotation covers up to four distinct families/windows; the oldest unresolved member
of a family is retained. Other versions can remain unsampled. Repeated intraday
observations still deduplicate by version/ticker/date.

Promotion now adds explicit matched comparison to existing gates: same ticker/date,
horizon, benchmark, price and realized outcome; late predictions and mismatches are
excluded. Same-day securities are averaged, and overlapping outcome windows are
excluded. Require at least **20 non-overlapping date blocks**, positive 95% Student-t
lower bounds for direction and net-return improvements, and positive candidate
long/cash return. Both forecasts receive the same diagnostic cost, at least 0.10pp
or the larger recorded estimate. Negative forecasts mean cash, not fictional shorts.
Inferiority retirement also requires matched evidence. Rollback comparisons use
post-activation observations. This conservative policy may take months, not five days.

Live drift monitoring uses deployed immutable versions: require 60 distinct matured
market dates; compare recent 30 with prior 30–60 dates. Alarm only when accuracy
drops over 10pp **and** MAE exceeds `max(0.5pp, 1.5 × reference MAE)`. This is an
explainable monitoring heuristic, not experimentally proven optimal adaptation.
Feature drift remains a separate alarm once there is adequate live evidence.
Repair cooldown is 24 hours per market; consumed version/outcome-dated alarms do
not repeatedly request training, and failed repair runs are not marked consumed.

## 9. Before/after evidence

Correctness tests establish: nonmatched aggregate winners cannot promote; genuinely
better synthetic matched pairs can pass; identical models cannot; 100 tickers on
one date count as one block; late predictions and different entry prices are
excluded; hypothetical zero-sized shadows do not avoid comparison costs;
persisted pair evidence reaches the comparison service; same-input shadow execution
is disabled; promotion still resolves to the active version; unavailable history
does not starve the next row; PIT corrections and restart-safe collection work.

Re-ran the fixed prior calibration experiment on 21 archived OOS streams (16 US,
5 HK; 2y ridge). It uses 60 past errors, six-row delay, >=30 observed errors and a
1pp correction cap, with no new parameter/ticker search:

| Market / split | Direction before → after | Balanced before → after | Diagnostic net return/opportunity before → after |
|---|---:|---:|---:|
| US development | 48.69% → 48.50% | 49.72% → 49.61% | +0.0398% → −0.0032% |
| HK development | 50.46% → 51.11% | 51.63% → 52.19% | +0.3905% → +0.3102% |
| US previously opened test, 3,952 rows | 51.16% → 52.28% | 51.53% → 52.19% | +0.2612% → +0.2975% |
| HK previously opened test, 1,235 rows | 48.74% → 49.23% | 48.75% → 49.26% | −0.1038% → −0.0970% |

US test always-UP accuracy is 56.73%; always-in-asset diagnostic return is +0.4329%.
HK test always-UP is 48.50%, but corrected balanced accuracy remains below 50% and
diagnostic net return remains negative. Development economics deteriorated in both
markets. **No adoption is justified.** This rerun is not a new independent experiment.

**Locked-test result: no fresh locked test exists for the new architecture or new
external features.** The historical test block was already opened in earlier audits
and ends August 14, 2026. It is not September live accuracy. New prospective data,
a preregistered fixed policy and a genuinely untouched interval are still required.

## 10. BUY/HOLD/SELL quality and US/HK differences

The return/evidence diagnostics are not actual portfolio P&L. Real BUY/HOLD/SELL
decisions additionally depend on positions, cash, confidence, market regime, fees,
exposure, stops and reversal controls. A HOLD can create useful prediction feedback;
it does not imply an executed transaction. No new trades were generated and no
claim of improved BUY/SELL timing is supported by this snapshot.

US retains USD and whole shares; HK retains HKD, reliable board lots, its benchmark,
market hours and separate accounts. Matched evidence never crosses markets. HK has
smaller training/observation cohorts and less reliable external coverage; a numeric
stock code is not a reliable English news-search entity. Validate local-language
entity matching and sources separately. Existing market/prediction architectures
and sizing were not replaced.

## 11. Experimental or unresolved

- External-feature alpha, risk overlays, ensembles and bias recalibration remain
  research-only; no new predictor weights were deployed.
- Matched Student-t intervals are not a full sequential/multiple-testing correction;
  residual regime dependence and repeated looks still matter. Twenty blocks is a
  conservative starting policy, not a demonstrated optimum.
- The long/cash comparator omits actual lot sizing, dynamic holdings and a complete
  broker cost model. It is an additional guard, not proof of account profitability.
- Scheduled refreshes remain time-based; add data/label fingerprints to avoid
  identical-data holiday/provider-cache retraining. The repair path is now evidence-based.
- Active drift scan is bounded; broader production fleets need a durable rotating
  monitor cursor. Historical artifact reconciliation still has a newest-400 cap.
- The secondary benchmark and 1/5/10/20 diagnostic queues retain their existing
  settlement scheduling. They are not independent newly trained horizon models.
- Version age/current-gate revalidation, bounded retirement of never-observed
  families, full PIT price/event storage and dataset lineage require follow-up.
- Initial activation still relies on historical gates. It should be separately
  evaluated against baseline-only paper trading, not advertised as forward-proven.

## 12. Recommended next-months plan

First collect coverage and matched outcomes without changing risk thresholds.
Choose a small, fixed, representative US/HK cohort with dependable data. Freeze
targets, execution costs and baseline policies before looking at the next test.
Develop simple risk and external-context candidates on vintage-safe panels; compare
with cash, always-long, momentum and volatility baselines. Use market-date blocks,
calibration/error metrics, drawdown, turnover and exposure-matched portfolio replay.
Only then allow an immutable challenger to enter paired shadow evaluation.

An evidence clock should govern model changes, not elapsed calendar days or how
many artifacts accumulated. Missing information means wait or abstain. Regime alarms
request investigation/bounded training; they must never lower acceptance standards.

## 13. Files changed

New this review:

- `app/core/sqlite_store.py`
- `app/services/context_observation_store.py`
- `app/services/context_collection_service.py`
- `app/services/paired_model_evidence.py`
- `tests/test_continuous_model_learning.py`
- `reports/continuous_model_learning_review_2026-09-12.md`

Modified this review:

- `.env.example`
- `app/core/settings.py`
- `app/api/model_lifecycle.py`
- `app/services/external_market_context_service.py`
- `app/services/live_virtual_trader.py`
- `app/services/model_feedback_service.py`
- `app/services/model_lifecycle_scheduler.py`
- `app/services/model_lifecycle_service.py`
- `app/services/model_version_service.py`
- `tests/test_model_version_service.py`
- `tests/test_model_lifecycle_scheduler_shadow.py`
- `docs/model_feedback_and_promotion.md`
- `reports/model_system_review_2026-09-11.md` (corrected unsupported network-cause wording).

Preserved previous review work already present in this workspace: the two audit
scripts, `tests/test_model_system_audit.py`, lifecycle/HK/decision-edge/runtime-adoption
tests and associated service fixes. No production database, model pickle, frontend
source or portfolio record was intentionally modified. Generated tracked test
fixtures are restored after verification.

## 14. Verification actually run

- Full backend unittest discovery: **353 passed**, isolated `APP_ENV=test` and
  `PROFILE_DB_PATH=data/test_continuous_review.db`.
- Frontend `npm.cmd test`: **29 passed**. Initial sandbox run failed to spawn Node
  workers (`EPERM`); rerun with approved escalation passed.
- Frontend `npm.cmd run build`: **passed**, Vite 66 modules.
- `python -m compileall -q app scripts tests`: **passed**.
- `git diff --check`: **passed**, only normal Windows line-ending warnings.
- Read-only local database/artifact audit and fixed chronological calibration rerun.
- No configured dedicated lint/type-check scripts were found; no separate lint or
  static-type-check pass is claimed. No NanoPi integration/real-feed/real-trade test.

## 15. Deployment recommendation

**Do not deploy automatically.** Review these behavioral governance changes and
back up the actual persistent database/artifacts first. Stage on a production-data
copy, confirm resource/provider budgets and monitor exact active-version adoption.
The tables/column are additive and created by service initialization; no destructive
migration or historical backfill is required. Disabling `CONTEXT_ARCHIVE_ENABLED`
disables the new collection but does not delete collected evidence.

After a separately approved deployment, verify from NanoPi with read-only
`GET /model-lifecycle/context-coverage`, per-market model health, selection trace
and feedback endpoints. Confirm available-feature counts and paired evidence, not
just more validated models. No extra daemon, cloud feature store, or paid provider
is required for this first collection stage. Credentials remain private.
