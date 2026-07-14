"""Contracts for Discord mutations of the shared virtual account."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bot import stock_api_client


class BotStockApiClientTests(unittest.TestCase):
    @patch("bot.stock_api_client.requests.get")
    def test_shadow_status_reads_shared_backend_evidence(self, mock_get) -> None:
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {"summary": {"sample_count": 0}, "feedback": []},
        )

        result = stock_api_client.benchmark_shadow_feedback("SPY")

        self.assertEqual(result["summary"]["sample_count"], 0)
        url = mock_get.call_args.args[0]
        self.assertIn("/model-lifecycle/benchmark-shadow-feedback", url)
        self.assertIn("ticker=SPY", url)
        self.assertIn("model_period=10y", url)

    @patch("bot.stock_api_client.requests.post")
    def test_deposit_is_attributed_to_discord(self, mock_post) -> None:
        mock_post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {"cash": 1000.0},
        )

        result = stock_api_client.virtual_account_deposit("web-user", 1000.0)

        self.assertEqual(result["cash"], 1000.0)
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {"user_id": "web-user", "amount": 1000.0, "source": "discord"},
        )

    @patch("bot.stock_api_client.requests.post")
    def test_monthly_contribution_uses_shared_endpoint(self, mock_post) -> None:
        mock_post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {"amount": 250.0, "effective_from_month": "2026-08"},
        )

        stock_api_client.virtual_account_set_monthly_contribution("web-user", 250.0)

        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/virtual-account/monthly-contribution-input"))
        self.assertEqual(kwargs["json"]["source"], "discord")


if __name__ == "__main__":
    unittest.main()
