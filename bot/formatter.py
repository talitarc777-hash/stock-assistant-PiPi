"""Formatting helpers for beginner-friendly Discord bot replies."""

from __future__ import annotations

from typing import Any


def _text(language: str, en: str, zh: str, bilingual: str | None = None) -> str:
    """Pick a short UI label based on the user's language mode."""
    if language == "en":
        return en
    if language == "zh":
        return zh
    return bilingual or f"{en} / {zh}"


def _format_price(value: Any) -> str:
    """Format a numeric price with a safe fallback."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_percent(value: Any, decimals: int = 1) -> str:
    """Format a percentage value with a safe fallback."""
    try:
        return f"{float(value):.{decimals}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _format_ratio(value: Any, decimals: int = 1) -> str:
    """Format a ratio between 0 and 1 as a percent string."""
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _format_number(value: Any, decimals: int = 2) -> str:
    """Format a general numeric value safely."""
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_money(value: Any) -> str:
    """Format a money amount in USD with a safe fallback."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_bool(value: bool) -> str:
    """Render a boolean setting in a user-friendly way."""
    return "on" if value else "off"


def _safe_text(value: Any, fallback: str = "N/A") -> str:
    """Return a clean string for display."""
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _format_signal_value(value: Any) -> str:
    """Format a binary model signal in a readable way."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return _safe_text(value)
    if numeric == 1.0:
        return "Up / 上升"
    if numeric == 0.0:
        return "Down / 下降"
    return _safe_text(value)


def _plain_decision_reason(reason: Any, language: str) -> str:
    """Translate live-trader reason codes into beginner-facing language."""
    labels = {
        "model_bullish_signal": ("The model supported a simulated buy", "模型支持模擬買入"),
        "model_bearish_signal": ("The model supported reducing the holding", "模型支持減少持倉"),
        "model_not_bullish": ("The model does not support buying yet", "模型暫時不支持買入"),
        "confidence_below_threshold": ("Model support was below the required level", "模型支持程度低於要求"),
        "risk_or_cash_constraint": ("Cash or risk limits blocked the trade", "現金或風險限制阻止交易"),
        "context_score_too_low": ("The wider evidence was too weak to buy", "整體證據不足以買入"),
        "context_risk_reduction": ("Wider risks caused a partial simulated sale", "整體風險觸發部分模擬賣出"),
        "portfolio_drawdown_pause": ("Account losses paused new buying", "帳戶虧損觸發暫停新買入"),
        "portfolio_drawdown_reduction": ("Large account losses triggered risk reduction", "帳戶大幅虧損觸發減持"),
        "market_data_quality_block": ("Unreliable market data blocked buying", "市場資料不可靠，因此阻止買入"),
        "holding_position": ("The simulation continues to hold", "模擬帳戶繼續持有"),
        "stop_loss": ("The loss limit was reached", "已觸及虧損上限"),
        "take_profit": ("The profit target was reached", "已達到獲利目標"),
        "signal_cooldown_active": ("The trader is waiting before repeating a trade", "交易工具正在等待，避免短時間重複交易"),
        "duplicate_signal_suppressed": ("A repeated decision was ignored", "重複決定已被忽略"),
        "fallback_rule_bullish_trend_momentum": ("Backup rules found an upward trend", "後備規則發現上升趨勢"),
        "fallback_rule_bearish_trend": ("Backup rules found a downward trend", "後備規則發現下跌趨勢"),
        "fallback_rule_neutral_hold": ("Backup rules found no clear direction", "後備規則未發現明確方向"),
    }
    normalized = str(reason or "").strip().lower()
    pair = labels.get(normalized)
    if pair is None:
        readable = normalized.replace("_", " ") or "No reason available"
        return readable
    return _text(language, pair[0], pair[1], f"{pair[0]} / {pair[1]}")


def _market_protection_text(regime: dict[str, Any], language: str) -> tuple[str, str]:
    """Explain market-wide risk protection and its trading effect."""
    level = str(regime.get("level") or "unknown").lower()
    level_labels = {
        "normal": ("Normal market conditions", "一般市場狀況"),
        "caution": ("Cautious market conditions", "需要審慎的市場狀況"),
        "stress": ("High-risk market conditions", "高風險市場狀況"),
        "unknown": ("Market protection unavailable", "未有市場保護資料"),
    }
    reason_labels = {
        "benchmark_20d_selloff": ("the wider market fell sharply over the last month", "整體市場在最近一個月大幅下跌"),
        "benchmark_20d_weakness": ("the wider market was weak over the last month", "整體市場在最近一個月表現疲弱"),
        "ticker_deep_drawdown": ("this stock is far below its recent peak", "這隻股票遠低於近期高位"),
        "ticker_drawdown": ("this stock has fallen from its recent peak", "這隻股票已由近期高位回落"),
        "extreme_volatility": ("the price is moving extremely sharply", "價格波動極為劇烈"),
        "elevated_volatility": ("the price is moving more sharply than usual", "價格波動較平常劇烈"),
    }
    pair = level_labels.get(level, level_labels["unknown"])
    label = _text(language, pair[0], pair[1], f"{pair[0]} / {pair[1]}")
    try:
        multiplier = float(regime.get("position_size_multiplier"))
    except (TypeError, ValueError):
        multiplier = None
    if regime.get("new_position_allowed") is False or multiplier == 0:
        effect = _text(language, "new buying is paused", "暫停新買入", "new buying is paused / 暫停新買入")
    elif multiplier is not None and multiplier < 1:
        percentage = f"{multiplier * 100:.0f}%"
        effect = _text(language, f"new buys use {percentage} of normal size", f"新買入使用正常倉位的 {percentage}", f"new buys use {percentage} of normal size / 新買入使用正常倉位的 {percentage}")
    else:
        effect = _text(language, "normal position size is allowed", "可使用正常倉位", "normal position size is allowed / 可使用正常倉位")
    reasons = []
    for reason in regime.get("reasons") or []:
        reason_pair = reason_labels.get(str(reason))
        if reason_pair:
            reasons.append(_text(language, reason_pair[0], reason_pair[1], f"{reason_pair[0]} / {reason_pair[1]}"))
    detail = f"{label}; {effect}"
    if reasons:
        detail += "; " + "; ".join(reasons)
    return label, detail


def _select_language_text(data: dict[str, Any], base_key: str, language: str) -> Any:
    """Select one field variant based on the user's language preference."""
    if language == "en":
        return data.get(f"{base_key}_en", data.get(base_key))
    if language == "bilingual":
        return data.get(f"{base_key}_bilingual", data.get(base_key))
    return data.get(f"{base_key}_zh", data.get(base_key))


def _format_bullets(items: list[str], limit: int = 3, fallback: str = "- No details yet.") -> str:
    """Render a short bullet list for Discord."""
    if not items:
        return fallback
    return "\n".join(f"- {item}" for item in items[:limit])


def _coerce_list(value: Any) -> list[Any]:
    """Return a list or an empty list so formatters stay predictable."""
    return value if isinstance(value, list) else []


def format_settings_message(user_id: int, settings: dict[str, Any]) -> str:
    """Render current per-user settings clearly and briefly."""
    watchlist = ", ".join(settings.get("default_watchlist", [])) or "system default"
    alert_watchlist = ", ".join(settings.get("alert_watchlist", [])) or "same as watchlist"
    return (
        "Your settings\n"
        f"- User ID: {user_id}\n"
        f"- Language: {settings.get('language', 'zh')}\n"
        f"- Compact mode: {_format_bool(bool(settings.get('compact_mode', False)))}\n"
        f"- Watchlist: {watchlist}\n"
        f"- Alerts: {_format_bool(bool(settings.get('alert_enabled', True)))} "
        f"(low {settings.get('alert_threshold_low', 45)} / high {settings.get('alert_threshold_high', 80)})\n"
        f"- Alert watchlist: {alert_watchlist}\n"
        "Tip: use `!addticker` or `!removeticker` for quick changes."
    )


def format_help_message(prefix: str) -> str:
    """Render a short, practical help guide."""
    return (
        "Stock bot help\n"
        "Use a command or type a simple request in plain language.\n"
        "\n"
        "Main commands\n"
        f"- `{prefix}analyze VOO` check one ticker\n"
        f"- `{prefix}forecast NVDA` view the outlook\n"
        f"- `{prefix}watchlist` rank your watchlist\n"
        f"- `{prefix}alerts` show current alert signals\n"
        f"- `{prefix}traderstatus` show scheduler status\n"
        f"- `{prefix}lastrun` show last scheduler run\n"
        f"- `{prefix}nextrun` show next scheduler run\n"
        f"- `{prefix}modelstatus VOO` show the latest model signal\n"
        f"- `{prefix}modelaccuracy VOO` show model hit rate and metrics\n"
        f"- `{prefix}shadowstatus SPY` show genuine forward promotion evidence\n"
        f"- `{prefix}virtualtrader VOO` show trader summary\n"
        f"- `{prefix}account` show virtual account summary\n"
        f"- `{prefix}deposit 1000` add simulation cash\n"
        f"- `{prefix}withdraw 100` withdraw simulation cash\n"
        f"- `{prefix}setmonthly 500` set monthly simulation cash\n"
        f"- `{prefix}cashledger` show recent cash/trade ledger\n"
        f"- `{prefix}runtrader VOO` run live trader now\n"
        f"- `{prefix}lasttrades VOO` show recent trades\n"
        f"- `{prefix}whytrade VOO` explain the latest trade\n"
        f"- `{prefix}comparetrader VOO` compare the trader with VOO\n"
        "\n"
        "Settings\n"
        f"- `{prefix}syncstatus` verify web/Discord shared data\n"
        f"- `{prefix}settings`\n"
        f"- `{prefix}link CODE` connect to your web profile\n"
        f"- `{prefix}setlang en|zh|bilingual`\n"
        f"- `{prefix}setcompact on|off`\n"
        f"- `{prefix}setwatchlist VOO,QQQ,AAPL`\n"
        f"- `{prefix}addticker MSFT`\n"
        f"- `{prefix}removeticker QQQ`\n"
        f"- `{prefix}resetsettings`\n"
        "\n"
        "Natural-language examples\n"
        "- `set my language to Chinese`\n"
        "- `turn on compact mode`\n"
        "- `add Tesla to my watchlist`\n"
        "- `show my watchlist`\n"
        "- `model status VOO`\n"
        "- `show prediction accuracy for VOO`\n"
        "- `show forward model status for SPY`\n"
        "- `show virtual trader summary`\n"
        "- `run trader now`\n"
        "- `show last 5 trades`\n"
        "- `why did the model buy or sell`\n"
        "- `compare virtual trader vs VOO`"
    )


def format_sync_status_message(data: dict[str, Any], prefix: str = "!") -> str:
    """Show concrete evidence that Discord is reading the web profile state."""
    if not data.get("linked"):
        return (
            "Web/Discord sync: not linked\n"
            "Open Settings on the web, generate a link code, then send "
            f"`{prefix}link CODE`."
        )
    watchlist = ", ".join(_coerce_list(data.get("watchlist"))) or "empty"
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    return (
        "Web/Discord sync: connected\n"
        f"- Web profile: {_safe_text(data.get('profile_user_id'))}\n"
        f"- Discord account: {_safe_text(data.get('discord_display_name'), _safe_text(data.get('discord_user_id')))}\n"
        f"- Shared watchlist: {watchlist}\n"
        f"- Shared virtual equity: {_format_money(account.get('total_equity'))}\n"
        f"- Shared recent trades: {int(data.get('recent_trade_count') or 0)}\n"
        f"- Backend snapshot: {_safe_text(data.get('generated_at_utc'))}\n"
        "These values come from the same backend profile used by the web dashboard."
    )


def format_analyze_message(symbol: str, data: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format analyze response using the user's settings."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))

    score_breakdown = data.get("score_breakdown", {})
    latest_close = _format_price(data.get("latest_close"))
    score = score_breakdown.get("total_score", "N/A")
    label = data.get("label", "N/A")
    action = _select_language_text(data, "action_summary", language) or "N/A"
    bullets = _select_language_text(data, "explanation_bullets", language) or []
    if not isinstance(bullets, list):
        bullets = []

    if compact_mode:
        return (
            f"{symbol} snapshot\n"
            f"- Close: {latest_close}\n"
            f"- Score: {score}\n"
            f"- Action: {action}"
        )

    return (
        f"{symbol} analysis\n"
        f"- Close: {latest_close}\n"
        f"- Score: {score}\n"
        f"- Label: {label}\n"
        f"- Action: {action}\n"
        "\n"
        "Why it stands out\n"
        f"{_format_bullets(bullets, limit=3)}"
    )


def format_forecast_message(symbol: str, data: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format forecast response using the user's settings."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))

    if language == "zh":
        trend = data.get("trend_regime_zh", data.get("trend_regime", "N/A"))
    elif language == "bilingual":
        trend = f"{data.get('trend_regime_en', 'N/A')} / {data.get('trend_regime_zh', 'N/A')}"
    else:
        trend = data.get("trend_regime_en", data.get("trend_regime", "N/A"))

    expected_range = data.get("expected_range", {})
    levels = data.get("levels", {})
    lower = _format_price(expected_range.get("lower"))
    upper = _format_price(expected_range.get("upper"))
    support = _format_price(levels.get("support_level"))
    resistance = _format_price(levels.get("resistance_level"))
    confidence = data.get("confidence_score", "N/A")

    title = _text(language, "Forecast", "預測", "Forecast / 預測")
    trend_label = _text(language, "Trend regime", "走勢狀態", "Trend regime / 走勢狀態")
    range_label = _text(language, "Expected range", "預期區間", "Expected range / 預期區間")
    confidence_label = _text(language, "Confidence", "信心評分", "Confidence / 信心評分")
    support_label = _text(language, "Support", "支撐位", "Support / 支撐位")
    resistance_label = _text(language, "Resistance", "阻力位", "Resistance / 阻力位")

    if compact_mode:
        return (
            f"{title}: {symbol}\n"
            f"- {trend_label}: {trend}\n"
            f"- {range_label}: {lower} - {upper}\n"
            f"- {confidence_label}: {confidence}/100"
        )

    return (
        f"{title}: {symbol}\n"
        f"- {trend_label}: {trend}\n"
        f"- {range_label}: {lower} - {upper}\n"
        f"- {confidence_label}: {confidence}/100\n"
        f"- {support_label}: {support}\n"
        f"- {resistance_label}: {resistance}"
    )


def format_watchlist_message(
    ranked: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    used_watchlist: list[str],
    settings: dict[str, Any],
) -> str:
    """Format watchlist response using the user's settings."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    top_rows = ranked[:5]

    if top_rows:
        lines = []
        for index, item in enumerate(top_rows, start=1):
            ticker = item.get("ticker", "N/A")
            score = item.get("score_breakdown", {}).get("total_score", "N/A")
            label = item.get("label", "N/A")
            lines.append(f"{index}. {ticker} | Score: {score} | {label}")
        ranked_text = "\n".join(lines)
    else:
        ranked_text = _text(language, "No ranked results yet.", "暫時未有排名結果", "No ranked results yet / 暫時未有排名結果")

    title = _text(language, "Watchlist", "觀察名單", "Watchlist / 觀察名單")
    using_label = _text(language, "Using", "使用中", "Using / 使用中")
    top_ranked_label = _text(language, "Top ranked", "最高排名", "Top ranked / 最高排名")
    failed_label = _text(language, "Skipped", "略過", "Skipped / 略過")
    none_label = _text(language, "- None", "- 無", "- None / - 無")

    if compact_mode:
        return f"{title}\n{ranked_text}"

    failed_text = (
        "\n".join(
            f"- {row.get('ticker', 'N/A')}: {row.get('error', 'Unknown error')}"
            for row in failed[:3]
        )
        if failed
        else none_label
    )
    watchlist_text = ", ".join(used_watchlist) if used_watchlist else "(empty)"
    return (
        f"{title}\n"
        f"- {using_label}: {watchlist_text}\n"
        "\n"
        f"{top_ranked_label}\n"
        f"{ranked_text}\n"
        "\n"
        f"{failed_label}\n"
        f"{failed_text}"
    )


def format_alerts_message(alert_lines: list[str], settings: dict[str, Any]) -> str:
    """Format a Discord alert block for current watchlist alerts."""
    language = settings.get("language", "zh")
    title = _text(language, "Current alerts", "目前提示", "Current alerts / 目前提示")
    no_alerts = _text(language, "No new alerts right now.", "目前沒有新提示。", "No new alerts right now / 目前沒有新提示")
    if not alert_lines:
        return f"{title}\n{no_alerts}"
    return f"{title}\n" + "\n".join(alert_lines)


def format_model_status_message(symbol: str, data: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format the latest model prediction into a compact Discord reply."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    latest = data.get("latest_prediction", {})

    title = _text(language, "Model status", "模型狀態", "Model status / 模型狀態")
    signal_label = _text(language, "Latest signal", "最新訊號", "Latest signal / 最新訊號")
    confidence_label = _text(language, "Confidence", "信心", "Confidence / 信心")
    risk_label = _text(
        language,
        "Portfolio protection",
        "投資組合保護",
        "Portfolio protection / 投資組合保護",
    )
    risk_value = _safe_text(account.get("portfolio_risk_level"), "unavailable")
    if account.get("buying_paused"):
        risk_value += _text(
            language,
            " (new buys paused)",
            "（暫停新買入）",
            " (new buys paused / 暫停新買入)",
        )
    actual_label = _text(language, "Actual result", "實際結果", "Actual result / 實際結果")
    reason_label = _text(language, "Reason", "原因摘要", "Reason / 原因摘要")

    predicted_value = latest.get("predicted_value")
    signal_text = _safe_text(predicted_value)
    if latest.get("target_name") == "target_5d_updown":
        signal_text = _format_signal_value(predicted_value)

    confidence_text = _format_ratio(latest.get("confidence_score"))
    actual_value = latest.get("actual_future_result")
    actual_text = _safe_text(actual_value)
    if latest.get("task_type") == "classification" and actual_value is not None:
        actual_text = _format_signal_value(actual_value)

    reason = _safe_text(latest.get("explanation"))
    date_text = _safe_text(latest.get("prediction_date"))

    if compact_mode:
        return (
            f"{title}: {symbol}\n"
            f"- {signal_label}: {signal_text}\n"
            f"- {confidence_label}: {confidence_text}\n"
            f"- {actual_label}: {actual_text}"
        )

    return (
        f"{title}: {symbol}\n"
        f"- Date: {date_text}\n"
        f"- {signal_label}: {signal_text}\n"
        f"- {confidence_label}: {confidence_text}\n"
        f"- {actual_label}: {actual_text}\n"
        f"- {reason_label}: {reason}"
    )


def format_model_accuracy_message(symbol: str, data: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format model evaluation metrics for Discord."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    metrics_summary = data.get("metrics_summary", {})
    metrics = metrics_summary.get("metrics", {})
    latest_rolling_accuracy = data.get("latest_rolling_accuracy")
    task_type = _safe_text(metrics_summary.get("task_type"), "classification")
    try:
        validation_scheme_version = int(metrics_summary.get("validation_scheme_version") or 0)
    except (TypeError, ValueError):
        validation_scheme_version = 0
    pooled_training = bool(metrics_summary.get("pooled_training"))
    stationary_features = bool(
        metrics_summary.get("stationary_features")
        or metrics_summary.get("pooled_stationary_features")
    )
    training_tickers = _coerce_list(metrics_summary.get("training_tickers"))
    economics = metrics_summary.get("outperformance_economics_gate") or {}

    title = _text(language, "Prediction accuracy", "預測表現", "Prediction accuracy / 預測表現")
    rolling_label = _text(language, "Rolling hit rate", "滾動命中率", "Rolling hit rate / 滾動命中率")
    latest_label = _text(language, "Latest", "最新", "Latest / 最新")

    if task_type == "classification":
        lines = [
            f"- {rolling_label}: {_format_ratio(latest_rolling_accuracy)}",
            f"- Accuracy: {_format_ratio(metrics.get('accuracy'))}",
            f"- Precision: {_format_ratio(metrics.get('precision'))}",
            f"- Recall: {_format_ratio(metrics.get('recall'))}",
            f"- F1: {_format_number(metrics.get('f1'), 3)}",
        ]
    else:
        lines = [
            f"- {rolling_label}: {_format_ratio(latest_rolling_accuracy)}",
            f"- MAE: {_format_number(metrics.get('mae'))}",
            f"- RMSE: {_format_number(metrics.get('rmse'))}",
            f"- R2: {_format_number(metrics.get('r2'), 3)}",
            f"- Direction accuracy: {_format_ratio(metrics.get('direction_accuracy'))}",
        ]
        evidence_text = _text(
            language,
            "Current evidence: uncertainty and market-regime checks can convert a prediction to no action"
            if validation_scheme_version >= 4
            else "Legacy evidence: retraining is required for current uncertainty and regime checks",
            "目前證據：不確定的預測會經校準後列為不行動"
            if validation_scheme_version >= 4
            else "舊版證據：需要重新訓練以進行校準不行動檢查",
            "Current calibrated evidence / 目前校準證據"
            if validation_scheme_version >= 4
            else "Legacy evidence; retraining required / 舊版證據；需要重新訓練",
        )
        lines.append(f"- Evidence: {evidence_text}")
        lines.append(
            "- Feature safety: "
            + (
                "scale-independent return inputs"
                if stationary_features
                else "legacy price-level inputs; retraining required"
            )
        )
        if pooled_training:
            lines.append(
                "- Shared-model check: "
                f"{len(training_tickers)} tickers; scale-independent inputs; "
                "aggregate results alone cannot validate it"
            )

    if economics:
        economics_passed = bool(economics.get("passed"))
        profit_status = _text(
            language,
            "Passed" if economics_passed else "Failed",
            "通過" if economics_passed else "未通過",
            "Passed / 通過" if economics_passed else "Failed / 未通過",
        )
        lines.insert(1, f"- Profit check after costs: {profit_status}")
        lines.append(
            "- Average net return per signal: "
            f"{_format_number(economics.get('average_net_stock_return_pct'))}%"
        )
        lines.append(
            "- Profitable independent paths: "
            f"{_format_ratio(economics.get('profitable_non_overlapping_path_rate'))}"
        )
        lines.append(
            "- Accuracy does not mean profit; this check uses actual stock returns after estimated costs."
        )

    history = _coerce_list(data.get("rolling_accuracy"))
    latest_point = history[-1] if history else {}
    latest_line = f"- {latest_label}: {_safe_text(latest_point.get('date'))}"

    if compact_mode:
        return f"{title}: {symbol}\n" + "\n".join(lines[:3])

    return f"{title}: {symbol}\n{latest_line}\n" + "\n".join(lines)


def format_benchmark_shadow_message(
    symbol: str,
    data: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    """Explain genuine forward promotion evidence for beginners."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    historical = data.get("historical_evidence") if isinstance(data.get("historical_evidence"), dict) else {}
    quality = historical.get("quality_gate") if isinstance(historical.get("quality_gate"), dict) else {}
    samples = int(summary.get("sample_count") or 0)
    pending = int(summary.get("pending_count") or 0)
    required = int(summary.get("required_sample_count") or summary.get("minimum_samples_for_promotion") or 8)
    active = int(summary.get("active_signal_count") or 0)
    required_active = int(summary.get("required_active_signal_count") or 5)
    passed = bool(summary.get("passed"))
    state = (
        _text(language, "Ready for promotion review", "\u53ef\u9032\u884c\u6649\u7d1a\u5be9\u6838", "Ready for promotion review / \u53ef\u9032\u884c\u6649\u7d1a\u5be9\u6838")
        if passed
        else _text(language, "Still collecting evidence", "\u4ecd\u5728\u6536\u96c6\u8b49\u64da", "Still collecting evidence / \u4ecd\u5728\u6536\u96c6\u8b49\u64da")
    )
    title = _text(language, "Forward model check", "\u524d\u77bb\u6a21\u578b\u6aa2\u67e5", "Forward model check / \u524d\u77bb\u6a21\u578b\u6aa2\u67e5")
    status_label = _text(language, "Status", "\u72c0\u614b", "Status / \u72c0\u614b")
    matured_label = _text(language, "Matured predictions", "\u5df2\u5230\u671f\u9810\u6e2c", "Matured predictions / \u5df2\u5230\u671f\u9810\u6e2c")
    pending_label = _text(language, "Pending five-day outcomes", "\u7b49\u5f85\u4e94\u500b\u4ea4\u6613\u65e5\u7d50\u679c", "Pending five-day outcomes / \u7b49\u5f85\u4e94\u500b\u4ea4\u6613\u65e5\u7d50\u679c")
    latest_label = _text(language, "Latest observation", "\u6700\u65b0\u89c0\u5bdf", "Latest observation / \u6700\u65b0\u89c0\u5bdf")
    maturity_label = _text(language, "Earliest estimated maturity", "\u6700\u65e9\u4f30\u8a08\u5230\u671f\u65e5", "Earliest estimated maturity / \u6700\u65e9\u4f30\u8a08\u5230\u671f\u65e5")
    active_label = _text(language, "Active signals", "\u4e3b\u52d5\u8a0a\u865f", "Active signals / \u4e3b\u52d5\u8a0a\u865f")
    accuracy_label = _text(language, "Forward accuracy", "\u524d\u77bb\u6b63\u78ba\u7387", "Forward accuracy / \u524d\u77bb\u6b63\u78ba\u7387")
    return_label = _text(language, "Average active return after cost", "\u6263\u9664\u6210\u672c\u5f8c\u7684\u4e3b\u52d5\u8a0a\u865f\u5e73\u5747\u56de\u5831", "Average active return after cost / \u6263\u9664\u6210\u672c\u5f8c\u7684\u4e3b\u52d5\u8a0a\u865f\u5e73\u5747\u56de\u5831")
    profitable_label = _text(language, "Profitable active signals", "\u7372\u5229\u4e3b\u52d5\u8a0a\u865f", "Profitable active signals / \u7372\u5229\u4e3b\u52d5\u8a0a\u865f")
    lines = [
        f"{title}: {symbol}",
        f"- {status_label}: {state}",
        f"- {matured_label}: {samples}/{required}",
        f"- {pending_label}: {pending}",
        f"- {active_label}: {active}/{required_active}",
    ]
    if quality:
        history_label = _text(language, "Historical context", "\u6b77\u53f2\u80cc\u666f", "Historical context / \u6b77\u53f2\u80cc\u666f")
        balance_label = _text(language, "Imbalance checks", "\u4e0d\u5e73\u8861\u6aa2\u67e5", "Imbalance checks / \u4e0d\u5e73\u8861\u6aa2\u67e5")
        lines.append(
            f"- {history_label}: {_format_ratio(quality.get('direction_accuracy'))} raw vs "
            f"{_format_ratio(quality.get('naive_majority_accuracy'))} common-result baseline; "
            f"{_format_ratio(quality.get('direction_edge'))} lift"
        )
        lines.append(
            f"- {balance_label}: {_format_ratio(quality.get('balanced_direction_accuracy'))} balanced accuracy; "
            f"{_format_ratio(quality.get('worst_class_recall'))} harder-class recall"
        )
    if summary.get("latest_observation_date"):
        lines.append(f"- {latest_label}: {summary['latest_observation_date']} ({summary.get('latest_observation_status') or 'unknown'})")
    if summary.get("estimated_next_maturity_date"):
        lines.append(
            f"- {maturity_label}: {summary['estimated_next_maturity_date']} "
            f"({_text(language, 'market holidays or delayed data can move this date', '\u5e02\u5834\u5047\u671f\u6216\u6578\u64da\u5ef6\u8aa4\u53ef\u80fd\u4f7f\u65e5\u671f\u9806\u5ef6', 'market holidays or delayed data can move this date / \u5e02\u5834\u5047\u671f\u6216\u6578\u64da\u5ef6\u8aa4\u53ef\u80fd\u4f7f\u65e5\u671f\u9806\u5ef6')})"
        )
    if samples:
        lines.append(f"- {accuracy_label}: {_format_ratio(summary.get('direction_accuracy'))}")
    if active:
        lines.extend(
            [
                f"- {return_label}: {_format_percent(summary.get('average_active_net_return_pct'))}",
                f"- {profitable_label}: {_format_ratio(summary.get('active_profitable_rate'))}",
            ]
        )
    if not compact_mode and not passed:
        reasons = _coerce_list(summary.get("reasons"))
        if reasons:
            reason_labels = {
                "insufficient_matured_forward_predictions": _text(language, "more matured predictions", "\u66f4\u591a\u5df2\u5230\u671f\u9810\u6e2c", "more matured predictions / \u66f4\u591a\u5df2\u5230\u671f\u9810\u6e2c"),
                "insufficient_matured_active_signals": _text(language, "more active signals", "\u66f4\u591a\u4e3b\u52d5\u8a0a\u865f", "more active signals / \u66f4\u591a\u4e3b\u52d5\u8a0a\u865f"),
                "forward_direction_accuracy_below_minimum": _text(language, "higher forward accuracy", "\u66f4\u9ad8\u7684\u524d\u77bb\u6b63\u78ba\u7387", "higher forward accuracy / \u66f4\u9ad8\u7684\u524d\u77bb\u6b63\u78ba\u7387"),
                "forward_average_net_return_not_positive": _text(language, "positive average return after costs", "\u6263\u9664\u6210\u672c\u5f8c\u5e73\u5747\u56de\u5831\u70ba\u6b63\u6578", "positive average return after costs / \u6263\u9664\u6210\u672c\u5f8c\u5e73\u5747\u56de\u5831\u70ba\u6b63\u6578"),
                "forward_profitable_rate_below_minimum": _text(language, "a higher profitable-signal rate", "\u66f4\u9ad8\u7684\u7372\u5229\u8a0a\u865f\u6bd4\u7387", "a higher profitable-signal rate / \u66f4\u9ad8\u7684\u7372\u5229\u8a0a\u865f\u6bd4\u7387"),
            }
            needed_label = _text(language, "Still needed", "\u4ecd\u9700\u8981", "Still needed / \u4ecd\u9700\u8981")
            friendly_reasons = [
                reason_labels.get(str(item), str(item).replace("_", " "))
                for item in reasons
            ]
            lines.append(f"- {needed_label}: {', '.join(friendly_reasons)}")
    lines.append(_text(
        language,
        "- Meaning: only predictions made before later prices were known count. This is virtual research, not guaranteed profit or financial advice.",
        "- \u8aaa\u660e\uff1a\u53ea\u6709\u5728\u5c1a\u672a\u77e5\u9053\u5f8c\u4f86\u50f9\u683c\u524d\u4f5c\u51fa\u7684\u9810\u6e2c\u624d\u6703\u8a08\u7b97\u3002\u9019\u662f\u865b\u64ec\u7814\u7a76\uff0c\u4e0d\u4fdd\u8b49\u7372\u5229\uff0c\u4e5f\u4e0d\u662f\u8ca1\u52d9\u5efa\u8b70\u3002",
        "- Meaning / \u8aaa\u660e: only predictions made before later prices were known count. This is virtual research, not guaranteed profit or financial advice. / \u53ea\u6709\u5728\u5c1a\u672a\u77e5\u9053\u5f8c\u4f86\u50f9\u683c\u524d\u4f5c\u51fa\u7684\u9810\u6e2c\u624d\u6703\u8a08\u7b97\uff1b\u4e0d\u4fdd\u8b49\u7372\u5229\u3002",
    ))
    return "\n".join(lines)


def format_virtual_trader_summary_message(
    symbol: str,
    data: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    """Format the saved virtual trader summary for Discord."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    summary = data.get("summary", {})

    title = _text(language, "Virtual trader", "模擬交易", "Virtual trader / 模擬交易")
    pnl_label = _text(language, "PnL", "盈虧", "PnL / 盈虧")
    cash_label = _text(language, "Cash", "現金", "Cash / 現金")
    holdings_label = _text(language, "Holdings", "持倉", "Holdings / 持倉")
    trades_label = _text(language, "Trades", "交易次數", "Trades / 交易次數")
    compare_label = _text(language, "Vs VOO", "相對 VOO", "Vs VOO / 相對 VOO")

    cash = _format_money(summary.get("cash"))
    holdings_count = _format_number(summary.get("holdings"), 4)
    realized = _format_money(summary.get("realized_pnl"))
    unrealized = _format_money(summary.get("unrealized_pnl"))
    trade_count = _safe_text(summary.get("trade_count"))
    outperformance = _format_percent(summary.get("outperformance_vs_benchmark_pct_points"))

    if compact_mode:
        return (
            f"{title}: {symbol}\n"
            f"- {pnl_label}: {realized}\n"
            f"- {cash_label}: {cash}\n"
            f"- {holdings_label}: {holdings_count}"
        )

    return (
        f"{title}: {symbol}\n"
        f"- {cash_label}: {cash}\n"
        f"- {holdings_label}: {holdings_count}\n"
        f"- {pnl_label}: {realized} realized, {unrealized} unrealized\n"
        f"- {trades_label}: {trade_count}\n"
        f"- {compare_label}: {outperformance}"
    )


def format_virtual_trader_trades_message(
    symbol: str,
    data: dict[str, Any],
    settings: dict[str, Any],
    limit: int = 5,
) -> str:
    """Format recent virtual trader trades for Discord."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    trades = _coerce_list(data.get("trade_log"))[-limit:]

    title = _text(language, "Recent trades", "最近交易", "Recent trades / 最近交易")
    none_text = _text(language, "No trades saved yet.", "暫時未有已保存交易。", "No trades saved yet / 暫時未有已保存交易")
    if not trades:
        return f"{title}: {symbol}\n- {none_text}"

    lines = []
    for trade in reversed(trades):
        action = _safe_text(trade.get("action")).upper()
        price = _format_price(trade.get("price"))
        confidence = _format_ratio(trade.get("model_confidence"))
        timestamp = _safe_text(trade.get("timestamp"))
        if compact_mode:
            lines.append(f"- {timestamp[:10]} | {action} | {price}")
        else:
            lines.append(f"- {timestamp[:10]} | {action} | {price} | conf {confidence}")

    return f"{title}: {symbol}\n" + "\n".join(lines)


def format_trade_reason_message(symbol: str, trade: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format the latest trade explanation for Discord."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))

    title = _text(language, "Latest trade reason", "最新交易原因", "Latest trade reason / 最新交易原因")
    action_label = _text(language, "Action", "動作", "Action / 動作")
    threshold_label = _text(language, "Thresholds", "觸發門檻", "Thresholds / 觸發門檻")
    reason_label = _text(language, "Reason", "原因", "Reason / 原因")

    action = _safe_text(trade.get("action")).upper()
    summary = _safe_text(trade.get("action_summary"))
    metadata = trade.get("metadata") if isinstance(trade.get("metadata"), dict) else {}
    reason = _safe_text(trade.get("explanation") or metadata.get("explanation"))
    thresholds = _safe_text(trade.get("threshold_summary"))
    validation_status = _safe_text(metadata.get("model_validation_status"), "unavailable")
    regime = metadata.get("market_regime") if isinstance(metadata.get("market_regime"), dict) else {}
    _, regime_text = _market_protection_text(regime, language)
    validation_text = {
        "validated": _text(language, "Validated model", "\u5df2\u9a57\u8b49\u6a21\u578b", "Validated model / \u5df2\u9a57\u8b49\u6a21\u578b"),
        "safety_fallback": _text(language, "Safety fallback", "\u5b89\u5168\u5f8c\u5099\u898f\u5247", "Safety fallback / \u5b89\u5168\u5f8c\u5099\u898f\u5247"),
        "user_requested_unvalidated": _text(language, "User-requested unvalidated model", "\u7528\u6236\u6307\u5b9a\u7684\u672a\u9a57\u8b49\u6a21\u578b", "User-requested unvalidated model / \u7528\u6236\u6307\u5b9a\u7684\u672a\u9a57\u8b49\u6a21\u578b"),
    }.get(validation_status, _text(language, "Verification unavailable", "\u672a\u6709\u9a57\u8b49\u8cc7\u6599", "Verification unavailable / \u672a\u6709\u9a57\u8b49\u8cc7\u6599"))

    if compact_mode:
        return (
            f"{title}: {symbol}\n"
            f"- {action_label}: {action}\n"
            f"- {reason_label}: {summary}\n"
            f"- Model check: {validation_text}"
        )

    return (
        f"{title}: {symbol}\n"
        f"- {action_label}: {action}\n"
        f"- Summary: {summary}\n"
        f"- {threshold_label}: {thresholds}\n"
        f"- {reason_label}: {reason}\n"
        f"- Model check: {validation_text}\n"
        f"- {_text(language, 'Wider-market protection', '整體市場保護', 'Wider-market protection / 整體市場保護')}: {regime_text}"
    )


def format_virtual_trader_compare_message(
    symbol: str,
    data: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    """Format the trader-versus-benchmark comparison for Discord."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    summary = data.get("summary", {})
    benchmark = data.get("benchmark_comparison", {})

    title = _text(language, "Trader vs VOO", "模擬交易對比 VOO", "Trader vs VOO / 模擬交易對比 VOO")
    trader_label = _text(language, "Trader equity", "模擬組合資產", "Trader equity / 模擬組合資產")
    benchmark_label = _text(language, "VOO equity", "VOO 資產", "VOO equity / VOO 資產")
    gap_label = _text(language, "Difference", "差距", "Difference / 差距")

    trader_equity = _format_money(summary.get("final_equity"))
    benchmark_equity = _format_money(benchmark.get("final_equity"))
    gap = _format_percent(summary.get("outperformance_vs_benchmark_pct_points"))

    if compact_mode:
        return (
            f"{title}: {symbol}\n"
            f"- {trader_label}: {trader_equity}\n"
            f"- {benchmark_label}: {benchmark_equity}\n"
            f"- {gap_label}: {gap}"
        )

    return (
        f"{title}: {symbol}\n"
        f"- {trader_label}: {trader_equity}\n"
        f"- {benchmark_label}: {benchmark_equity}\n"
        f"- {gap_label}: {gap}\n"
        f"- Contributions: {_format_money(summary.get('total_contributions'))}"
    )


def format_live_virtual_trader_status_message(
    symbol: str,
    data: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    """Format current live virtual trader status for Discord."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))
    account = data.get("account", {})
    decisions = _coerce_list(data.get("latest_decisions"))
    latest = decisions[0] if decisions else {}
    risk_label = _text(language, "Portfolio protection", "\u6295\u8cc7\u7d44\u5408\u4fdd\u8b77", "Portfolio protection / \u6295\u8cc7\u7d44\u5408\u4fdd\u8b77")
    risk_level = _safe_text(account.get("portfolio_risk_level"), "unavailable")
    risk_value = (
        _text(language, "New buys paused", "\u66ab\u505c\u65b0\u8cb7\u5165", "New buys paused / \u66ab\u505c\u65b0\u8cb7\u5165")
        if bool(account.get("buying_paused"))
        else _text(language, "Smaller positions", "\u7e2e\u5c0f\u5009\u4f4d", "Smaller positions / \u7e2e\u5c0f\u5009\u4f4d")
        if risk_level == "caution"
        else _text(language, "Normal", "\u6b63\u5e38", "Normal / \u6b63\u5e38")
        if risk_level == "normal"
        else _text(language, "Not enough account history", "\u5e33\u6236\u6b77\u53f2\u4e0d\u8db3", "Not enough account history / \u5e33\u6236\u6b77\u53f2\u4e0d\u8db3")
    )
    latest_metadata = latest.get("metadata") if isinstance(latest.get("metadata"), dict) else {}
    model_status = _safe_text(latest_metadata.get("model_validation_status"), "unavailable")
    model_check = {
        "validated": _text(language, "Validated model", "已驗證模型", "Validated model / 已驗證模型"),
        "safety_fallback": _text(language, "Safety fallback; no model passed every quality check", "安全後備規則；未有模型通過全部質量檢查", "Safety fallback; no model passed every quality check / 安全後備規則；未有模型通過全部質量檢查"),
        "user_requested_unvalidated": _text(language, "User-requested model; not quality-approved", "用戶指定模型；尚未通過質量審批", "User-requested model; not quality-approved / 用戶指定模型；尚未通過質量審批"),
    }.get(model_status, _text(language, "Verification unavailable", "未有驗證資料", "Verification unavailable / 未有驗證資料"))
    market_regime = latest_metadata.get("market_regime") if isinstance(latest_metadata.get("market_regime"), dict) else {}
    _, market_regime_text = _market_protection_text(market_regime, language)
    benchmark_shadow = (
        latest_metadata.get("benchmark_shadow")
        if isinstance(latest_metadata.get("benchmark_shadow"), dict)
        else {}
    )
    shadow_line = ""
    if benchmark_shadow.get("status") == "available":
        forward = (
            benchmark_shadow.get("forward_evidence")
            if isinstance(benchmark_shadow.get("forward_evidence"), dict)
            else {}
        )
        forward_samples = int(forward.get("sample_count") or 0)
        required_samples = int(forward.get("minimum_samples_for_promotion") or 20)
        if forward_samples > 0:
            forward_text = _text(
                language,
                f"{forward_samples} completed predictions, {_format_ratio(forward.get('direction_accuracy'))} correct",
                f"{forward_samples} 個已完成預測，{_format_ratio(forward.get('direction_accuracy'))} 正確",
            )
        else:
            forward_text = _text(
                language,
                f"collecting 0/{required_samples} completed predictions",
                f"正在收集已完成預測：0/{required_samples}",
            )
        benchmark_name = _safe_text(benchmark_shadow.get("benchmark"), "benchmark")
        shadow_signal = _text(
            language,
            f"may beat {benchmark_name} over the next five trading days",
            f"未來五個交易日可能跑贏 {benchmark_name}",
        ) if benchmark_shadow.get("signal") == "outperform" else _text(
            language,
            f"is not expected to beat {benchmark_name} over the next five trading days",
            f"預期未能在未來五個交易日跑贏 {benchmark_name}",
        )
        comparison_label = _text(language, "Research comparison", "研究比較", "Research comparison / 研究比較")
        probability_label = _text(language, "model estimate", "模型估計", "model estimate / 模型估計")
        forward_label = _text(language, "Real-time check", "實時檢查", "Real-time check / 實時檢查")
        warning = _text(
            language,
            "Research only—it cannot place trades and does not guarantee profit.",
            "只作研究用途；不會執行交易，亦不保證獲利。",
            "Research only—it cannot place trades and does not guarantee profit. / 只作研究用途；不會執行交易，亦不保證獲利。",
        )
        shadow_line = (
            f"\n- {comparison_label}: {shadow_signal}; "
            f"{probability_label} {_format_ratio(benchmark_shadow.get('outperform_probability'))}. "
            f"{forward_label}: {forward_text}. {warning}"
        )

    title = _text(language, "Live virtual trader", "即時虛擬交易", "Live virtual trader / 即時虛擬交易")
    action_label = _text(language, "Action", "動作", "Action / 動作")
    reason_label = _text(language, "Reason", "原因", "Reason / 原因")
    confidence_label = _text(language, "Confidence", "信心", "Confidence / 信心")

    if compact_mode:
        return (
            f"{title}: {symbol}\n"
            f"- Cash: {_format_money(account.get('cash'))}\n"
            f"- Equity: {_format_money(account.get('total_equity'))}\n"
            f"- {risk_label}: {risk_value}\n"
            f"- {action_label}: {_safe_text(latest.get('action'), 'N/A')}"
        )

    return (
        f"{title}: {symbol}\n"
        f"- Cash: {_format_money(account.get('cash'))}\n"
        f"- Holdings value: {_format_money(account.get('holdings_value'))}\n"
        f"- Total equity: {_format_money(account.get('total_equity'))}\n"
        f"- Realized PnL: {_format_money(account.get('realized_pnl'))}\n"
        f"- {risk_label}: {risk_value}\n"
        f"- {action_label}: {_safe_text(latest.get('action'), 'N/A')}\n"
        f"- {reason_label}: {_plain_decision_reason(latest.get('reason'), language)}\n"
        f"- {confidence_label}: {_format_ratio(latest.get('confidence_score'))} "
        f"({_text(language, 'signal support, not profit probability', '訊號支持程度，並非獲利機率', 'signal support, not profit probability / 訊號支持程度，並非獲利機率')})\n"
        f"- {_text(language, 'Wider-market protection', '整體市場保護', 'Wider-market protection / 整體市場保護')}: {market_regime_text}\n"
        f"- Model check: {model_check}"
        f"{shadow_line}"
    )


def format_live_virtual_trader_trades_message(
    symbol: str,
    data: dict[str, Any],
    settings: dict[str, Any],
    limit: int = 5,
) -> str:
    """Format latest live simulated trades for Discord."""
    language = settings.get("language", "zh")
    trades = _coerce_list(data.get("trades"))[: max(1, limit)]
    title = _text(language, "Live trades", "即時交易紀錄", "Live trades / 即時交易紀錄")
    if not trades:
        return f"{title}: {symbol}\n- {_text(language, 'No records yet.', '暫時未有紀錄。')}"

    lines = []
    for trade in trades:
        lines.append(
            f"- {_safe_text(trade.get('timestamp'))[:19]} | {_safe_text(trade.get('action')).upper()} | "
            f"{_format_price(trade.get('price'))} | {_safe_text(trade.get('reason'))}"
        )
    return f"{title}: {symbol}\n" + "\n".join(lines)


def format_virtual_account_summary_message(data: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format immutable virtual account summary for Discord."""
    language = settings.get("language", "zh")
    title = _text(language, "Virtual account", "虛擬帳戶", "Virtual account / 虛擬帳戶")
    return (
        f"{title}\n"
        f"- Cash: {_format_money(data.get('cash'))}\n"
        f"- Holdings value: {_format_money(data.get('holdings_value'))}\n"
        f"- Total value: {_format_money(data.get('total_account_value'))}\n"
        f"- Realized PnL: {_format_money(data.get('realized_pnl'))}\n"
        f"- Unrealized PnL: {_format_money(data.get('unrealized_pnl'))}\n"
        f"- Net deposits: {_format_money(data.get('net_deposits'))}"
    )


def format_virtual_account_ledger_message(
    data: dict[str, Any],
    settings: dict[str, Any],
    limit: int = 10,
) -> str:
    """Format recent immutable cash/trade ledger events for Discord."""
    language = settings.get("language", "zh")
    title = _text(language, "Recent ledger events", "最近分類帳事件", "Recent ledger events / 最近分類帳事件")
    events = _coerce_list(data.get("events"))[: max(1, limit)]
    if not events:
        return f"{title}\n- {_text(language, 'No records yet.', '目前沒有記錄。')}"
    lines = []
    for event in events:
        lines.append(
            f"- {_safe_text(event.get('created_at'))[:19]} | {_safe_text(event.get('event_type'))} | "
            f"{_format_money(event.get('amount'))} | {_safe_text(event.get('ticker'), '-')}"
        )
    return f"{title}\n" + "\n".join(lines)


def format_trader_scheduler_status_message(data: dict[str, Any], settings: dict[str, Any]) -> str:
    """Format scheduler status for Discord in a compact, readable way."""
    language = settings.get("language", "zh")
    compact_mode = bool(settings.get("compact_mode", False))

    title = _text(language, "Trader scheduler", "交易排程器", "Trader scheduler / 交易排程器")
    running_text = (
        _text(language, "Running", "執行中", "Running / 執行中")
        if bool(data.get("running"))
        else _text(language, "Idle", "待機中", "Idle / 待機中")
    )
    mode = str(data.get("mode", "market_closed"))
    mode_text = (
        _text(language, "Market Open (5 min)", "開市（5 分鐘）", "Market Open (5 min) / 開市（5 分鐘）")
        if mode == "market_open"
        else _text(language, "Market Closed (1 hour)", "休市（1 小時）", "Market Closed (1 hour) / 休市（1 小時）")
    )

    last_run = _safe_text(data.get("last_run_time_utc"), "N/A")
    next_run = _safe_text(data.get("next_run_time_utc"), "N/A")
    executed = _safe_text(data.get("last_decisions_executed"), "0")
    tickers_processed = _safe_text(data.get("last_tickers_processed"), "0")
    tickers_failed = _safe_text(data.get("last_tickers_failed"), "0")
    fallback_used = _safe_text(data.get("last_fallback_used"), "0")
    skipped = _safe_text(data.get("skipped_runs_total"), "0")

    if compact_mode:
        return (
            f"{title}\n"
            f"- {_text(language, 'Status', '狀態')}: {running_text}\n"
            f"- {_text(language, 'Mode', '模式')}: {mode_text}\n"
            f"- {_text(language, 'Next run', '下次執行')}: {next_run}"
        )

    return (
        f"{title}\n"
        f"- {_text(language, 'Status', '狀態')}: {running_text}\n"
        f"- {_text(language, 'Mode', '模式')}: {mode_text}\n"
        f"- {_text(language, 'Last run', '上次執行')}: {last_run}\n"
        f"- {_text(language, 'Next run', '下次執行')}: {next_run}\n"
        f"- {_text(language, 'Tickers processed', '已評估股票')}: {tickers_processed}\n"
        f"- {_text(language, 'Tickers failed', '失敗股票')}: {tickers_failed}\n"
        f"- {_text(language, 'Fallback used', '後備策略')}: {fallback_used}\n"
        f"- {_text(language, 'Trades executed', '執行交易')}: {executed}\n"
        f"- {_text(language, 'Skipped runs', '略過次數')}: {skipped}"
    )
