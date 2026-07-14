"""Discord formatting contracts for beginner-facing live trader evidence."""

import unittest

from bot.formatter import (
    format_benchmark_shadow_message,
    format_live_virtual_trader_status_message,
    format_model_accuracy_message,
    format_sync_status_message,
    format_trade_reason_message,
)


class BotVirtualTraderFormatterTests(unittest.TestCase):
    def test_shadow_status_explains_forward_gate_without_profit_claim(self) -> None:
        message = format_benchmark_shadow_message(
            "SPY",
            {
                "summary": {
                    "passed": False,
                    "sample_count": 3,
                    "pending_count": 2,
                    "latest_observation_date": "2026-07-13",
                    "latest_observation_status": "pending",
                    "estimated_next_maturity_date": "2026-07-20",
                    "required_sample_count": 8,
                    "active_signal_count": 1,
                    "required_active_signal_count": 5,
                    "direction_accuracy": 2 / 3,
                    "average_active_net_return_pct": 0.2,
                    "active_profitable_rate": 1.0,
                    "reasons": ["insufficient_matured_forward_predictions"],
                },
                "feedback": [],
                "historical_evidence": {
                    "quality_gate": {
                        "direction_accuracy": 0.976,
                        "naive_majority_accuracy": 0.932,
                        "direction_edge": 0.043,
                        "balanced_direction_accuracy": 0.897,
                        "worst_class_recall": 0.808,
                    }
                },
            },
            {"language": "en", "compact_mode": False},
        )

        self.assertIn("Still collecting evidence", message)
        self.assertIn("Matured predictions: 3/8", message)
        self.assertIn("Pending five-day outcomes: 2", message)
        self.assertIn("Latest observation: 2026-07-13 (pending)", message)
        self.assertIn("Earliest estimated maturity: 2026-07-20", message)
        self.assertIn("market holidays or delayed data can move this date", message)
        self.assertIn("97.6% raw vs 93.2% common-result baseline", message)
        self.assertIn("89.7% balanced accuracy", message)
        self.assertIn("80.8% harder-class recall", message)
        self.assertIn("Active signals: 1/5", message)
        self.assertIn("66.7%", message)
        self.assertIn("not guaranteed profit", message)

    def test_shadow_status_translates_internal_gate_reasons_for_chinese_user(self) -> None:
        message = format_benchmark_shadow_message(
            "SPY",
            {
                "summary": {
                    "passed": False,
                    "sample_count": 0,
                    "required_sample_count": 8,
                    "active_signal_count": 0,
                    "required_active_signal_count": 5,
                    "reasons": [
                        "insufficient_matured_forward_predictions",
                        "forward_average_net_return_not_positive",
                    ],
                },
                "feedback": [],
            },
            {"language": "zh", "compact_mode": False},
        )

        self.assertIn("已到期預測", message)
        self.assertIn("仍需要", message)
        self.assertIn("更多已到期預測", message)
        self.assertIn("扣除成本後平均回報為正數", message)
        self.assertNotIn("insufficient_matured", message)

    def test_sync_status_shows_shared_profile_evidence(self) -> None:
        message = format_sync_status_message(
            {
                "linked": True,
                "profile_user_id": "web-user",
                "discord_display_name": "Beginner",
                "watchlist": ["VOO", "AAPL"],
                "account": {"total_equity": 1234.5},
                "recent_trade_count": 2,
                "generated_at_utc": "2026-07-14T02:00:00+00:00",
            }
        )

        self.assertIn("Web/Discord sync: connected", message)
        self.assertIn("web-user", message)
        self.assertIn("VOO, AAPL", message)
        self.assertIn("$1,234.50", message)

    def test_sync_status_unlinked_gives_actionable_instructions(self) -> None:
        message = format_sync_status_message({"linked": False}, "!")
        self.assertIn("not linked", message)
        self.assertIn("!link CODE", message)

    def test_live_status_includes_portfolio_protection_without_crashing(self) -> None:
        message = format_live_virtual_trader_status_message(
            "VOO",
            {
                "account": {
                    "cash": 1000,
                    "holdings_value": 500,
                    "total_equity": 1500,
                    "realized_pnl": 0,
                    "portfolio_risk_level": "critical",
                    "buying_paused": True,
                },
                "latest_decisions": [{
                    "action": "no_action",
                    "reason": "portfolio_drawdown_pause",
                    "confidence_score": None,
                    "metadata": {
                        "model_validation_status": "safety_fallback",
                        "market_regime": {
                            "level": "caution",
                            "position_size_multiplier": 0.5,
                            "new_position_allowed": True,
                            "reasons": ["benchmark_20d_weakness", "elevated_volatility"],
                        },
                        "benchmark_shadow": {
                            "status": "available",
                            "benchmark": "VOO",
                            "signal": "outperform",
                            "outperform_probability": 0.8,
                            "execution_enabled": False,
                        },
                    },
                }],
            },
            {"language": "en", "compact_mode": False},
        )

        self.assertIn("Portfolio protection: New buys paused", message)
        self.assertIn("Model check: Safety fallback; no model passed every quality check", message)
        self.assertIn("Wider-market protection: Cautious market conditions", message)
        self.assertIn("new buys use 50% of normal size", message)
        self.assertIn("wider market was weak", message)
        self.assertNotIn("benchmark_20d_weakness", message)
        self.assertIn("Account losses paused new buying", message)
        self.assertNotIn("portfolio_drawdown_pause", message)
        self.assertIn("signal support, not profit probability", message)
        self.assertIn("Research comparison: may beat VOO over the next five trading days", message)
        self.assertIn("model estimate 80.0%", message)
        self.assertIn("Real-time check: collecting 0/20 completed predictions", message)
        self.assertIn("does not guarantee profit", message)

    def test_why_trade_reads_metadata_explanation_and_validation(self) -> None:
        message = format_trade_reason_message(
            "AAPL",
            {
                "action": "no_action",
                "action_summary": "No action taken.",
                "threshold_summary": "Confidence below threshold.",
                "metadata": {
                    "explanation": "Market evidence was mixed.",
                    "model_validation_status": "safety_fallback",
                    "market_regime": {
                        "level": "stress",
                        "position_size_multiplier": 0.0,
                        "new_position_allowed": False,
                        "reasons": ["ticker_deep_drawdown"],
                    },
                },
            },
            {"language": "en", "compact_mode": False},
        )

        self.assertIn("Market evidence was mixed.", message)
        self.assertIn("Model check: Safety fallback", message)
        self.assertIn("Wider-market protection: High-risk market conditions", message)
        self.assertIn("new buying is paused", message)
        self.assertIn("far below its recent peak", message)
        self.assertNotIn("ticker_deep_drawdown", message)

    def test_live_status_translates_risk_codes_for_chinese_user(self) -> None:
        message = format_live_virtual_trader_status_message(
            "SPY",
            {
                "account": {"portfolio_risk_level": "normal"},
                "latest_decisions": [{
                    "action": "no_action",
                    "reason": "market_data_quality_block",
                    "confidence_score": 0.72,
                    "metadata": {
                        "model_validation_status": "validated",
                        "market_regime": {
                            "level": "stress",
                            "position_size_multiplier": 0,
                            "new_position_allowed": False,
                            "reasons": ["extreme_volatility"],
                        },
                        "benchmark_shadow": {
                            "status": "available",
                            "benchmark": "VOO",
                            "signal": "not_outperform",
                            "outperform_probability": 0.3,
                            "forward_evidence": {
                                "sample_count": 0,
                                "minimum_samples_for_promotion": 8,
                            },
                        },
                    },
                }],
            },
            {"language": "zh", "compact_mode": False},
        )

        self.assertIn("市場資料不可靠，因此阻止買入", message)
        self.assertIn("高風險市場狀況", message)
        self.assertIn("暫停新買入", message)
        self.assertIn("價格波動極為劇烈", message)
        self.assertIn("訊號支持程度，並非獲利機率", message)
        self.assertIn("研究比較", message)
        self.assertIn("預期未能在未來五個交易日跑贏 VOO", message)
        self.assertIn("正在收集已完成預測：0/8", message)
        self.assertIn("不保證獲利", message)
        self.assertNotIn("Research comparison", message)
        self.assertNotIn("market_data_quality_block", message)
        self.assertNotIn("extreme_volatility", message)

    def test_model_accuracy_explains_calibrated_no_action_evidence(self) -> None:
        message = format_model_accuracy_message(
            "GLD",
            {
                "metrics_summary": {
                    "task_type": "regression",
                    "validation_scheme_version": 4,
                    "stationary_features": True,
                    "pooled_training": True,
                    "training_tickers": ["AAPL", "MSFT", "VOO"],
                    "outperformance_economics_gate": {
                        "passed": False,
                        "average_net_stock_return_pct": -0.14,
                        "profitable_non_overlapping_path_rate": 0.4,
                    },
                    "metrics": {"mae": 1.2, "rmse": 1.8, "direction_accuracy": 0.56},
                },
                "rolling_accuracy": [],
            },
            {"language": "en", "compact_mode": False},
        )

        self.assertIn("uncertainty and market-regime checks can convert a prediction to no action", message)
        self.assertIn("3 tickers; scale-independent inputs", message)
        self.assertIn("Feature safety: scale-independent return inputs", message)
        self.assertIn("Profit check after costs: Failed", message)
        self.assertIn("Average net return per signal: -0.14%", message)
        self.assertIn("Accuracy does not mean profit", message)


if __name__ == "__main__":
    unittest.main()
