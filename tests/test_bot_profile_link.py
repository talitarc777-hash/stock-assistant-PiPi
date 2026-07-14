"""Tests that Discord commands operate on the linked dashboard profile."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from bot import main as bot_main


class BotProfileLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = SimpleNamespace(author=SimpleNamespace(id=12345))

    @patch("bot.main.resolve_discord_profile")
    def test_commands_use_linked_web_profile(self, mock_resolve) -> None:
        mock_resolve.return_value = {
            "linked": True,
            "discord_user_id": "12345",
            "profile_user_id": "web-beginner",
        }

        self.assertEqual(bot_main._discord_user_id(self.ctx), "web-beginner")
        self.assertEqual(bot_main._discord_user_id(self.ctx), "web-beginner")
        self.assertEqual(mock_resolve.call_count, 2)
        mock_resolve.assert_called_with("12345")

    @patch("bot.main.resolve_discord_profile")
    def test_backend_outage_does_not_fall_back_to_unsynchronized_profile(self, mock_resolve) -> None:
        mock_resolve.side_effect = RuntimeError("backend unavailable")
        with self.assertRaisesRegex(RuntimeError, "backend unavailable"):
            bot_main._discord_user_id(self.ctx)

    @patch("bot.main.fetch_user_profile")
    @patch("bot.main.resolve_discord_profile")
    def test_settings_outage_does_not_use_local_settings(
        self,
        mock_resolve,
        mock_fetch_profile,
    ) -> None:
        mock_resolve.return_value = {"linked": True, "profile_user_id": "web-beginner"}
        mock_fetch_profile.side_effect = RuntimeError("settings backend unavailable")

        with self.assertRaisesRegex(RuntimeError, "settings backend unavailable"):
            bot_main._get_shared_user_settings(self.ctx)

    @patch("bot.main.fetch_user_watchlist")
    @patch("bot.main.resolve_discord_profile")
    def test_watchlist_outage_does_not_use_local_watchlist(
        self,
        mock_resolve,
        mock_fetch_watchlist,
    ) -> None:
        mock_resolve.return_value = {"linked": True, "profile_user_id": "web-beginner"}
        mock_fetch_watchlist.side_effect = RuntimeError("watchlist backend unavailable")

        with self.assertRaisesRegex(RuntimeError, "watchlist backend unavailable"):
            bot_main._get_shared_effective_watchlist(self.ctx)

    @patch("bot.main.backend_scan_user_alerts")
    @patch("bot.main._get_shared_effective_watchlist", return_value=["SPY"])
    @patch(
        "bot.main._get_shared_user_settings",
        return_value={"language": "en", "compact_mode": False},
    )
    @patch("bot.main.resolve_discord_profile")
    def test_alert_outage_does_not_calculate_unsynchronized_local_alerts(
        self,
        mock_resolve,
        _mock_settings,
        _mock_watchlist,
        mock_scan,
    ) -> None:
        self.ctx.send = AsyncMock()
        mock_resolve.return_value = {"linked": True, "profile_user_id": "web-beginner"}
        mock_scan.side_effect = RuntimeError("alert backend unavailable")

        import asyncio
        with self.assertRaisesRegex(RuntimeError, "alert backend unavailable"):
            asyncio.run(bot_main._send_alerts(self.ctx))

        self.ctx.send.assert_not_awaited()

    @patch("bot.main.resolve_discord_profile")
    def test_read_path_observes_unlink_on_the_next_command(self, mock_resolve) -> None:
        mock_resolve.side_effect = [
            {"linked": True, "profile_user_id": "web-beginner"},
            {"linked": False, "profile_user_id": "12345"},
        ]

        self.assertEqual(bot_main._discord_user_id(self.ctx), "web-beginner")
        self.assertEqual(bot_main._discord_user_id(self.ctx), "12345")
        self.assertEqual(mock_resolve.call_count, 2)

    @patch("bot.main.virtual_trader_live_sync")
    @patch("bot.main.resolve_discord_profile")
    def test_sync_status_reads_all_state_from_linked_web_profile(
        self,
        mock_resolve,
        mock_live_sync,
    ) -> None:
        self.ctx.author.display_name = "Beginner"
        self.ctx.send = AsyncMock()
        mock_resolve.return_value = {
            "linked": True,
            "discord_user_id": "12345",
            "profile_user_id": "web-beginner",
        }
        mock_live_sync.return_value = {
            "watchlist": ["VOO", "AAPL"],
            "status": {
                "generated_at_utc": "2026-07-14T02:00:00+00:00",
                "account": {"total_equity": 1500.0},
            },
            "recent_trades": [{"ticker": "VOO"}],
        }

        import asyncio
        asyncio.run(bot_main._send_sync_status(self.ctx))

        mock_live_sync.assert_called_once_with("web-beginner")
        sent_message = self.ctx.send.await_args.args[0]
        self.assertIn("Web/Discord sync: connected", sent_message)
        self.assertIn("VOO, AAPL", sent_message)
        self.assertIn("$1,500.00", sent_message)

    @patch("bot.main._get_shared_user_settings", return_value={"language": "en", "compact_mode": False})
    @patch("bot.main.virtual_account_deposit")
    @patch("bot.main.resolve_discord_profile")
    def test_discord_deposit_changes_linked_web_account(
        self,
        mock_resolve,
        mock_deposit,
        _mock_settings,
    ) -> None:
        self.ctx.send = AsyncMock()
        mock_resolve.return_value = {
            "linked": True,
            "profile_user_id": "web-beginner",
        }
        mock_deposit.return_value = {
            "cash": 1500,
            "holdings_value": 0,
            "total_account_value": 1500,
            "net_deposits": 1500,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "holdings": [],
        }

        import asyncio
        asyncio.run(bot_main._change_virtual_cash(self.ctx, 500, "deposit"))

        mock_deposit.assert_called_once_with("web-beginner", 500.0)
        self.assertIn("Deposits are not profit", self.ctx.send.await_args.args[0])

    @patch("bot.main.resolve_discord_profile", return_value={"linked": False})
    def test_discord_cash_change_requires_web_link(self, _mock_resolve) -> None:
        self.ctx.send = AsyncMock()
        import asyncio
        with self.assertRaisesRegex(ValueError, "Link the web profile first"):
            asyncio.run(bot_main._change_virtual_cash(self.ctx, 500, "deposit"))

    @patch("bot.main.resolve_discord_profile", return_value={"linked": False})
    def test_unlink_immediately_blocks_profile_mutations(
        self,
        _mock_resolve,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "Link the web profile first"):
            bot_main._set_shared_language(self.ctx, "en")

    @patch("bot.main.update_user_profile_settings")
    @patch("bot.main.resolve_discord_profile")
    def test_relink_routes_settings_write_to_new_authoritative_profile(
        self,
        mock_resolve,
        mock_update,
    ) -> None:
        mock_resolve.return_value = {
            "linked": True,
            "profile_user_id": "new-web-profile",
        }
        mock_update.return_value = {
            "preferred_language": "en",
            "compact_mode": False,
            "default_watchlist": ["VOO"],
        }

        bot_main._set_shared_language(self.ctx, "en")

        self.assertEqual(
            mock_update.call_args.args[0]["user_id"],
            "new-web-profile",
        )

    @patch("bot.main.virtual_trader_run_now")
    @patch("bot.main.resolve_discord_profile", return_value={"linked": False})
    def test_unlink_blocks_run_trader_mutation(
        self,
        _mock_resolve,
        mock_run,
    ) -> None:
        self.ctx.send = AsyncMock()
        import asyncio
        with self.assertRaisesRegex(ValueError, "Link the web profile first"):
            asyncio.run(bot_main._run_virtual_trader_now(self.ctx, "SPY"))

        mock_run.assert_not_called()

    @patch("bot.main.format_live_virtual_trader_status_message", return_value="updated")
    @patch("bot.main._get_shared_user_settings", return_value={"language": "en", "compact_mode": False})
    @patch("bot.main.virtual_trader_run_now", return_value={})
    @patch("bot.main.resolve_discord_profile")
    def test_relink_routes_run_trader_to_new_authoritative_profile(
        self,
        mock_resolve,
        mock_run,
        _mock_settings,
        _mock_formatter,
    ) -> None:
        self.ctx.send = AsyncMock()
        mock_resolve.return_value = {
            "linked": True,
            "profile_user_id": "new-web-profile",
        }

        import asyncio
        asyncio.run(bot_main._run_virtual_trader_now(self.ctx, "SPY"))

        mock_run.assert_called_once_with(
            user_id="new-web-profile",
            tickers=["SPY"],
        )
        self.ctx.send.assert_awaited_once_with("updated")

    @patch("bot.main.virtual_account_set_monthly_contribution")
    @patch("bot.main.resolve_discord_profile")
    def test_monthly_contribution_updates_linked_profile(
        self,
        mock_resolve,
        mock_monthly,
    ) -> None:
        self.ctx.send = AsyncMock()
        mock_resolve.return_value = {
            "linked": True,
            "profile_user_id": "web-beginner",
        }
        mock_monthly.return_value = {
            "amount": 250,
            "effective_from_month": "2026-08",
        }

        import asyncio
        asyncio.run(bot_main._set_monthly_virtual_cash(self.ctx, 250))

        mock_monthly.assert_called_once_with("web-beginner", 250.0)
        self.assertIn("$250.00", self.ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
