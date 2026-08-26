"""Tests for immutable model deployment pointers and rollback."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil
import unittest
from unittest.mock import patch
import uuid

from app.services.model_version_service import ModelVersionService
from app.services.model_lifecycle_service import ModelLifecycleService


REQUIRED_ARTIFACTS = (
    "model.pkl",
    "feature_list.json",
    "metrics_summary.json",
    "predictions.csv",
    "evaluation_table.csv",
)


def _training_result(root: Path, version: str, marker: str) -> SimpleNamespace:
    artifact_dir = root / "sources" / version
    artifact_dir.mkdir(parents=True)
    for name in REQUIRED_ARTIFACTS:
        (artifact_dir / name).write_text(
            marker if name == "model.pkl" else "{}" if name.endswith(".json") else "x\n",
            encoding="utf-8",
        )
    artifact = SimpleNamespace(
        model_path=artifact_dir / "model.pkl",
        model_version=version,
    )
    return SimpleNamespace(
        ticker="AAPL",
        period="2y",
        target_name="target_5d_return",
        model_name="linear_regression",
        metrics={"model_version": version, "generated_at_utc": "2026-08-01T00:00:00+00:00"},
        artifact=artifact,
    )


class ModelVersionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        token = uuid.uuid4().hex
        self.root = Path("data") / f"test_model_version_assets_{token}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = Path("data") / f"test_model_version_{token}.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                try:
                    path.unlink()
                except PermissionError:
                    pass

    def test_promotion_changes_pointer_and_rollback_restores_artifact(self) -> None:
        root = self.root
        settings = SimpleNamespace(research_models_dir=str(root / "models"))
        with patch("app.services.model_version_service.get_settings", return_value=settings):
            service = ModelVersionService(db_path=str(self.db_path))
            first = service.register_training_result(
                result=_training_result(root, "v1", "first"),
                market="US",
                is_validated=True,
                validation_score=0.62,
                rejection_reasons=[],
                retrain_type="test",
                parent_model_version=None,
            )
            service.activate_version(
                "v1", reason="initial_validated_incumbent", evidence={}
            )
            canonical = (
                root / "models" / "AAPL" / "2y" / "target_5d_return" / "linear_regression"
            )
            self.assertEqual((canonical / "model.pkl").read_text(encoding="utf-8"), "first")

            service.register_training_result(
                result=_training_result(root, "v2", "second"),
                market="US",
                is_validated=True,
                validation_score=0.66,
                rejection_reasons=[],
                retrain_type="test",
                parent_model_version=first["model_version"],
            )
            service.activate_version("v2", reason="forward_better", evidence={})
            active = service.get_active(
                ticker="AAPL", period="2y", target_name="target_5d_return"
            )
            self.assertEqual(active["model_version"], "v2")
            self.assertEqual(active["previous_model_version"], "v1")
            self.assertEqual((canonical / "model.pkl").read_text(encoding="utf-8"), "second")
            funnel = service.get_funnel("US")
            self.assertEqual(funnel["initial_activations"], 1)
            self.assertEqual(funnel["promoted"], 1)

            service.rollback(
                active_model_version="v2",
                previous_model_version="v1",
                reason="probation_degraded",
                evidence={},
            )
            restored = service.get_active(
                ticker="AAPL", period="2y", target_name="target_5d_return"
            )
            self.assertEqual(restored["model_version"], "v1")
            self.assertEqual((canonical / "model.pkl").read_text(encoding="utf-8"), "first")
            self.assertEqual(service.get_version("v2")["lifecycle_status"], "rolled_back")

    def test_shadow_evidence_promotes_and_probation_can_rollback(self) -> None:
        root = self.root
        settings = SimpleNamespace(research_models_dir=str(root / "models"))
        with patch("app.services.model_version_service.get_settings", return_value=settings):
            lifecycle = ModelLifecycleService(db_path=str(self.db_path))
            for version, marker, score, parent in (
                ("v1", "first", 0.61, None),
                ("v2", "second", 0.66, "v1"),
            ):
                lifecycle.version_service.register_training_result(
                    result=_training_result(root, version, marker),
                    market="US",
                    is_validated=True,
                    validation_score=score,
                    rejection_reasons=[],
                    retrain_type="test",
                    parent_model_version=parent,
                )
            lifecycle.version_service.activate_version("v1", reason="initial", evidence={})

            promotion_summaries = {
                "v1": {
                    "sample_count": 12, "effective_sample_size": 11.0,
                    "time_coverage_days": 20, "feedback_score": 0.54,
                    "direction_accuracy": 0.55,
                    "direction_accuracy_interval_90": {"low": 0.35, "high": 0.72},
                    "average_strategy_net_return_pct": 0.10,
                },
                "v2": {
                    "sample_count": 12, "effective_sample_size": 11.0,
                    "time_coverage_days": 20, "feedback_score": 0.62,
                    "direction_accuracy": 0.70,
                    "direction_accuracy_interval_90": {"low": 0.50, "high": 0.84},
                    "average_strategy_net_return_pct": 0.40,
                },
            }
            with patch.object(
                lifecycle.feedback_service,
                "get_model_summary",
                side_effect=lambda **kwargs: promotion_summaries[kwargs["model_version"]],
            ):
                result = lifecycle.refresh_feedback_scores()
            self.assertEqual(result["versioned_promoted"], 1)
            active = lifecycle.version_service.get_active(
                ticker="AAPL", period="2y", target_name="target_5d_return"
            )
            self.assertEqual(active["model_version"], "v2")
            self.assertEqual(active["previous_model_version"], "v1")
            runtime = lifecycle.resolve_runtime_model_candidates(
                ticker="AAPL",
                period="2y",
                periods=("2y",),
                target_name="target_5d_return",
            )
            self.assertEqual(runtime[0]["model_version"], "v2")
            self.assertEqual(runtime[0]["model_role"], "incumbent")
            self.assertTrue(Path(runtime[0]["artifact_dir"]).is_dir())

            rollback_summaries = {
                "v1": {
                    "sample_count": 16, "effective_sample_size": 14.0,
                    "time_coverage_days": 30, "feedback_score": 0.68,
                    "direction_accuracy": 0.75,
                    "direction_accuracy_interval_90": {"low": 0.55, "high": 0.87},
                    "average_strategy_net_return_pct": 0.50,
                },
                "v2": {
                    "sample_count": 16, "effective_sample_size": 14.0,
                    "time_coverage_days": 30, "feedback_score": 0.47,
                    "direction_accuracy": 0.38,
                    "direction_accuracy_interval_90": {"low": 0.22, "high": 0.48},
                    "average_strategy_net_return_pct": -0.40,
                },
            }
            with patch.object(
                lifecycle.feedback_service,
                "get_model_summary",
                side_effect=lambda **kwargs: rollback_summaries[
                    kwargs.get("model_version", "v2")
                ],
            ):
                result = lifecycle.refresh_feedback_scores()
            self.assertEqual(result["versioned_rolled_back"], 1)
            restored = lifecycle.version_service.get_active(
                ticker="AAPL", period="2y", target_name="target_5d_return"
            )
            self.assertEqual(restored["model_version"], "v1")


if __name__ == "__main__":
    unittest.main()
