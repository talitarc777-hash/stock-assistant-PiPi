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
