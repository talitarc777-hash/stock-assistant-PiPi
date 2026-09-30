"""Versioned model artifacts, active deployments, and rollback-safe pointers.

The older registry is intentionally retained for compatibility.  This service
adds the missing version dimension so a challenger cannot overwrite the model
that the Virtual Trader is currently using.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from app.core.settings import get_settings
from app.core.sqlite_store import model_store_connection
from app.services.market_config import model_security_root, resolve_model_identity
from app.services.model_results import clear_saved_model_artifact_cache


MODEL_VERSION_STATUSES = {
    "trained",
    "validated",
    "shadow",
    "eligible",
    "active",
    "rejected",
    "quarantined",
    "retired",
    "rolled_back",
    "research_shadow",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


class ModelVersionService:
    """Persist immutable challenger versions and one active pointer per target."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        self._initialize()

    def _connect(self):
        return model_store_connection(self.db_path)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_versions (
                    model_version TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    model_role TEXT NOT NULL,
                    artifact_dir TEXT NOT NULL,
                    parent_model_version TEXT,
                    is_validated INTEGER NOT NULL DEFAULT 0,
                    validation_score REAL,
                    feedback_score REAL,
                    feedback_sample_count INTEGER NOT NULL DEFAULT 0,
                    feedback_json TEXT NOT NULL DEFAULT '{}',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    rejection_reasons_json TEXT NOT NULL DEFAULT '[]',
                    retrain_type TEXT,
                    training_end_date TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    activated_at_utc TEXT,
                    deactivated_at_utc TEXT,
                    UNIQUE(
                        market, ticker, period, target_name,
                        model_name, model_version
                    )
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_versions_selection
                ON model_versions(
                    market, ticker, period, target_name,
                    lifecycle_status, is_validated, validation_score
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_model_deployments (
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    active_model_version TEXT NOT NULL,
                    previous_model_version TEXT,
                    activated_at_utc TEXT NOT NULL,
                    probation_started_at_utc TEXT NOT NULL,
                    activation_reason TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY(market, ticker, period, target_name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_deployment_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_model_version TEXT,
                    to_model_version TEXT,
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["is_validated"] = bool(item.get("is_validated"))
        item["metrics_summary"] = _json_load(item.pop("metrics_json", None), {})
        item["feedback_summary"] = _json_load(item.pop("feedback_json", None), {})
        item["rejection_reasons"] = _json_load(
            item.pop("rejection_reasons_json", None), []
        )
        return item

    def register_training_result(
        self,
        *,
        result,
        market: str,
        is_validated: bool,
        validation_score: float,
        rejection_reasons: list[str],
        retrain_type: str,
        parent_model_version: str | None,
        validated_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = resolve_model_identity(result.ticker, market)
        metrics = dict(validated_metrics if validated_metrics is not None else result.metrics or {})
        model_version = str(
            getattr(result.artifact, "model_version", None)
            or metrics.get("model_version")
            or uuid4().hex
        )
        now = _utc_now_iso()
        status = "shadow" if is_validated else "rejected"
        role = "challenger" if is_validated else "none"
        if metrics.get('experimental_only'):
            status, role = 'research_shadow', 'research'
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_versions(
                    model_version, market, ticker, period, target_name,
                    model_name, lifecycle_status, model_role, artifact_dir,
                    parent_model_version, is_validated, validation_score,
                    metrics_json, rejection_reasons_json, retrain_type,
                    training_end_date, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_version) DO UPDATE SET
                    lifecycle_status = excluded.lifecycle_status,
                    model_role = excluded.model_role,
                    artifact_dir = excluded.artifact_dir,
                    is_validated = excluded.is_validated,
                    validation_score = excluded.validation_score,
                    metrics_json = excluded.metrics_json,
                    rejection_reasons_json = excluded.rejection_reasons_json,
                    retrain_type = excluded.retrain_type,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    model_version,
                    identity.market,
                    identity.ticker,
                    str(result.period),
                    str(result.target_name),
                    str(result.model_name).strip().lower(),
                    status,
                    role,
                    str(result.artifact.model_path.parent),
                    parent_model_version,
                    1 if is_validated else 0,
                    float(validation_score),
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(list(dict.fromkeys(rejection_reasons)), ensure_ascii=False),
                    str(retrain_type),
                    metrics.get("generated_at_utc"),
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_version(model_version) or {}

    def get_version(self, model_version: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM model_versions WHERE model_version = ?",
                (str(model_version),),
            ).fetchone()
        return self._row_to_dict(row)

    def get_active(
        self,
        *,
        ticker: str,
        period: str,
        target_name: str,
        market: str = "US",
    ) -> dict[str, Any] | None:
        identity = resolve_model_identity(ticker, market)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT v.*, d.previous_model_version,
                       d.probation_started_at_utc, d.activation_reason
                FROM active_model_deployments d
                JOIN model_versions v
                  ON v.model_version = d.active_model_version
                 AND v.market = d.market AND v.ticker = d.ticker
                 AND v.period = d.period AND v.target_name = d.target_name
                WHERE d.market = ? AND d.ticker = ? AND d.period = ?
                  AND d.target_name = ?
                  AND v.is_validated = 1 AND v.lifecycle_status = 'active'
                  AND COALESCE(json_extract(v.metrics_json,'$.experimental_only'),0)=0
                """,
                (identity.market, identity.ticker, str(period), str(target_name)),
            ).fetchone()
        return self._row_to_dict(row)

    def list_versions(
        self,
        *,
        market: str | None = None,
        ticker: str | None = None,
        period: str | None = None,
        target_name: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 500,
        oldest_first: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if market:
            clauses.append("market = ?")
            params.append(str(market).strip().upper())
        if ticker:
            identity = resolve_model_identity(ticker, market or "US")
            clauses.extend(["market = ?", "ticker = ?"])
            params.extend([identity.market, identity.ticker])
        if period:
            clauses.append("period = ?")
            params.append(str(period))
        if target_name:
            clauses.append("target_name = ?")
            params.append(str(target_name))
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"lifecycle_status IN ({placeholders})")
            params.extend(statuses)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM model_versions
                {where}
                ORDER BY updated_at_utc {"ASC" if oldest_first else "DESC"}, model_version ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_dict(row) or {} for row in rows]

    def list_shadow_challengers(
        self,
        *,
        ticker: str,
        periods: tuple[str, ...],
        target_name: str,
        market: str = "US",
        limit: int = 1,
        observation_date: str | None = None,
    ) -> list[dict[str, Any]]:
        identity = resolve_model_identity(ticker, market)
        if not periods:
            return []
        placeholders = ",".join("?" for _ in periods)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                WITH candidates AS (SELECT v.*,
                       CASE WHEN d.previous_model_version = v.model_version
                            THEN 1 ELSE 0 END AS rollback_candidate,
                       ROW_NUMBER() OVER (
                           PARTITION BY v.ticker,v.period,v.model_name,
                               CASE WHEN d.previous_model_version=v.model_version THEN 1 ELSE 0 END
                           ORDER BY v.created_at_utc ASC,v.model_version ASC
                       ) AS family_rank
                FROM model_versions v
                LEFT JOIN active_model_deployments d
                  ON d.market = v.market AND d.ticker = v.ticker
                 AND d.period = v.period AND d.target_name = v.target_name
                WHERE v.market = ?
                  AND v.ticker IN (?, 'GLOBAL')
                  AND v.period IN ({placeholders})
                  AND v.target_name = ?
                  AND v.is_validated = 1
                  AND v.lifecycle_status IN ('shadow', 'eligible')
                  AND COALESCE(json_extract(v.metrics_json,'$.experimental_only'),0)=0
                ) SELECT * FROM candidates WHERE (? = 0 OR family_rank = 1)
                ORDER BY rollback_candidate DESC,
                         CASE WHEN ticker = ? THEN 0 ELSE 1 END,
                         CASE WHEN ? = 1 THEN created_at_utc END ASC,
                         validation_score DESC,
                         created_at_utc ASC,model_version ASC
                LIMIT ?
                """,
                (
                    identity.market,
                    identity.ticker,
                    *periods,
                    str(target_name),
                    int(bool(observation_date)),
                    identity.ticker,
                    int(bool(observation_date)),
                    4 if observation_date else max(1, int(limit)),
                ),
            ).fetchall()
        candidates = [self._row_to_dict(row) or {} for row in rows]
        if not observation_date or not candidates:
            return candidates
        # At most four distinct model families/windows, one selected per market
        # date. Repeated intraday cycles retain the same allocation and dedupe.
        # Oldest unresolved member of each family stays in the cohort; daily
        # training must not replace it before its forward evidence matures.
        cohort = candidates
        day = datetime.fromisoformat(observation_date).date().toordinal()
        offset = int(hashlib.sha256(f"{identity.market}:{identity.ticker}".encode()).hexdigest()[:8], 16)
        start = (day + offset) % len(cohort)
        return [cohort[(start + index) % len(cohort)] for index in range(min(max(1, limit), len(cohort)))]

    def bootstrap_active_from_registry(self, row: dict[str, Any]) -> dict[str, Any] | None:
        configured_db = Path(get_settings().profile_db_path)
        if self.db_path.resolve() != configured_db.resolve():
            # Isolated audit/test registries must not copy or mutate the real
            # configured model store merely because they contain a test row.
            return None
        metrics = dict(row.get("metrics_summary") or {})
        market = str(row.get("market") or "US")
        identity = resolve_model_identity(str(row["ticker"]), market)
        canonical_dir = (
            model_security_root(
                Path(get_settings().research_models_dir),
                identity.market,
                identity.ticker,
            )
            / str(row["period"])
            / str(row["target_name"])
            / str(row["model_name"])
        )
        if not (canonical_dir / "model.pkl").exists():
            return None
        seed = "|".join(
            (
                identity.market,
                identity.ticker,
                str(row["period"]),
                str(row["target_name"]),
                str(row["model_name"]),
                str(row.get("last_trained_at_utc") or "legacy"),
            )
        )
        version = "legacy-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        # A legacy registry row points at the mutable canonical directory.
        # Snapshot it before any future promotion can replace those files.
        immutable_dir = canonical_dir / "versions" / version
        immutable_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "model.pkl",
            "feature_list.json",
            "metrics_summary.json",
            "predictions.csv",
            "evaluation_table.csv",
        ):
            source = canonical_dir / name
            destination = immutable_dir / name
            if source.exists() and not destination.exists():
                shutil.copy2(source, destination)
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO model_versions(
                    model_version, market, ticker, period, target_name,
                    model_name, lifecycle_status, model_role, artifact_dir,
                    is_validated, validation_score, metrics_json,
                    retrain_type, training_end_date, created_at_utc,
                    updated_at_utc, activated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', 'incumbent', ?, 1, ?, ?,
                          'legacy_bootstrap', ?, ?, ?, ?)
                """,
                (
                    version,
                    identity.market,
                    identity.ticker,
                    str(row["period"]),
                    str(row["target_name"]),
                    str(row["model_name"]).lower(),
                    str(immutable_dir),
                    row.get("validation_score"),
                    json.dumps(metrics, ensure_ascii=False),
                    row.get("last_trained_at_utc"),
                    now,
                    now,
                    row.get("last_promoted_at_utc") or now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO active_model_deployments(
                    market, ticker, period, target_name, active_model_version,
                    previous_model_version, activated_at_utc,
                    probation_started_at_utc, activation_reason, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'legacy_registry_bootstrap', ?)
                """,
                (
                    identity.market,
                    identity.ticker,
                    str(row["period"]),
                    str(row["target_name"]),
                    version,
                    row.get("last_promoted_at_utc") or now,
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_active(
            ticker=identity.ticker,
            period=str(row["period"]),
            target_name=str(row["target_name"]),
            market=identity.market,
        )

    def set_status(
        self,
        model_version: str,
        lifecycle_status: str,
        *,
        model_role: str | None = None,
    ) -> None:
        if lifecycle_status not in MODEL_VERSION_STATUSES:
            raise ValueError(f"Unsupported model version status: {lifecycle_status}")
        with self._connect() as conn:
            if model_role is None:
                conn.execute(
                    "UPDATE model_versions SET lifecycle_status = ?, updated_at_utc = ? "
                    "WHERE model_version = ?",
                    (lifecycle_status, _utc_now_iso(), str(model_version)),
                )
            else:
                conn.execute(
                    "UPDATE model_versions SET lifecycle_status = ?, model_role = ?, "
                    "updated_at_utc = ? WHERE model_version = ?",
                    (
                        lifecycle_status,
                        str(model_role),
                        _utc_now_iso(),
                        str(model_version),
                    ),
                )
            conn.commit()

    def list_deployment_events(
        self,
        *,
        market: str | None = None,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if market:
            clauses.append("market = ?")
            params.append(str(market).strip().upper())
        if ticker:
            identity = resolve_model_identity(ticker, market or "US")
            clauses.extend(["market = ?", "ticker = ?"])
            params.extend([identity.market, identity.ticker])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM model_deployment_events {where}
                ORDER BY created_at_utc DESC, id DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["evidence"] = _json_load(item.pop("evidence_json", None), {})
            output.append(item)
        return output

    @staticmethod
    def _publish_artifact(version: dict[str, Any]) -> Path:
        source = Path(str(version["artifact_dir"]))
        identity = resolve_model_identity(version["ticker"], version["market"])
        destination = (
            model_security_root(
                Path(get_settings().research_models_dir),
                identity.market,
                identity.ticker,
            )
            / str(version["period"])
            / str(version["target_name"])
            / str(version["model_name"])
        )
        required = (
            "model.pkl",
            "feature_list.json",
            "metrics_summary.json",
            "predictions.csv",
            "evaluation_table.csv",
        )
        missing = [name for name in required if not (source / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"Model version {version['model_version']} is missing artifacts: {missing}"
            )
        destination.mkdir(parents=True, exist_ok=True)
        if source.resolve() == destination.resolve():
            return destination
        stage = destination / f".promotion-{uuid4().hex}"
        stage.mkdir(parents=True, exist_ok=False)
        try:
            for name in required:
                shutil.copy2(source / name, stage / name)
            for name in required:
                os.replace(stage / name, destination / name)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        clear_saved_model_artifact_cache()
        return destination

    def activate_version(
        self,
        model_version: str,
        *,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = self.get_version(model_version)
        if version is None:
            raise ValueError(f"Unknown model version: {model_version}")
        if (version.get('metrics_summary') or {}).get('experimental_only'):
            raise ValueError('Research-only models cannot activate; a separately reviewed deployment contract is required')
        if not version["is_validated"]:
            raise ValueError("An unvalidated model version cannot become active.")
        self._publish_artifact(version)
        now = _utc_now_iso()
        with self._connect() as conn:
            current = conn.execute(
                """
                SELECT active_model_version FROM active_model_deployments
                WHERE market = ? AND ticker = ? AND period = ? AND target_name = ?
                """,
                (
                    version["market"],
                    version["ticker"],
                    version["period"],
                    version["target_name"],
                ),
            ).fetchone()
            previous = str(current["active_model_version"]) if current else None
            if previous and previous != model_version:
                conn.execute(
                    """
                    UPDATE model_versions
                    SET lifecycle_status = 'shadow', model_role = 'rollback_candidate',
                        deactivated_at_utc = ?, updated_at_utc = ?
                    WHERE model_version = ?
                    """,
                    (now, now, previous),
                )
            conn.execute(
                """
                UPDATE model_versions
                SET lifecycle_status = 'active', model_role = 'incumbent',
                    activated_at_utc = ?, deactivated_at_utc = NULL,
                    updated_at_utc = ?
                WHERE model_version = ?
                """,
                (now, now, model_version),
            )
            conn.execute(
                """
                INSERT INTO active_model_deployments(
                    market, ticker, period, target_name, active_model_version,
                    previous_model_version, activated_at_utc,
                    probation_started_at_utc, activation_reason, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, ticker, period, target_name) DO UPDATE SET
                    active_model_version = excluded.active_model_version,
                    previous_model_version = excluded.previous_model_version,
                    activated_at_utc = excluded.activated_at_utc,
                    probation_started_at_utc = excluded.probation_started_at_utc,
                    activation_reason = excluded.activation_reason,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    version["market"],
                    version["ticker"],
                    version["period"],
                    version["target_name"],
                    model_version,
                    previous if previous != model_version else version.get("previous_model_version"),
                    now,
                    now,
                    str(reason),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO model_deployment_events(
                    market, ticker, period, target_name, event_type,
                    from_model_version, to_model_version, reason,
                    evidence_json, created_at_utc
                ) VALUES (?, ?, ?, ?, 'promotion', ?, ?, ?, ?, ?)
                """,
                (
                    version["market"],
                    version["ticker"],
                    version["period"],
                    version["target_name"],
                    previous,
                    model_version,
                    str(reason),
                    json.dumps(evidence or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
        return self.get_version(model_version) or version

    def rollback(
        self,
        *,
        active_model_version: str,
        previous_model_version: str,
        reason: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        failed = self.get_version(active_model_version)
        previous = self.get_version(previous_model_version)
        if failed is None or previous is None:
            raise ValueError("Rollback versions are unavailable.")
        if any((v.get('metrics_summary') or {}).get('experimental_only') for v in (failed, previous)):
            raise ValueError('Research-only versions cannot participate in production rollback')
        self._publish_artifact(previous)
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE model_versions SET lifecycle_status = 'rolled_back',
                    model_role = 'none', deactivated_at_utc = ?, updated_at_utc = ?
                WHERE model_version = ?
                """,
                (now, now, active_model_version),
            )
            conn.execute(
                """
                UPDATE model_versions SET lifecycle_status = 'active',
                    model_role = 'incumbent', activated_at_utc = ?,
                    deactivated_at_utc = NULL, updated_at_utc = ?
                WHERE model_version = ?
                """,
                (now, now, previous_model_version),
            )
            conn.execute(
                """
                UPDATE active_model_deployments
                SET active_model_version = ?, previous_model_version = ?,
                    activated_at_utc = ?, probation_started_at_utc = ?,
                    activation_reason = ?, updated_at_utc = ?
                WHERE market = ? AND ticker = ? AND period = ? AND target_name = ?
                """,
                (
                    previous_model_version,
                    active_model_version,
                    now,
                    now,
                    f"rollback:{reason}",
                    now,
                    failed["market"],
                    failed["ticker"],
                    failed["period"],
                    failed["target_name"],
                ),
            )
            conn.execute(
                """
                INSERT INTO model_deployment_events(
                    market, ticker, period, target_name, event_type,
                    from_model_version, to_model_version, reason,
                    evidence_json, created_at_utc
                ) VALUES (?, ?, ?, ?, 'rollback', ?, ?, ?, ?, ?)
                """,
                (
                    failed["market"],
                    failed["ticker"],
                    failed["period"],
                    failed["target_name"],
                    active_model_version,
                    previous_model_version,
                    str(reason),
                    json.dumps(evidence or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
        return self.get_version(previous_model_version) or previous

    def update_feedback(
        self,
        model_version: str,
        feedback_summary: dict[str, Any],
        *,
        lifecycle_status: str | None = None,
    ) -> None:
        status = lifecycle_status
        if status is not None and status not in MODEL_VERSION_STATUSES:
            raise ValueError(f"Unsupported model version status: {status}")
        score = feedback_summary.get("feedback_score")
        count = int(feedback_summary.get("sample_count") or 0)
        with self._connect() as conn:
            if status is None:
                conn.execute(
                    """
                    UPDATE model_versions
                    SET feedback_score = ?, feedback_sample_count = ?,
                        feedback_json = ?, updated_at_utc = ?
                    WHERE model_version = ?
                    """,
                    (
                        score,
                        count,
                        json.dumps(feedback_summary, ensure_ascii=False),
                        _utc_now_iso(),
                        str(model_version),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE model_versions
                    SET feedback_score = ?, feedback_sample_count = ?,
                        feedback_json = ?, lifecycle_status = ?,
                        updated_at_utc = ?
                    WHERE model_version = ?
                    """,
                    (
                        score,
                        count,
                        json.dumps(feedback_summary, ensure_ascii=False),
                        status,
                        _utc_now_iso(),
                        str(model_version),
                    ),
                )
            conn.commit()

    def get_funnel(self, market: str | None = None) -> dict[str, Any]:
        clauses = "WHERE market = ?" if market else ""
        params = (str(market).upper(),) if market else ()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT lifecycle_status, COUNT(*) AS count
                FROM model_versions {clauses}
                GROUP BY lifecycle_status
                """,
                params,
            ).fetchall()
            event_rows = conn.execute(
                f"""
                SELECT event_type, COUNT(*) AS count
                FROM model_deployment_events {clauses}
                GROUP BY event_type
                """,
                params,
            ).fetchall()
            promotion_rows = conn.execute(
                f"""
                SELECT reason, COUNT(*) AS count
                FROM model_deployment_events {clauses}
                AND event_type = 'promotion'
                GROUP BY reason
                """ if clauses else """
                SELECT reason, COUNT(*) AS count
                FROM model_deployment_events
                WHERE event_type = 'promotion'
                GROUP BY reason
                """,
                params,
            ).fetchall()
        counts = {str(row["lifecycle_status"]): int(row["count"]) for row in rows}
        events = {str(row["event_type"]): int(row["count"]) for row in event_rows}
        promotions_by_reason = {
            str(row["reason"]): int(row["count"]) for row in promotion_rows
        }
        initial_activations = int(
            promotions_by_reason.get("initial_validated_incumbent", 0)
        )
        all_promotions = int(events.get("promotion", 0))
        promoted = max(0, all_promotions - initial_activations)
        eligible = int(counts.get("eligible", 0)) + promoted
        return {
            "trained": sum(counts.values()),
            "research_shadow": counts.get('research_shadow', 0),
            "validated": sum(
                counts.get(status, 0)
                for status in (
                    "validated",
                    "shadow",
                    "eligible",
                    "active",
                    "retired",
                    "rolled_back",
                )
            ),
            "shadow": counts.get("shadow", 0),
            "eligible": counts.get("eligible", 0),
            "promoted": promoted,
            "initial_activations": initial_activations,
            "active": counts.get("active", 0),
            "rejected": counts.get("rejected", 0),
            "quarantined": counts.get("quarantined", 0),
            "retired": counts.get("retired", 0),
            "rolled_back": counts.get("rolled_back", 0),
            "promotion_rate": (promoted / eligible) if eligible else None,
            "rollback_rate": (
                int(events.get("rollback", 0)) / promoted if promoted else 0.0
            ),
            "status_counts": counts,
            "event_counts": events,
            "promotions_by_reason": promotions_by_reason,
        }
