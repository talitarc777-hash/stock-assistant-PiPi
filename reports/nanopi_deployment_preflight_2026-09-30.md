# NanoPi controlled-deployment preflight — September 30, 2026

## Outcome

**Deployment blocked before any production change. Production collection is not verified.**
The NanoPi is online in Tailscale, but this execution environment cannot open its
SSH or HTTPS services. No production database backup could be made or verified.
No commit, push, deployment, restart, production configuration change, promotion,
trade, or database/model modification was performed during this check.

## Observed evidence

- Local branch: `main`; local HEAD and remote `main` both resolve to
  `4d6f624d9a7668ec86585c559cf503535ff6ef8f`.
- The reviewed continuous-learning implementation is still in modified and
  untracked local files. The working tree is **not clean**; existing edits were
  preserved. A deploy of the current remote commit would omit these changes.
- `.github/workflows/deploy-nanopi.yml` has only `workflow_dispatch` enabled.
  Its comment says the NanoPi already pulls updates on a timer. The actual timer
  could not be inspected. A push to `main` could therefore trigger deployment
  outside this session, so nothing was pushed before verifying backup control.
- The workflow migrates a legacy SQLite database only when the destination is
  absent; that is not a backup of an existing production database. It also
  restarts the Discord bot unconditionally and checks API health immediately.
  These are deployment risks to resolve against the actual service setup before
  using that manual workflow; it was not dispatched.
- Tailscale reports the local connection running, the expected tailnet, and
  `NanoPi-R76S` online. A single ping received a response through DERP Hong Kong
  in 351 ms. A direct connection was not established. This establishes device
  connectivity through the relay, **not application or SSH availability**.
- Batch-mode SSH with strict host-key checking to
  `pi@nanopi-r76s.tail8919df.ts.net` failed at connection establishment:
  `port 22: Permission denied`. Authentication was not reached.
- HTTPS to the NanoPi `/model-lifecycle/learning-health` failed with curl error 7,
  `Bad access`. These errors persisted outside the filesystem sandbox. They do
  not prove that the API service is stopped; the exact network/access cause is
  unverified. No access rules or networking configuration were changed.
- The public Pages website returned HTTP 200. Its deployed JavaScript asset,
  `/assets/index-CyKLxtHh.js`, explicitly configures
  `https://nanopi-r76s.tail8919df.ts.net` as the API base. It does not contain the
  new `/model-lifecycle/learning-health` request or operations panel. Thus the new
  frontend diagnostics are not present in the inspected deployed bundle.
- The alternative `cowbox.dpdns.org` diagnostics URL returned Cloudflare error
  1033. It is **not** the API base configured in the inspected deployed bundle.

## Requested production verification

| Item | Result |
|---|---|
| 1. Deployment | Not performed; access and backup prerequisites unmet. |
| 2. Continuous learning enabled | Unknown in production. The private environment, including any `PROSPECTIVE_RESEARCH_ENABLED=false` override, was not accessible. |
| 3. Scheduler | Unknown; online host is not scheduler proof. |
| 4. Stored external observations | Unknown; no production database/API response. |
| 5. US coverage | Unknown. |
| 6. HK coverage | Unknown. |
| 7. Source availability | Not probed from NanoPi; earlier development-host Yahoo results do not establish production availability. |
| 8. Research cohort | Production progress unknown. |
| 9. Dataset fingerprints | Production values and transitions unknown. |
| 10. Next training eligibility | Unknown; requires actual coverage/matured-date diagnostics. |
| 11. Paired experiments | Production state unknown; no experiments manually started. |
| 12. Errors/retries | SSH/HTTPS access errors confirmed; provider retry/backoff state unknown. |
| 13. CPU/memory/disk/API usage | Not measured on NanoPi. No local resource figures substituted. |
| 14. Active models/Virtual Trader | No changes made by this session; operational health not verified. |
| 15. Website diagnostics | Static site reachable, correct NanoPi API base, new operations panel absent. |
| 16. Configuration changes | None. |
| 17. Checks | 37 focused backend tests, 31 frontend tests, production frontend build, and `git diff --check` passed locally. |
| 18. Remaining issue | Need authorized working SSH/service access to back up, inspect configuration/timer, deploy, and compare real production snapshots. |

## Local verification executed during this preflight

```powershell
$env:APP_ENV='test'
$env:PROFILE_DB_PATH='data/test_operations_predeploy.db'
python -m unittest tests.test_learning_operations tests.test_prospective_model_research tests.test_continuous_model_learning -q
# Ran 37 tests in 33.565s: OK

# From frontend/
npm.cmd test
# 31 passed, 0 failed
npm.cmd run build
# Passed, 68 modules transformed

git diff --check
# Passed; Git emitted line-ending warnings, not whitespace errors
```

These tests use local/synthetic evidence, not prospective production accuracy.
The frontend tests are Node utility/wiring tests, not browser end-to-end tests.
No standalone lint or type-check scripts are configured in `frontend/package.json`.
The full backend suite was not rerun during this preflight.

## Resume prerequisites

1. Establish an authorized SSH route from this session, or perform the remote
   steps in the user's NanoPi terminal. Do not share private keys, tokens or the
   full environment file.
2. Inspect actual service WorkingDirectory, EnvironmentFiles, service user, and
   automatic pull timer before changing anything. Earlier supplied production
   output used `/srv/stock-assistant-PiPi`; do not assume the documentation's
   `/home/pi` example is the deployed location.
3. Make and verify a consistent backup of the actual persistent SQLite state and
   model metadata/artifacts; account for SQLite WAL and concurrent writers.
   Record active pointers and portfolio/trade/evidence counts before deployment.
4. Review/package the local changes into an explicit revision, then use the
   existing controlled update process after backup and timer coordination.
5. Inspect only relevant environment flags; enable the intended workflow if an
   explicit old override disables it. Do not expose credentials.
6. Verify startup health, then capture successive learning-health snapshots
   across a scheduled cycle to establish actual archive writes, cursor/retry
   progress and evidence admission for both markets. Check resources on NanoPi.
7. A fresh five-session outcome cannot be proven immediately after deployment.
   Report it as pending until real market outcomes mature. No invented history,
   relaxed gates, early context-model promotion, or forced trades.

Read-only initial commands in the NanoPi terminal:

```bash
sudo systemctl show stock-assistant-api -p WorkingDirectory -p EnvironmentFiles --no-pager
systemctl is-active stock-assistant-api
curl -fsS --max-time 30 http://127.0.0.1:8000/model-lifecycle/health
curl -fsS --max-time 30 http://127.0.0.1:8000/model-lifecycle/learning-health
```

A 404 on the last endpoint would indicate that these diagnostics are not exposed
by the running backend; it is not evidence that no older learning workflow runs.

Only this report was added in this deployment-preflight turn. Application code
and all prior working-tree changes were left unchanged.
