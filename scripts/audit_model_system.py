"""Read-only snapshot audit; no application imports, training, or deployment.

Run on the NanoPi with its real database and model paths for production counts.
Stored validation flags are reported as claims, not independently certified.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3


def _json(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def audit_database(path: Path, recent_limit: int = 10000) -> dict:
    """Use SQLite read-only URI and one consistent transaction; omit user data."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("BEGIN")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table):
            return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}"')] if table in tables else []
        logs = rows("live_trader_trade_log")
        registry = rows("market_model_registry")
        versions = rows("model_versions")
        deployments = rows("active_model_deployments")
        feedback = rows("model_decision_feedback")
        result = {
            "database": str(path.resolve()),
            "scope": "local database snapshot, not proof of production state",
            "table_counts": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                             for t in ("live_trader_trade_log", "market_model_registry", "model_versions",
                                       "active_model_deployments", "model_decision_feedback", "model_lifecycle_runs") if t in tables},
            "markets": {},
        }
        for market in ("US", "HK"):
            market_logs = [r for r in logs if (r.get("market") or "US") == market]
            recent = sorted(market_logs, key=lambda r: r["id"], reverse=True)[:recent_limit]
            sources, reasons, failures, actions, adoption, missing = (Counter() for _ in range(6))
            for row in recent:
                meta = _json(row.get("metadata_json"))
                source = meta.get("decision_source") or "unrecorded"
                sources[source] += 1
                actions[row.get("action", "unknown")] += 1
                if source == "fallback_rule":
                    reasons[meta.get("model_resolution_reason") or (
                        "legacy_recorded_load_errors" if meta.get("model_load_errors")
                        else "legacy_cause_not_recorded"
                    )] += 1
                    for error in meta.get("model_load_errors") or []:
                        failures[error.split(":", 1)[-1]] += 1
                version = meta.get("model_version")
                if source not in ("fallback_rule", "unrecorded"):
                    adoption["version_recorded" if version else "version_missing"] += 1
                    if version and version not in {v["model_version"] for v in versions}:
                        missing["version_not_in_database"] += 1
            regs = [r for r in registry if r["market"] == market]
            rejected = Counter()
            gate_versions = Counter()
            for row in regs:
                metrics = _json(row.get("metrics_json"))
                gate_versions[str(metrics.get("validation_gate_version", "missing"))] += 1
                for key in ("walk_forward_quality_gate", "historical_trading_quality_gate"):
                    rejected.update((metrics.get(key) or {}).get("reasons") or [])
            fb = [r for r in feedback if (r.get("market") or "US") == market]
            result["markets"][market] = {
                "all_decisions": len(market_logs), "recent_decisions": len(recent),
                "date_min": min((r["timestamp"] for r in market_logs), default=None),
                "date_max": max((r["timestamp"] for r in market_logs), default=None),
                "recent_sources": dict(sources), "recent_actions": dict(actions),
                "fallback_pct": 100 * sources["fallback_rule"] / len(recent) if recent else None,
                "fallback_resolution_causes": dict(reasons), "load_errors": dict(failures.most_common(12)),
                "adoption": dict(adoption + missing),
                "registry_rows": len(regs),
                "registry_validation_claims": sum(bool(r["is_validated"]) for r in regs),
                "registry_statuses": dict(Counter(r["status"] for r in regs)),
                "registry_gate_versions": dict(gate_versions),
                "rejection_reasons": dict(rejected.most_common(12)),
                "version_count": sum(v["market"] == market for v in versions),
                "deployment_count": sum(d["market"] == market for d in deployments),
                "feedback_statuses": dict(Counter(r["status"] for r in fb)),
            }
        return result
    finally:
        conn.rollback()
        conn.close()


def audit_artifacts(root: Path) -> dict:
    files = sorted(root.rglob("model.pkl"))
    fingerprint = hashlib.sha256()
    for path in files:
        fingerprint.update(path.relative_to(root).as_posix().encode())
        fingerprint.update(hashlib.sha256(path.read_bytes()).digest())
    summaries = []
    for path in sorted(root.rglob("metrics_summary.json")):
        metrics = _json(path.read_text(encoding="utf-8"))
        summaries.append(metrics)
    return {
        "root": str(root.resolve()), "model_files": len(files),
        "immutable_model_files": sum("versions" in p.relative_to(root).parts for p in files),
        "model_content_sha256": fingerprint.hexdigest(),
        "summary_files": len(summaries),
        "validation_schemes": dict(Counter(str(m.get("validation_scheme_version", "missing")) for m in summaries)),
        "targets": dict(Counter(str(m.get("target_name", "missing")) for m in summaries)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_database(args.db)
    if args.models:
        report["artifacts"] = audit_artifacts(args.models)
    content = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        # Never allow a report path to replace the inspected input or artifacts.
        output = args.output.resolve()
        if output == args.db.resolve() or (args.models and output.is_relative_to(args.models.resolve())):
            parser.error("Output must be separate from database/model inputs")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content + "\n", encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
