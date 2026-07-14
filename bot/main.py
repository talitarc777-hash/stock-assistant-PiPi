import discord
from discord.ext import commands
import math

try:
    from .config import (
        ALLOWED_CHANNEL_IDS,
        COMMAND_PREFIX,
        DISCORD_BOT_TOKEN,
    )
    from .formatter import (
        format_analyze_message,
        format_alerts_message,
        format_forecast_message,
        format_help_message,
        format_model_accuracy_message,
        format_benchmark_shadow_message,
        format_model_status_message,
        format_live_virtual_trader_status_message,
        format_live_virtual_trader_trades_message,
        format_settings_message,
        format_sync_status_message,
        format_trader_scheduler_status_message,
        format_trade_reason_message,
        format_virtual_account_ledger_message,
        format_virtual_account_summary_message,
        format_virtual_trader_compare_message,
        format_virtual_trader_summary_message,
        format_virtual_trader_trades_message,
        format_watchlist_message,
    )
    from .nlp_router import parse_natural_language_message
    from .profile_client import (
        add_user_watchlist_ticker as backend_add_user_watchlist_ticker,
        consume_discord_link,
        fetch_user_profile,
        fetch_user_watchlist,
        remove_user_watchlist_ticker as backend_remove_user_watchlist_ticker,
        reset_user_profile as backend_reset_user_profile,
        resolve_discord_profile,
        scan_user_alerts as backend_scan_user_alerts,
        update_user_profile_settings,
    )
    from .settings_store import (
        parse_watchlist_input,
    )
    from .stock_api_client import (
        ApiClientError,
        BackendTimeoutError,
        BackendUnavailableError,
        InvalidTickerApiError,
        analyze,
        benchmark_shadow_feedback,
        forecast,
        model_accuracy,
        model_latest,
        virtual_account_ledger,
        virtual_account_deposit,
        virtual_account_set_monthly_contribution,
        virtual_account_summary,
        virtual_account_withdraw,
        virtual_trader_live_status,
        virtual_trader_live_sync,
        virtual_trader_live_trades,
        virtual_trader_run_now,
        virtual_trader_summary,
        virtual_trader_trades,
        watchlist,
        trader_scheduler_status,
    )
except ImportError:  # pragma: no cover - script execution fallback
    from config import (
        ALLOWED_CHANNEL_IDS,
        COMMAND_PREFIX,
        DISCORD_BOT_TOKEN,
    )
    from formatter import (
        format_analyze_message,
        format_alerts_message,
        format_forecast_message,
        format_help_message,
        format_model_accuracy_message,
        format_benchmark_shadow_message,
        format_model_status_message,
        format_live_virtual_trader_status_message,
        format_live_virtual_trader_trades_message,
        format_settings_message,
        format_sync_status_message,
        format_trader_scheduler_status_message,
        format_trade_reason_message,
        format_virtual_account_ledger_message,
        format_virtual_account_summary_message,
        format_virtual_trader_compare_message,
        format_virtual_trader_summary_message,
        format_virtual_trader_trades_message,
        format_watchlist_message,
    )
    from nlp_router import parse_natural_language_message
    from profile_client import (
        add_user_watchlist_ticker as backend_add_user_watchlist_ticker,
        consume_discord_link,
        fetch_user_profile,
        fetch_user_watchlist,
        remove_user_watchlist_ticker as backend_remove_user_watchlist_ticker,
        reset_user_profile as backend_reset_user_profile,
        resolve_discord_profile,
        scan_user_alerts as backend_scan_user_alerts,
        update_user_profile_settings,
    )
    from settings_store import (
        parse_watchlist_input,
    )
    from stock_api_client import (
        ApiClientError,
        BackendTimeoutError,
        BackendUnavailableError,
        InvalidTickerApiError,
        analyze,
        benchmark_shadow_feedback,
        forecast,
        model_accuracy,
        model_latest,
        virtual_account_ledger,
        virtual_account_deposit,
        virtual_account_set_monthly_contribution,
        virtual_account_summary,
        virtual_account_withdraw,
        virtual_trader_live_status,
        virtual_trader_live_sync,
        virtual_trader_live_trades,
        virtual_trader_run_now,
        virtual_trader_summary,
        virtual_trader_trades,
        watchlist,
        trader_scheduler_status,
    )

intents = discord.Intents.default()
intents.message_content = True

# Disable discord.py's default help so we can provide a beginner-friendly version.
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


def is_allowed(ctx) -> bool:
    """Allow command in configured channels only (or all if not configured)."""
    if not ALLOWED_CHANNEL_IDS:
        return True
    return ctx.channel.id in ALLOWED_CHANNEL_IDS


def _friendly_error_message(exc: Exception) -> str:
    """Convert known exceptions into short, friendly chat messages."""
    if isinstance(exc, InvalidTickerApiError):
        return "I couldn't recognise that ticker. Please check the symbol and try again, for example `!analyze VOO`."
    if isinstance(exc, BackendTimeoutError):
        return "The backend is taking a little longer than usual to reply. Please try again in a moment."
    if isinstance(exc, BackendUnavailableError):
        return "The backend isn't available right now. Please try again a little later."
    if isinstance(exc, ValueError):
        message = str(exc)
        if "Language must be one of" in message:
            return "That language option isn't valid. Please use `en`, `zh`, or `bilingual`."
        if "Ticker cannot be empty" in message:
            return "Please enter a ticker. Try something like `!addticker MSFT`."
        if "Watchlist cannot be empty" in message:
            return "Your watchlist is empty right now. Try `!setwatchlist VOO,QQQ,AAPL`."
        if "Unexpected API shape" in message:
            return "The API reply was missing some expected fields. Please try again later."
        return message
    if isinstance(exc, ApiClientError):
        detail = str(exc)
        detail_lower = detail.lower()
        if "already running" in detail_lower:
            return "Trader is already running a cycle now. Please try again in a moment."
        if "saved model artifacts were not found" in detail_lower or "run the training command first" in detail_lower:
            return "I couldn't find saved model results for that ticker yet. Run the training step first, then try again."
        if "saved virtual trader artifacts were not found" in detail_lower or "run the virtual trader command first" in detail_lower:
            return "I couldn't find saved virtual trader results for that ticker yet. Run the virtual trader step first, then try again."
        return f"API error: {str(exc)}"
    return "Something went wrong while talking to the backend. Please try again."


def _require_dict(data, field_name: str) -> dict:
    """Ensure a field is a dictionary for predictable parsing."""
    if isinstance(data, dict):
        return data
    raise ValueError(f"Unexpected API shape: '{field_name}' should be an object.")


def _require_list(data, field_name: str) -> list:
    """Ensure a field is a list for predictable parsing."""
    if isinstance(data, list):
        return data
    raise ValueError(f"Unexpected API shape: '{field_name}' should be a list.")


def _language_label(language: str) -> str:
    """Render a short human-friendly language label."""
    labels = {
        "en": "English",
        "zh": "中文",
        "bilingual": "English + 中文",
    }
    return labels.get(str(language).lower(), str(language))


def _discord_user_id(ctx) -> str:
    """Resolve the current dashboard link for every Discord command."""
    discord_user_id = str(ctx.author.id)
    resolved = resolve_discord_profile(discord_user_id)
    return str(resolved.get("profile_user_id") or discord_user_id)


def _require_linked_profile_id(ctx) -> str:
    """Resolve the authoritative web link before any Discord mutation.

    Read-only commands may use the short cache, but writes always re-check the
    backend so an unlink or relink takes effect immediately.
    """
    discord_user_id = str(ctx.author.id)
    resolved = resolve_discord_profile(discord_user_id)
    if not resolved.get("linked"):
        raise ValueError(
            f"Link the web profile first with `{COMMAND_PREFIX}link CODE`, then retry."
        )
    profile_user_id = str(resolved.get("profile_user_id") or ctx.author.id)
    return profile_user_id


def _discord_display_name(ctx) -> str:
    """Return the best display name available from Discord context."""
    return getattr(ctx.author, "display_name", None) or getattr(ctx.author, "name", "")


def _normalize_profile_settings(profile: dict) -> dict:
    """Shape backend profile payloads into the existing formatter-friendly settings shape."""
    return {
        "language": profile.get("preferred_language", "zh"),
        "compact_mode": bool(profile.get("compact_mode", False)),
        "default_watchlist": list(profile.get("default_watchlist", [])),
        "alert_enabled": bool(profile.get("alert_enabled", True)),
        "alert_threshold_high": profile.get("alert_threshold_high", 80),
        "alert_threshold_low": profile.get("alert_threshold_low", 45),
        "alert_watchlist": list(profile.get("alert_watchlist", [])),
        "preferred_delivery_source": profile.get("preferred_delivery_source", "discord"),
    }


def _get_shared_user_settings(ctx) -> dict:
    """Read settings only from the shared backend profile."""
    user_id = _discord_user_id(ctx)
    display_name = _discord_display_name(ctx)
    profile = fetch_user_profile(user_id=user_id, display_name=display_name, source="discord")
    return _normalize_profile_settings(profile)


def _set_shared_language(ctx, language: str) -> dict:
    """Persist language to the currently linked web profile."""
    payload = {
        "user_id": _require_linked_profile_id(ctx),
        "display_name": _discord_display_name(ctx),
        "preferred_language": language,
        "last_active_source": "discord",
    }
    profile = update_user_profile_settings(payload)
    return _normalize_profile_settings(profile)


def _set_shared_compact_mode(ctx, compact_mode: bool) -> dict:
    """Persist compact mode to the currently linked web profile."""
    payload = {
        "user_id": _require_linked_profile_id(ctx),
        "display_name": _discord_display_name(ctx),
        "compact_mode": compact_mode,
        "last_active_source": "discord",
    }
    profile = update_user_profile_settings(payload)
    return _normalize_profile_settings(profile)


def _set_shared_watchlist(ctx, tickers: list[str]) -> dict:
    """Persist the full watchlist to the currently linked web profile."""
    payload = {
        "user_id": _require_linked_profile_id(ctx),
        "display_name": _discord_display_name(ctx),
        "default_watchlist": tickers,
        "last_active_source": "discord",
    }
    profile = update_user_profile_settings(payload)
    return _normalize_profile_settings(profile)


def _merge_watchlist_response_with_settings(ctx, response: dict) -> dict:
    """Keep add/remove watchlist results aligned with the shared settings shape."""
    current_settings = _get_shared_user_settings(ctx)
    return {
        **current_settings,
        "default_watchlist": list(
            response.get("watchlist", current_settings.get("default_watchlist", []))
        ),
    }


def _add_shared_watchlist_ticker(ctx, ticker: str) -> dict:
    """Add one ticker to the currently linked web profile."""
    payload = {
        "user_id": _require_linked_profile_id(ctx),
        "display_name": _discord_display_name(ctx),
        "ticker": ticker,
        "last_active_source": "discord",
    }
    response = backend_add_user_watchlist_ticker(payload)
    return _merge_watchlist_response_with_settings(ctx, response)


def _remove_shared_watchlist_ticker(ctx, ticker: str) -> dict:
    """Remove one ticker from the currently linked web profile."""
    payload = {
        "user_id": _require_linked_profile_id(ctx),
        "display_name": _discord_display_name(ctx),
        "ticker": ticker,
        "last_active_source": "discord",
    }
    response = backend_remove_user_watchlist_ticker(payload)
    return _merge_watchlist_response_with_settings(ctx, response)


def _get_shared_effective_watchlist(ctx) -> list[str]:
    """Read the effective watchlist only from the shared backend profile."""
    response = fetch_user_watchlist(_discord_user_id(ctx))
    return list(response.get("watchlist", []))


def _resolve_reporting_ticker(ctx, requested_ticker: str | None = None) -> str:
    """Choose a ticker for model/trader reporting.

    These report commands are useful even without an explicit ticker.
    In that case, we fall back to the user's first watchlist symbol,
    then to VOO as a safe system default.
    """
    if requested_ticker:
        return requested_ticker.strip().upper()

    watchlist = _get_shared_effective_watchlist(ctx)
    if watchlist:
        return str(watchlist[0]).upper()
    return "VOO"


def _reset_shared_settings(ctx) -> dict:
    """Reset Discord-visible settings via the shared backend profile."""
    payload = {
        "user_id": _require_linked_profile_id(ctx),
        "display_name": _discord_display_name(ctx),
        "last_active_source": "discord",
    }
    profile = backend_reset_user_profile(payload)
    return _normalize_profile_settings(profile)


async def _send_settings(ctx) -> None:
    """Send the current user's saved settings."""
    user_settings = _get_shared_user_settings(ctx)
    print(f"SETTINGS user={ctx.author.id} settings={user_settings}")
    await ctx.send(format_settings_message(ctx.author.id, user_settings))


async def _send_sync_status(ctx) -> None:
    """Verify that Discord can read the linked web profile's shared state."""
    discord_user_id = str(ctx.author.id)
    resolved = resolve_discord_profile(discord_user_id)
    if not resolved.get("linked"):
        await ctx.send(format_sync_status_message(resolved, COMMAND_PREFIX))
        return
    profile_user_id = str(resolved.get("profile_user_id") or discord_user_id)
    sync_payload = virtual_trader_live_sync(profile_user_id)
    status = sync_payload.get("status") if isinstance(sync_payload, dict) else {}
    account = status.get("account") if isinstance(status, dict) else {}
    recent_trades = sync_payload.get("recent_trades") if isinstance(sync_payload, dict) else []
    await ctx.send(
        format_sync_status_message(
            {
                **resolved,
                "discord_display_name": resolved.get("discord_display_name")
                or _discord_display_name(ctx),
                "profile_user_id": profile_user_id,
                "watchlist": sync_payload.get("watchlist") or [],
                "account": account,
                "recent_trade_count": len(recent_trades or []),
                "generated_at_utc": status.get("generated_at_utc")
                if isinstance(status, dict)
                else None,
            },
            COMMAND_PREFIX,
        )
    )


async def _apply_language_setting(ctx, language: str) -> None:
    """Save language preference and send a friendly confirmation."""
    user_settings = _set_shared_language(ctx, language)
    print(f"SETLANG user={ctx.author.id} language={user_settings['language']}")
    await ctx.send(
        f"Saved. I'll reply in {_language_label(user_settings['language'])} from now on.\n"
        f"Use `{COMMAND_PREFIX}settings` any time to review your setup."
    )


async def _apply_compact_setting(ctx, compact_mode: bool) -> None:
    """Save compact mode preference and send a friendly confirmation."""
    user_settings = _set_shared_compact_mode(ctx, compact_mode)
    print(f"SETCOMPACT user={ctx.author.id} compact_mode={user_settings['compact_mode']}")
    mode_text = "on" if user_settings["compact_mode"] else "off"
    extra = "I'll keep replies shorter." if user_settings["compact_mode"] else "I'll include a bit more detail."
    await ctx.send(
        f"Saved. Compact mode is now `{mode_text}`.\n"
        f"{extra}"
    )


async def _apply_watchlist_update(ctx, tickers: list[str], action: str) -> None:
    """Add or remove one or more tickers from the user's watchlist."""
    if action == "add":
        for ticker in tickers:
            updated = _add_shared_watchlist_ticker(ctx, ticker)
        print(f"ADDTICKER user={ctx.author.id} watchlist={updated['default_watchlist']}")
        added_text = ", ".join(tickers)
        watchlist_text = ", ".join(updated["default_watchlist"])
        await ctx.send(
            f"Added `{added_text}` to your watchlist.\n"
            f"Current watchlist: `{watchlist_text}`"
        )
        return

    for ticker in tickers:
        updated = _remove_shared_watchlist_ticker(ctx, ticker)
    print(f"REMOVETICKER user={ctx.author.id} watchlist={updated['default_watchlist']}")
    removed_text = ", ".join(tickers)
    watchlist_text = ", ".join(updated["default_watchlist"])
    await ctx.send(
        f"Removed `{removed_text}` from your watchlist.\n"
        f"Current watchlist: `{watchlist_text}`"
    )


async def _send_analyze(ctx, symbol: str) -> None:
    """Fetch and send an analysis reply for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    data = analyze(symbol)
    print("ANALYZE RAW RESPONSE:", data)
    data = _require_dict(data, "analyze response")
    _require_dict(data.get("score_breakdown", {}), "score_breakdown")
    await ctx.send(format_analyze_message(symbol, data, user_settings))


async def _send_forecast(ctx, symbol: str) -> None:
    """Fetch and send a forecast reply for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    data = forecast(symbol, period="2y")
    print("FORECAST RAW RESPONSE:", data)
    data = _require_dict(data, "forecast response")
    _require_dict(data.get("expected_range", {}), "expected_range")
    _require_dict(data.get("levels", {}), "levels")
    await ctx.send(format_forecast_message(symbol, data, user_settings))


async def _send_watchlist(ctx) -> None:
    """Fetch and send ranked watchlist results."""
    user_settings = _get_shared_user_settings(ctx)
    effective_watchlist = _get_shared_effective_watchlist(ctx)
    if not effective_watchlist:
        raise ValueError("Watchlist cannot be empty. Add one with `!setwatchlist VOO,QQQ,AAPL`.")

    data = watchlist(",".join(effective_watchlist), period="5y")
    print("WATCHLIST RAW RESPONSE:", data)
    data = _require_dict(data, "watchlist response")
    ranked = _require_list(data.get("ranked_results", []), "ranked_results")
    failed = _require_list(data.get("failed_tickers", []), "failed_tickers")
    await ctx.send(format_watchlist_message(ranked, failed, effective_watchlist, user_settings))


async def _send_model_status(ctx, requested_ticker: str | None = None) -> None:
    """Fetch and send the latest saved model status for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = model_latest(symbol, period="5y")
    print("MODEL STATUS RAW RESPONSE:", data)
    data = _require_dict(data, "model status response")
    _require_dict(data.get("latest_prediction", {}), "latest_prediction")
    await ctx.send(format_model_status_message(symbol, data, user_settings))


async def _send_model_accuracy(ctx, requested_ticker: str | None = None) -> None:
    """Fetch and send saved model accuracy metrics for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = model_accuracy(symbol, period="5y")
    print("MODEL ACCURACY RAW RESPONSE:", data)
    data = _require_dict(data, "model accuracy response")
    _require_dict(data.get("metrics_summary", {}), "metrics_summary")
    _require_list(data.get("rolling_accuracy", []), "rolling_accuracy")
    await ctx.send(format_model_accuracy_message(symbol, data, user_settings))


async def _send_benchmark_shadow(ctx, requested_ticker: str | None = None) -> None:
    """Send forward promotion evidence from the shared backend ledger."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = benchmark_shadow_feedback(symbol, period="10y", limit=20)
    data = _require_dict(data, "benchmark shadow response")
    _require_dict(data.get("summary", {}), "benchmark shadow summary")
    _require_list(data.get("feedback", []), "benchmark shadow feedback")
    await ctx.send(format_benchmark_shadow_message(symbol, data, user_settings))


async def _send_virtual_trader_summary(ctx, requested_ticker: str | None = None) -> None:
    """Fetch and send live virtual trader status for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = virtual_trader_live_status(user_id=_discord_user_id(ctx), ticker=symbol)
    print("LIVE VIRTUAL TRADER STATUS RAW RESPONSE:", data)
    data = _require_dict(data, "live virtual trader status response")
    _require_dict(data.get("account", {}), "account")
    await ctx.send(format_live_virtual_trader_status_message(symbol, data, user_settings))


async def _send_virtual_trader_trades(ctx, requested_ticker: str | None = None, limit: int = 5) -> None:
    """Fetch and send recent live virtual trader trades for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = virtual_trader_live_trades(user_id=_discord_user_id(ctx), ticker=symbol, limit=max(limit, 5))
    print("LIVE VIRTUAL TRADER TRADES RAW RESPONSE:", data)
    data = _require_dict(data, "live virtual trader trades response")
    _require_list(data.get("trades", []), "trades")
    await ctx.send(format_live_virtual_trader_trades_message(symbol, data, user_settings, limit=limit))


async def _send_why_trade(ctx, requested_ticker: str | None = None) -> None:
    """Fetch and explain the latest live virtual trader action for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = virtual_trader_live_trades(user_id=_discord_user_id(ctx), ticker=symbol, limit=5)
    print("WHY TRADE LIVE RAW RESPONSE:", data)
    data = _require_dict(data, "live virtual trader trades response")
    trades = _require_list(data.get("trades", []), "trades")
    if not trades:
        await ctx.send(f"No saved virtual trades yet for `{symbol}`.")
        return
    await ctx.send(format_trade_reason_message(symbol, trades[0], user_settings))


async def _run_virtual_trader_now(ctx, requested_ticker: str | None = None) -> None:
    """Trigger one live simulation cycle now and return updated status."""
    user_id = _require_linked_profile_id(ctx)
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = virtual_trader_run_now(
        user_id=user_id,
        tickers=[symbol],
    )
    print("RUN VIRTUAL TRADER NOW RAW RESPONSE:", data)
    data = _require_dict(data, "live virtual trader run response")
    await ctx.send(format_live_virtual_trader_status_message(symbol, data, user_settings))


async def _send_virtual_trader_compare(ctx, requested_ticker: str | None = None) -> None:
    """Fetch and compare the virtual trader against VOO for one ticker."""
    user_settings = _get_shared_user_settings(ctx)
    symbol = _resolve_reporting_ticker(ctx, requested_ticker)
    data = virtual_trader_summary(symbol, period="5y")
    print("VIRTUAL TRADER COMPARE RAW RESPONSE:", data)
    data = _require_dict(data, "virtual trader summary response")
    _require_dict(data.get("summary", {}), "summary")
    _require_dict(data.get("benchmark_comparison", {}), "benchmark_comparison")
    await ctx.send(format_virtual_trader_compare_message(symbol, data, user_settings))


async def _send_virtual_account_summary(ctx) -> None:
    """Fetch and send immutable virtual account summary."""
    user_settings = _get_shared_user_settings(ctx)
    data = virtual_account_summary(user_id=_discord_user_id(ctx))
    data = _require_dict(data, "virtual account summary response")
    await ctx.send(format_virtual_account_summary_message(data, user_settings))


async def _send_virtual_account_ledger(ctx, limit: int = 10) -> None:
    """Fetch and send recent immutable ledger events."""
    user_settings = _get_shared_user_settings(ctx)
    data = virtual_account_ledger(user_id=_discord_user_id(ctx), limit=max(limit, 1))
    data = _require_dict(data, "virtual account ledger response")
    await ctx.send(format_virtual_account_ledger_message(data, user_settings, limit=limit))


async def _change_virtual_cash(ctx, amount: float, action: str) -> None:
    """Apply one simulation-only cash change through the shared ledger."""
    numeric_amount = float(amount)
    if not math.isfinite(numeric_amount) or numeric_amount <= 0:
        raise ValueError("Amount must be a positive number, for example `!deposit 1000`.")
    user_id = _require_linked_profile_id(ctx)
    if action == "deposit":
        data = virtual_account_deposit(user_id, numeric_amount)
        heading = f"Simulation deposit added: ${numeric_amount:,.2f}. Deposits are not profit."
    elif action == "withdraw":
        data = virtual_account_withdraw(user_id, numeric_amount)
        heading = f"Simulation withdrawal added: ${numeric_amount:,.2f}."
    else:
        raise ValueError("Unsupported virtual cash action.")
    data = _require_dict(data, "virtual account summary response")
    user_settings = _get_shared_user_settings(ctx)
    await ctx.send(
        heading + "\n\n" + format_virtual_account_summary_message(data, user_settings)
    )


async def _set_monthly_virtual_cash(ctx, amount: float) -> None:
    """Set the recurring monthly simulation contribution shared with the web."""
    numeric_amount = float(amount)
    if not math.isfinite(numeric_amount) or numeric_amount < 0:
        raise ValueError("Monthly amount must be zero or more, for example `!setmonthly 500`.")
    data = virtual_account_set_monthly_contribution(
        _require_linked_profile_id(ctx),
        numeric_amount,
    )
    data = _require_dict(data, "monthly contribution response")
    await ctx.send(
        f"Shared monthly simulation contribution: ${float(data.get('amount', 0)):,.2f}\n"
        f"Effective from: {data.get('effective_from_month', 'next applicable month')}\n"
        "The web dashboard will show the same setting. Use zero to turn it off."
    )


async def _send_alerts(ctx) -> None:
    """Fetch and send current alert messages for the effective watchlist."""
    user_settings = _get_shared_user_settings(ctx)
    effective_watchlist = _get_shared_effective_watchlist(ctx)
    if not effective_watchlist:
        raise ValueError("Watchlist cannot be empty. Add one with `!setwatchlist VOO,QQQ,AAPL`.")

    alert_payload = backend_scan_user_alerts(_discord_user_id(ctx))
    print("ALERTS PROFILE RAW RESPONSE:", alert_payload)
    alert_lines = []
    for item in alert_payload.get("alerts", []):
        message = item.get("message_zh", "")
        if str(user_settings.get("language")) == "en":
            message = item.get("message_en", message)
        elif str(user_settings.get("language")) == "bilingual":
            message = f"{item.get('message_en', '')} / {item.get('message_zh', '')}"
        icon = "🚨" if item.get("severity") == "high" else "⚠️"
        alert_lines.append(f"{icon} {message}")
    await ctx.send(format_alerts_message(alert_lines, user_settings))


async def _send_trader_scheduler_status(ctx) -> None:
    """Fetch and send scheduler runtime status."""
    user_settings = _get_shared_user_settings(ctx)
    data = trader_scheduler_status(log_limit=8)
    data = _require_dict(data, "trader scheduler status response")
    await ctx.send(format_trader_scheduler_status_message(data, user_settings))


async def _send_trader_last_run(ctx) -> None:
    """Send the most recent scheduler run time only."""
    user_settings = _get_shared_user_settings(ctx)
    language = str(user_settings.get("language", "bilingual"))
    data = trader_scheduler_status(log_limit=2)
    data = _require_dict(data, "trader scheduler status response")
    value = data.get("last_run_time_utc") or "N/A"
    if language == "zh":
        await ctx.send(f"上次執行時間: {value}")
    elif language == "en":
        await ctx.send(f"Last run time: {value}")
    else:
        await ctx.send(f"Last run / 上次執行: {value}")


async def _send_trader_next_run(ctx) -> None:
    """Send the next scheduled run time only."""
    user_settings = _get_shared_user_settings(ctx)
    language = str(user_settings.get("language", "bilingual"))
    data = trader_scheduler_status(log_limit=2)
    data = _require_dict(data, "trader scheduler status response")
    value = data.get("next_run_time_utc") or "N/A"
    if language == "zh":
        await ctx.send(f"下次執行時間: {value}")
    elif language == "en":
        await ctx.send(f"Next run time: {value}")
    else:
        await ctx.send(f"Next run / 下次執行: {value}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    """Handle explicit commands first, then try rule-based natural-language routing."""
    if message.author.bot:
        return

    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    content = (message.content or "").strip()
    if not content:
        return

    if content.startswith(COMMAND_PREFIX):
        await bot.process_commands(message)
        return

    parsed = parse_natural_language_message(content)
    print(
        "NLP ROUTER:",
        {
            "user_id": getattr(message.author, "id", None),
            "intent": parsed.intent,
            "tickers": parsed.tickers,
            "language": parsed.language,
            "compact_mode": parsed.compact_mode,
            "amount": parsed.amount,
            "needs_help_hint": parsed.needs_help_hint,
        },
    )

    if not parsed.intent:
        if parsed.needs_help_hint and parsed.message:
            await message.channel.send(parsed.message)
        return

    ctx = await bot.get_context(message)
    try:
        if parsed.intent == "show_settings":
            await _send_settings(ctx)
        elif parsed.intent == "set_language" and parsed.language:
            await _apply_language_setting(ctx, parsed.language)
        elif parsed.intent == "set_compact" and parsed.compact_mode is not None:
            await _apply_compact_setting(ctx, parsed.compact_mode)
        elif parsed.intent == "add_watchlist" and parsed.tickers:
            await _apply_watchlist_update(ctx, parsed.tickers, action="add")
        elif parsed.intent == "remove_watchlist" and parsed.tickers:
            await _apply_watchlist_update(ctx, parsed.tickers, action="remove")
        elif parsed.intent == "show_watchlist":
            await _send_watchlist(ctx)
        elif parsed.intent == "analyze" and parsed.tickers:
            await _send_analyze(ctx, parsed.tickers[0].upper())
        elif parsed.intent == "forecast" and parsed.tickers:
            await _send_forecast(ctx, parsed.tickers[0].upper())
        elif parsed.intent == "model_status":
            await _send_model_status(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "model_accuracy":
            await _send_model_accuracy(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "benchmark_shadow":
            await _send_benchmark_shadow(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "virtual_trader_summary":
            await _send_virtual_trader_summary(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "run_virtual_trader_now":
            await _run_virtual_trader_now(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "virtual_trader_trades":
            await _send_virtual_trader_trades(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "why_trade":
            await _send_why_trade(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "virtual_trader_compare":
            await _send_virtual_trader_compare(ctx, parsed.tickers[0].upper() if parsed.tickers else None)
        elif parsed.intent == "virtual_account_summary":
            await _send_virtual_account_summary(ctx)
        elif parsed.intent == "virtual_account_ledger":
            await _send_virtual_account_ledger(ctx, limit=10)
        elif parsed.intent == "virtual_cash_deposit" and parsed.amount is not None:
            await _change_virtual_cash(ctx, parsed.amount, "deposit")
        elif parsed.intent == "virtual_cash_withdraw" and parsed.amount is not None:
            await _change_virtual_cash(ctx, parsed.amount, "withdraw")
        elif parsed.intent == "set_monthly_virtual_cash" and parsed.amount is not None:
            await _set_monthly_virtual_cash(ctx, parsed.amount)
        elif parsed.intent == "trader_scheduler_status":
            await _send_trader_scheduler_status(ctx)
        elif parsed.intent == "trader_scheduler_last_run":
            await _send_trader_last_run(ctx)
        elif parsed.intent == "trader_scheduler_next_run":
            await _send_trader_next_run(ctx)
        elif parsed.needs_help_hint and parsed.message:
            await message.channel.send(parsed.message)
        else:
            await message.channel.send(
                "I'm not sure what you want to do. Try `show my settings`, `analyze VOO`, `model status VOO`, or `add Tesla to my watchlist`."
            )
    except Exception as exc:
        print("NLP ROUTER ERROR:", repr(exc))
        await message.channel.send(_friendly_error_message(exc))


@bot.event
async def on_command_error(ctx, error):
    """Turn common Discord command errors into friendly chat replies."""
    if not is_allowed(ctx):
        return

    if isinstance(error, commands.CommandNotFound):
        await ctx.send(
            f"I don't know that command yet. Use `{COMMAND_PREFIX}help` to see what's available."
        )
        return

    if isinstance(error, commands.MissingRequiredArgument):
        command_name = ctx.command.qualified_name if ctx.command else "command"
        await ctx.send(
            f"You're missing part of `{command_name}`. Use `{COMMAND_PREFIX}help` for quick examples."
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            f"That input doesn't look right. Use `{COMMAND_PREFIX}help` for examples."
        )
        return

    print("COMMAND ERROR:", repr(error))
    await ctx.send(_friendly_error_message(error))


@bot.command(name="help")
async def help_cmd(ctx):
    if not is_allowed(ctx):
        return
    await ctx.send(format_help_message(COMMAND_PREFIX))


@bot.command(name="settings")
async def settings_cmd(ctx):
    if not is_allowed(ctx):
        return

    await _send_settings(ctx)


@bot.command(name="link")
async def link_cmd(ctx, code: str = ""):
    """Link this Discord account to a web profile using a one-time code."""
    if not is_allowed(ctx):
        return
    clean_code = str(code).strip().upper()
    if not clean_code:
        await ctx.send(
            "Open Settings on the web, select **Link Discord**, then run "
            f"`{COMMAND_PREFIX}link YOUR-CODE` here."
        )
        return
    try:
        consume_discord_link(
            {
                "code": clean_code,
                "discord_user_id": str(ctx.author.id),
                "discord_display_name": _discord_display_name(ctx),
            }
        )
    except Exception as exc:
        await ctx.send(_friendly_error_message(exc))
        return
    await ctx.send(
        "Linked successfully. Your Discord commands now use the same "
        "watchlist, settings, alerts, and virtual account as the web."
    )


@bot.command(name="syncstatus")
async def syncstatus_cmd(ctx):
    """Show evidence that web and Discord are reading the same profile."""
    if not is_allowed(ctx):
        return
    try:
        await _send_sync_status(ctx)
    except Exception as exc:
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="setlang")
async def setlang_cmd(ctx, language: str):
    if not is_allowed(ctx):
        return

    try:
        await _apply_language_setting(ctx, language)
    except Exception as exc:
        print("SETLANG ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="setcompact")
async def setcompact_cmd(ctx, mode: str):
    if not is_allowed(ctx):
        return

    try:
        normalized = mode.strip().lower()
        if normalized not in {"on", "off"}:
            raise ValueError("Invalid compact mode. Use `!setcompact on` or `!setcompact off`.")
        await _apply_compact_setting(ctx, normalized == "on")
    except Exception as exc:
        print("SETCOMPACT ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="setwatchlist")
async def setwatchlist_cmd(ctx, *, raw_watchlist: str):
    if not is_allowed(ctx):
        return

    try:
        tickers = parse_watchlist_input(raw_watchlist)
        user_settings = _set_shared_watchlist(ctx, tickers)
        print(f"SETWATCHLIST user={ctx.author.id} watchlist={user_settings['default_watchlist']}")
        watchlist_text = ", ".join(user_settings["default_watchlist"])
        await ctx.send(
            f"Your watchlist is saved.\n"
            f"Using: `{watchlist_text}`"
        )
    except Exception as exc:
        print("SETWATCHLIST ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="addticker")
async def addticker_cmd(ctx, ticker: str):
    if not is_allowed(ctx):
        return

    try:
        await _apply_watchlist_update(ctx, [ticker.upper()], action="add")
    except Exception as exc:
        print("ADDTICKER ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="removeticker")
async def removeticker_cmd(ctx, ticker: str):
    if not is_allowed(ctx):
        return

    try:
        await _apply_watchlist_update(ctx, [ticker.upper()], action="remove")
    except Exception as exc:
        print("REMOVETICKER ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="resetsettings")
async def resetsettings_cmd(ctx):
    if not is_allowed(ctx):
        return

    user_settings = _reset_shared_settings(ctx)
    print(f"RESETSETTINGS user={ctx.author.id}")
    await ctx.send(
        "Your settings are back to the defaults.\n"
        f"{format_settings_message(ctx.author.id, user_settings)}"
    )


@bot.command(name="analyze")
async def analyze_cmd(ctx, ticker: str):
    if not is_allowed(ctx):
        return

    try:
        await _send_analyze(ctx, ticker.upper())
    except Exception as exc:
        print("ANALYZE ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="forecast")
async def forecast_cmd(ctx, ticker: str):
    if not is_allowed(ctx):
        return

    try:
        await _send_forecast(ctx, ticker.upper())
    except Exception as exc:
        print("FORECAST ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="watchlist")
async def watchlist_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_watchlist(ctx)
    except Exception as exc:
        print("WATCHLIST ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="alerts")
async def alerts_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_alerts(ctx)
    except Exception as exc:
        print("ALERTS ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="traderstatus")
async def traderstatus_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_trader_scheduler_status(ctx)
    except Exception as exc:
        print("TRADERSTATUS ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="lastrun")
async def lastrun_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_trader_last_run(ctx)
    except Exception as exc:
        print("LASTRUN ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="nextrun")
async def nextrun_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_trader_next_run(ctx)
    except Exception as exc:
        print("NEXTRUN ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="modelstatus")
async def modelstatus_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_model_status(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("MODELSTATUS ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="modelaccuracy")
async def modelaccuracy_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_model_accuracy(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("MODELACCURACY ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="virtualtrader")
async def virtualtrader_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_virtual_trader_summary(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("VIRTUALTRADER ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="runtrader")
async def runtrader_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _run_virtual_trader_now(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("RUNTRADER ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="lasttrades")
async def lasttrades_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_virtual_trader_trades(ctx, ticker.upper() if ticker else None, limit=5)
    except Exception as exc:
        print("LASTTRADES ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="whytrade")
async def whytrade_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_why_trade(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("WHYTRADE ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="comparetrader")
async def comparetrader_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_virtual_trader_compare(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("COMPARETRADER ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="account")
async def account_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_virtual_account_summary(ctx)
    except Exception as exc:
        print("ACCOUNT ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="cashledger")
async def cashledger_cmd(ctx):
    if not is_allowed(ctx):
        return

    try:
        await _send_virtual_account_ledger(ctx, limit=10)
    except Exception as exc:
        print("CASHLEDGER ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="shadowstatus")
async def shadowstatus_cmd(ctx, ticker: str | None = None):
    if not is_allowed(ctx):
        return

    try:
        await _send_benchmark_shadow(ctx, ticker.upper() if ticker else None)
    except Exception as exc:
        print("SHADOWSTATUS ERROR:", repr(exc))
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="deposit")
async def deposit_cmd(ctx, amount: float):
    """Add simulation-only cash to the linked virtual account."""
    if not is_allowed(ctx):
        return
    try:
        await _change_virtual_cash(ctx, amount, "deposit")
    except Exception as exc:
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="withdraw")
async def withdraw_cmd(ctx, amount: float):
    """Withdraw simulation-only cash from the linked virtual account."""
    if not is_allowed(ctx):
        return
    try:
        await _change_virtual_cash(ctx, amount, "withdraw")
    except Exception as exc:
        await ctx.send(_friendly_error_message(exc))


@bot.command(name="setmonthly")
async def setmonthly_cmd(ctx, amount: float):
    """Set recurring monthly simulation cash for the linked account."""
    if not is_allowed(ctx):
        return
    try:
        await _set_monthly_virtual_cash(ctx, amount)
    except Exception as exc:
        await ctx.send(_friendly_error_message(exc))


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is required to start the Discord bot.")
    bot.run(DISCORD_BOT_TOKEN)
