# Continuous-learning operational review — September 28, 2026

Implemented locally, not deployed. No prediction-accuracy improvement claimed.
The existing estimator, targets, validation gates and portfolio rules were retained.

## 1. Is learning automatic?

Yes, the collection → settlement → evidence admission → paired research fits →
forward observation → next-round workflow starts through the API's existing
lifecycle scheduler. Production defaults now enable research; explicit old disable
flags are respected. No user needs to manually create each experiment. This does
not mean new context models automatically become trading models.

## 2. What starts after deployment?

The FastAPI lifespan starts the scheduler immediately; subsequent cycles occur
15 minutes after the preceding cycle finishes. General context sampling rotates
hourly, and the four US/four HK research tickers are prioritized after 17:00 in
their own market timezones. Only current completed daily candles become research
snapshots. Canonical five-session settlement supplies real labels. Training starts
automatically when existing coverage/history gates are met.

Jobs, retry state, cursors, experiments and observations survive service restarts.
A deployment changing the frozen data/model contract creates a new isolated
generation and retires incompatible in-flight rounds without deleting evidence.
UI/status-only edits are excluded from the research orchestration's semantic hash;
ordinary new rounds do not restart the contract. Relevant provider/trainer semantic
changes can require fresh evidence; there is no reinterpretation of old results.

## 3. Sources actually usable

A small live probe from this development host returned:

| Source | AAPL | 0700.HK |
|---|---|---|
| Yahoo entity-matched news | Usable | Usable |
| Yahoo analyst context | Usable | Usable |
| Reddit public search | No usable data | No usable data |
| Alpha Vantage news/transcript | Not configured; not probed | Unsupported by this adapter |
| SEC filings | Contact not configured; not probed | Unsupported by this adapter |

This proves a current response here, not NanoPi availability, all-ticker coverage,
sentiment validity or predictive usefulness. The probe saved no context or model
data. Canonical market/benchmark price features remain in the price pipeline;
this pass added no dedicated macro feed, HK regulatory feed or HK transcript feed.

Entity safeguards normalize bare HK codes to `.HK`, check returned symbols when
available, prefer explicit associated news tickers and verified company names,
and reject bare numeric/short-symbol matches in social text. They are conservative
filters, not a perfect multilingual entity-recognition system. Legal company
suffixes are removed from verified names without maintaining a ticker-name map.

## 4. Configuration and credentials

Required operational settings: context archive, external context, prospective
research and model feedback enabled; feedback horizon five sessions. New defaults
enable these outside tests, but an existing explicit `false` must be changed once.
Yahoo needs no configured API key. Reddit's public endpoint may be blocked.
Alpha Vantage needs a key and applicable entitlement; a persistent 20-request daily
ceiling prevents uncontrolled usage. Transcript attempts are limited to the latest
completed quarter, not six probes per ticker per hour.

SEC requires an identifying contact User-Agent; the example placeholder is rejected.
These requirements follow the [Alpha Vantage API documentation](https://www.alphavantage.co/documentation/#earnings-call-transcript)
and [SEC access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).
No credentials, contacts, raw exception URLs or backend file paths appear in the
new health response. Missing credentials, unsupported markets, empty feeds and
unavailability are explicit rather than reported as successful data collection.

## 5. How fast coverage grows

Hourly batch = `min(configured ceiling, max(2, ceil(universe size / 24)))`, ceiling
eight by default. For 120 tickers this means five attempts/hour, approximately a
24-hour rotation under healthy conditions. A 20-ticker HK marked list rotates in
roughly ten hours. Actual universe counts and capacity estimates appear on the site.
Outages, rate limits and long scheduler work can slow these estimates.

Research aims for four snapshots per market per completed trading day: at most
one new training date/day, not four independent market dates. General coverage
is not a complete daily all-universe research panel. Failed feeds remain missing.

## 6. Retraining conditions

Keep 120 matured training dates, at least 90 dates per research ticker, qualifying
external-feature coverage/variation and the unchanged canonical validation process.
Research arms are fixed for 120 future prediction dates, at least 90 paired dates
per ticker, with outcomes settled before final evaluation. The length supports
roughly 20 nonoverlapping five-session periods, not a promise of statistical power.

After a completed/retired round, a replacement requires changed inputs and at least
five newly matured dates. Existing production repair also reacts to sufficient
forward deterioration evidence; its gates were not weakened. From an empty panel,
initial training takes roughly six months, followed by roughly six months of final
testing; provider gaps extend this.

## 7. Duplicate and wasted work prevention

Existing labeled-data fingerprints remain canonical and cover inputs, labels,
schema/configuration, training code and dependencies. Unchanged successful fits
are skipped. Completion/registration recovery reuses artifacts. Changed old data
alone does not replace the new-matured-date requirement.

Added durable per-symbol collection leases, one-hour success spacing, failure
backoff from 15 minutes up to six hours, shared symbol admission for general/research
collection, and SQLite-backed reuse of recent provider context after restart.
Research price acquisition retries at most hourly. Provider data without a saved
observation is not reported as collection success. Cache reuse preserves original
availability time. Registry discovery now runs once/day, with startup discovery
recorded so the immediate scheduled pass does not repeat it; normal training
already registers new versions directly. Existing manual discovery remains possible.

## 8. Challenger progression and intermediate evidence

Automatic paired fit → immutable research shadows → matched future records →
descriptive checkpoints → completed-window evidence → review eligibility.
The website displays both arms' prediction dates and matured rows, plus direction
accuracy and net opportunity-return comparisons at 20-date checkpoints.

Those checkpoints are explicitly provisional: they never select hyperparameters,
trigger early retraining or qualify promotion. Because interim scores are visible,
this is now a **monitored fixed-model prospective test, not a blind holdout**.
Repeated looks are not independent success tests. The final 120-date requirement
and paired-evidence gates remain. No context model can automatically activate,
become a rollback target or change production calibration. Returns remain research
opportunity diagnostics, not executable account P&L.

## 9. Deterioration and failures

Existing production drift/forward-performance monitoring, probation and rollback
remain unchanged. Research losses do not alter active trading models or cause
automatic gate relaxation. Failed research fits retry after six hours and retire
after seven days; unfinished forward rounds retire after 365 calendar days.
Artifacts and evaluated results remain. Pending rows for retired arms stop consuming
settlement work; replacement requires new evidence. Data/provider outages trigger
backoff and later retry, not fabricated observations or overwritten snapshots.

The system still needs a functioning host, writable persistent storage, working
clock/network and provider access. It cannot repair missing credentials, provider
entitlements, disk failure or a stopped systemd service by itself. Slow upstream
calls can delay the shared scheduler; stale heartbeats make that visible.

## 10. What to monitor on the website

Trading Models → **Continuous learning operations**:

- Overall status, heartbeat and explicit blockers; API failure is not shown as zero success.
- US/HK archive totals, fresh snapshots, cohort snapshots and matured training dates.
- Latest source availability, configuration and missing/unsupported reasons.
- Coverage/variation admission per feature and dates per ticker.
- Current dataset fingerprint, changed-information indicator and next-fit eligibility.
- Experiment generation, preserved older generations, both arms' progress,
  provisional comparisons and completed/retired results.
- Scrollable collection/retry details and training-admission reasons.

The panel refreshes every minute while visible without starting jobs. Lifetime
counts are distinct from quality summaries limited to the newest 10,000 snapshots.
Use a browser with access to the backend/Tailscale network. A public Pages frontend
does not make the private NanoPi API accessible from every environment.

Direct NanoPi health access from this host returned `Bad access`; its private
database and deployed counters were not audited. The public frontend was reachable
earlier today via HTTP, but the web-reading tool could not render its dynamic page.

## 11. Changed files and verification

This operational continuation changed:

- `.env.example`, `app/core/settings.py`, `app/main.py`
- `app/api/model_lifecycle.py`
- `app/services/context_collection_service.py`
- `app/services/context_observation_store.py`
- `app/services/external_market_context_service.py`
- `app/services/model_lifecycle_scheduler.py`
- `app/services/prospective_model_research.py`
- `app/services/learning_operations.py` (new)
- `frontend/src/api.js`, `frontend/src/pages/ModelLifecyclePage.jsx`, `frontend/src/styles.css`
- `frontend/src/components/LearningHealthPanel.jsx` (new)
- `frontend/src/utils/learningHealth.js`, `frontend/src/utils/learningHealth.test.js` (new)
- `scripts/check_learning_operations.py` (new)
- `tests/test_learning_operations.py` (new)
- `docs/model_feedback_and_promotion.md`, `deploy/pi/README.md`, this report.

Other uncommitted files belong to earlier work and were preserved.
Verification: **374 backend tests passed** (final run 35.212 seconds), **31 frontend
tests passed**, frontend production build passed, Python compilation and whitespace
checks passed. Frontend tests are Node utility/wiring tests, not an iPhone browser
automation test. No standalone configured lint/type-check runner was found.

## 12. Deployment safety and one-time check

Ready for a controlled deployment with a database/model backup and post-deployment
smoke check; **not certified against the private NanoPi**, and not deployed here.
Use one API worker and persistent database/model directories. New tables are
additive: `learning_collection_jobs`, `learning_provider_budget`,
`learning_heartbeat`, `research_capture_attempts`, `research_monitoring_checkpoints`.
There is no destructive migration, relaxed validation or forced trading.

Find the actual environment/project path rather than editing `~/.env`:

```bash
sudo systemctl show stock-assistant-api -p WorkingDirectory -p EnvironmentFiles --no-pager
```

After deploying backend/frontend together and confirming flags in that environment:

```bash
curl -sS http://127.0.0.1:8000/model-lifecycle/learning-health
# Run from the service WorkingDirectory:
.venv/bin/python scripts/check_learning_operations.py
.venv/bin/python scripts/check_learning_operations.py --probe
```

No manual experiment-start command is needed. Watch counts increase on subsequent
market days and review explicit blockers if they do not. Collecting evidence and
fitting challengers is automatic; actual accuracy improvement remains unproven.
