# SPLG provider correction and mixed training-run results

The State Street issuer notice changed SPLG to SPYM on 31 October 2025:
https://www.ssga.com/library-content/products/fund-docs/etfs/us/information-schedules/ap-notice/notice-to-aps-ticker-fund-name-and-benchmark-index-name-changes-10-31-25.pdf

A development-host Yahoo probe on 8 October 2026 returned no two-year history for
SPLG and 501 rows for SPYM through 7 October. The corrected application price and
feature pipeline produced 496 usable five-session target rows, with 41 features.
This verifies usable data, not model accuracy or NanoPi connectivity.

`resolve_security('SPLG', 'US')` now preserves stored ticker `SPLG` and resolves
the provider symbol to `SPYM`. History caches use SPYM, so stale SPLG cache files
are not selected by this request. Existing holdings, watchlists, model identities,
ledger records and feedback are not migrated or reset. SPYM input also works.
The shared resolver already serves history, live metadata and external context.

Lifecycle workflows now report `skipped_with_errors` when no fits complete,
some jobs lack sufficient new evidence and some jobs error. Counters and error
details remain unchanged. Entirely failed, all-skipped and partially successful
runs retain their distinct statuses. No validation or adoption gate changed.

The Recent Model Updates table recognizes both the new status and older `failed`
records with mixed counts. It displays "Skipped; 1 ticker error", the skipped-job
count, and the first actual error. Additional errors are expandable. Provider
URLs, artifact paths and credential assignments are removed from display text.
Underlying historical run records are not rewritten.

Publish the changes to GitHub for the user's two-minute NanoPi pull workflow and
the existing frontend deployment. After confirming NanoPi has the new revision,
restart `stock-assistant-api` if the pull workflow does not already restart it.
No packages or database migration are required; do not force a fit or promotion.

Read-only verification from the NanoPi checkout:

```bash
.venv/bin/python -c "from app.services.market_config import resolve_security; print(resolve_security('SPLG','US'))"
curl -fsS 'http://127.0.0.1:8000/model-lifecycle/runs?limit=3'
```

Expect provider_symbol='SPYM'. At the next scheduled training cycle SPLG should
have usable provider history if Yahoo works on NanoPi; other evidence-based skips
remain normal. Refresh the frontend after its build deploys. Its historical mixed
US rows should display the corrected label without retraining.

Local checks: 28 relevant backend tests, 34 frontend utility/wiring tests and
frontend production build passed. Live feature generation and `git diff --check`
passed. No browser end-to-end test or production deployment was performed here.
