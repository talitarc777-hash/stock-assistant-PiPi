# Recover space from unused rejected model binaries

Run `scripts/prune_unused_model_binaries.py` from the production checkout. It
opens the supplied SQLite database read-only, previews by default, and deletes
only old rejected `model.pkl` files when explicitly applied. It does not remove
database records, directories, CSV results, JSON metrics or canonical models.

Default retention protects the newest two rejected fits per market/ticker/window/
target/model family and all fits less than seven days old. It also protects all
non-rejected or validated versions, experimental models, deployment history,
active/previous pointers, parents, pending feedback, research-round references,
registry provenance and training-recovery artifacts. Unknown filesystem layouts,
symlinks and hard links are skipped. Missing required schema stops cleanup.

This is an explicit maintenance command, not an automatic retention scheduler.
It can leave most storage in historical CSV evidence; previews determine actual
recoverable bytes. It does not claim to reclaim all 40 GB or repair existing
partially written artifacts. Research/model selection thresholds are unchanged.

## Transfer and select the actual database

If the script has not been deployed, transfer just this standalone script to
`/srv/stock-assistant-PiPi/scripts/prune_unused_model_binaries.py`. It uses the
Python standard library and needs no additional packages or service restart.

Determine the running API's actual `PROFILE_DB_PATH`, `APP_ENV`, service user and
environment file from its service configuration. Do not select a tracked test or
legacy database because its filename looks plausible. In production, an absolute
`PROFILE_DB_PATH` is used directly; a relative/default profile path resolves to
the service user's `~/.local/share/stock-assistant/user_profiles.db`. The `HOME`
and any systemd drop-in overrides must match that service user. This behavior is
implemented in `app/core/settings.py`.

```bash
sudo systemctl show stock-assistant-api -p User -p WorkingDirectory -p EnvironmentFiles --no-pager
systemctl list-timers --all --no-pager | grep -Ei 'stock|model|deploy|pull'
```

Set a dedicated variable to that verified absolute database path:

```bash
cd /srv/stock-assistant-PiPi
DB_PATH='/verified/absolute/path/to/user_profiles.db'
.venv/bin/python scripts/prune_unused_model_binaries.py --db "$DB_PATH"
```

The preview reports individual candidates and `candidate_bytes`. A zero-byte
preview is a valid result; do not bypass its guards or delete arbitrary versions.

## Apply during a maintenance window

Stop the API, any stock-assistant bot service that accesses models, external
training jobs, and any automatic pull/deploy timer identified above. Keep track
of which services/timers were running so only those are restarted. The tool's
`--writers-stopped` flag is the operator's confirmation; it cannot independently
detect every process that may modify artifacts. Do not run it against a live
writer or simultaneously with another cleanup.

```bash
sudo systemctl stop stock-assistant-api
# Stop the other actual model writers/timers identified above as needed.
.venv/bin/python scripts/prune_unused_model_binaries.py --db "$DB_PATH" --apply --writers-stopped
df -h /srv/stock-assistant-PiPi/data/models
sudo systemctl start stock-assistant-api
# Restore only the other services/timers that were previously running.
```

Run as the service/file owner; elevated privileges may be needed for artifacts
owned by another account. The command re-reads database references before apply
and checks each candidate's identity and size immediately before unlinking.
Permissions/errors stop it; previously removed files are reported only if the
command reaches successful completion. Retrying safely skips absent binaries.

Wait for API startup, then inspect `/model-lifecycle/health` and subsequent
`/model-lifecycle/runs?limit=5`. Free capacity is required for successful training;
no forced training or model promotion is part of this procedure. If reclaimed
capacity is inadequate, retain the evidence and use additional storage or a
separately reviewed evidence archival procedure.

## Compress historical CSVs after binary cleanup

CSV results can dominate space even after fitted binaries are pruned. Deploy
`scripts/compress_unused_model_results.py`, the updated binary-pruning script,
`app/core/artifact_csv.py`, and the updated CSV readers in `model_results.py`,
`model_lifecycle_service.py` and `scripts/audit_model_quality.py` together before
applying compression. No packages are required beyond the existing environment.

The compressor uses the same age and database reference protections and only
selects directories whose fitted binary is already absent. It retains CSV
evidence as `.csv.gz`, verifies decompressed SHA-256 equality, stages one file at
a time, and checks available temporary space and concurrent changes. Original
CSV files are removed only after a verified gzip has been installed. Canonical,
active, rollback and research model files stay in their existing form. Historical
CSV readers understand the compressed fallback. External tools reading a retained
version directly must use its `.csv.gz` path. The command is rerunnable.

```bash
cd /srv/stock-assistant-PiPi
DB_PATH='/home/pi/.local/share/stock-assistant/user_profiles.db'
.venv/bin/python scripts/compress_unused_model_results.py --db "$DB_PATH"
# Stop the API and any other model writers/deployment timers as above.
sudo systemctl stop stock-assistant-api
.venv/bin/python scripts/compress_unused_model_results.py --db "$DB_PATH" --apply --writers-stopped
df -h /
sudo systemctl start stock-assistant-api
# Restore any other previously running services/timers.
```

The preview reports eligible source bytes, not promised savings. Apply reports
actual bytes reclaimed, including partial progress if an error stops the run.
Do not delete trade-log rows or run VACUUM solely from aggregate size: inspect
their action/date distribution, preserve BUY/SELL records and required evidence,
and verify free space before any database rebuilding operation.

## Discard unused rejected-version details instead of compressing

`scripts/prune_unused_model_results.py` is an explicitly destructive alternative
for old rejected versions whose fit binaries were already removed. It uses the
same seven-day and newest-two-per-family protection and database-reference checks
as binary cleanup. A valid retained `metrics_summary.json` is required. Only
`predictions.csv`, `evaluation_table.csv` and their `.gz` counterparts are removed.
No database rows, JSON metrics, other CSVs or operational model artifacts change.
Their detailed historical backtest can no longer be replayed after removal;
summary scores/rejection reasons and recorded feedback remain.

Copy both the current `prune_unused_model_binaries.py` and new
`prune_unused_model_results.py` into the production `scripts/` directory. This
option uses standard-library Python only and needs no application-code deployment
or compression-reader changes. Older binary-cleanup scripts lack the shared
`eligible_directories` result and must be updated.

From Windows PowerShell in the local repository:

```powershell
scp scripts/prune_unused_model_binaries.py scripts/prune_unused_model_results.py pi@nanopi-r76s.tail8919df.ts.net:/srv/stock-assistant-PiPi/scripts/
```

On NanoPi, preview without changing anything:

```bash
cd /srv/stock-assistant-PiPi
DB_PATH='/home/pi/.local/share/stock-assistant/user_profiles.db'
.venv/bin/python scripts/prune_unused_model_results.py --db "$DB_PATH"
```

`candidate_gib` is the total eligible file size, not the entire CSV footprint.
Zero eligible files is valid. No age-based `find ... -delete` is necessary.

Stop all actual artifact writers (API, bot if present, manual training jobs and
automatic deploy/training timers) before applying. Restore only services/timers
that were previously running, including after an error. Example for the API:

```bash
sudo systemctl stop stock-assistant-api
.venv/bin/python scripts/prune_unused_model_results.py --db "$DB_PATH" --apply --writers-stopped
df -h /
du -sh data/models
sudo systemctl start stock-assistant-api
```

The result reports removed-file count and actual file bytes reclaimed, including
partial progress if stopped by an error. Confirm API readiness rather than relying
only on systemd's process status:

```bash
curl --retry 15 --retry-connrefused --retry-delay 2 --max-time 10 -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/model-lifecycle/health
```

This affects filesystem results only; the large `live_trader_trade_log` database
table needs its own inspected retention/archive procedure. It does not require
VACUUM or change trading decisions, prediction targets or validation thresholds.
