"""Rule-based natural-language intent parsing for the Discord bot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from .ticker_map import TickerMatch, extract_tickers_from_text
except ImportError:  # pragma: no cover - script execution fallback
    from ticker_map import TickerMatch, extract_tickers_from_text


@dataclass(frozen=True)
class ParsedIntent:
    """Structured result from the natural-language parser."""

    intent: str | None
    language: str | None = None
    compact_mode: bool | None = None
    amount: float | None = None
    tickers: list[str] = field(default_factory=list)
    needs_help_hint: bool = False
    message: str | None = None


_LANGUAGE_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "bilingual",
        [
            "bilingual",
            "english and chinese",
            "english chinese",
            "both languages",
            "bilingual replies",
        ],
    ),
    ("zh", ["chinese", "traditional chinese", "中文", "廣東話"]),
    ("en", ["english", "english only", "英文"]),
]

_GENERAL_HELP_HINT = (
    "I'm not sure what you want to do yet. Try `show my settings`, `analyze VOO`, "
    "`forecast QQQ`, `model status VOO`, or `add Tesla to my watchlist`."
)


def _normalize_text(text: str) -> str:
    """Normalize free text so phrase matching stays simple and predictable."""
    lowered = str(text).strip().lower()
    return re.sub(r"\s+", " ", lowered)


def _contains_any(text: str, phrases: list[str]) -> bool:
    """Return True when any phrase is present in the text."""
    return any(phrase in text for phrase in phrases)


def _detect_language(text: str) -> str | None:
    """Map plain-language language requests into supported reply modes."""
    for language, phrases in _LANGUAGE_PATTERNS:
        if _contains_any(text, phrases):
            return language
    return None


def _contains_stock_keywords(text: str) -> bool:
    """Decide whether a message looks related to the stock bot at all."""
    keywords = [
        "language",
        "settings",
        "watchlist",
        "ticker",
        "stock",
        "analysis",
        "analyze",
        "forecast",
        "outlook",
        "predict",
        "compact",
        "short replies",
        "model",
        "accuracy",
        "virtual trader",
        "run trader",
        "trader status",
        "scheduler",
        "last run",
        "next run",
        "trades",
        "account",
        "cash balance",
        "holdings",
        "cash ledger",
        "buy or sell",
    ]
    return _contains_any(text, keywords)


def _parse_add_remove_tickers(text: str) -> TickerMatch:
    """Extract ticker candidates from add/remove watchlist requests."""
    cleaned = re.sub(r"\b(to my watchlist|from my watchlist|in my watchlist|my watchlist)\b", " ", text)
    cleaned = re.sub(r"\b(add|put|include|remove|delete)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return extract_tickers_from_text(cleaned)


def _ticker_help(action: str) -> str:
    """Return a short help hint when ticker extraction fails."""
    if action == "add":
        return (
            "I couldn't tell which ticker you want to add. Try `add TSLA to my watchlist` "
            "or `add Apple to my watchlist`."
        )
    if action == "remove":
        return "I couldn't tell which ticker you want to remove. Try `remove TSLA from my watchlist`."
    if action == "forecast":
        return "I need a ticker for the forecast, for example `forecast VOO` or `show me the forecast for Microsoft`."
    if action == "model_status":
        return "I need a ticker for model status, for example `model status VOO`."
    if action == "model_accuracy":
        return "I need a ticker for model accuracy, for example `show prediction accuracy for VOO`."
    return "I need a ticker to analyze, for example `analyze VOO` or `analyze Apple`."


def parse_natural_language_message(message_text: str) -> ParsedIntent:
    """Parse supported natural-language stock bot requests.

    The parser is intentionally simple and transparent:
    - explicit commands still take priority elsewhere
    - only clear, rule-based phrases are matched
    - when a stock-bot request is detected but unclear, a help hint is returned
    """

    text = _normalize_text(message_text)
    if not text:
        return ParsedIntent(intent=None)

    amount_match = re.search(r"\$?([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text)
    amount = float(amount_match.group(1).replace(",", "")) if amount_match else None
    if _contains_any(
        text,
        ["set monthly contribution", "set my monthly contribution", "monthly deposit"],
    ):
        if amount is not None:
            return ParsedIntent(intent="set_monthly_virtual_cash", amount=amount)
        return ParsedIntent(
            intent=None,
            needs_help_hint=True,
            message="Please include an amount, for example `set monthly contribution to 500`.",
        )
    if text.startswith(("deposit ", "add virtual cash ", "add cash ")):
        if amount is not None:
            return ParsedIntent(intent="virtual_cash_deposit", amount=amount)
        return ParsedIntent(
            intent=None,
            needs_help_hint=True,
            message="Please include an amount, for example `deposit 1000`.",
        )
    if text.startswith(("withdraw ", "withdraw virtual cash ")):
        if amount is not None:
            return ParsedIntent(intent="virtual_cash_withdraw", amount=amount)
        return ParsedIntent(
            intent=None,
            needs_help_hint=True,
            message="Please include an amount, for example `withdraw 100`.",
        )

    if _contains_any(
        text,
        ["show my settings", "what are my settings", "what language am i using", "show settings", "my settings"],
    ):
        return ParsedIntent(intent="show_settings")

    if _contains_any(
        text,
        [
            "set my language",
            "change language",
            "reply in",
            "use bilingual mode",
            "use bilingual replies",
            "speak in",
        ],
    ):
        language = _detect_language(text)
        if language:
            return ParsedIntent(intent="set_language", language=language)
        return ParsedIntent(
            intent=None,
            needs_help_hint=True,
            message="I can change language to `en`, `zh`, or `bilingual`. Try `set my language to Chinese`.",
        )

    if _contains_any(text, ["compact mode", "short replies", "compact replies"]):
        if _contains_any(text, ["turn off", "disable", "stop using"]):
            return ParsedIntent(intent="set_compact", compact_mode=False)
        if _contains_any(text, ["turn on", "enable", "use", "set", "short replies"]):
            return ParsedIntent(intent="set_compact", compact_mode=True)
        return ParsedIntent(
            intent=None,
            needs_help_hint=True,
            message="You can say `turn on compact mode`, `use short replies`, or `disable compact mode`.",
        )

    if _contains_any(
        text,
        ["show my watchlist", "what is in my watchlist", "what's in my watchlist", "show watchlist"],
    ):
        if not _contains_any(text, ["add ", "remove ", "delete ", "put ", "include "]):
            return ParsedIntent(intent="show_watchlist")

    if (_contains_any(text, ["add ", "put ", "include "]) and "watchlist" in text) or re.fullmatch(
        r"add [a-z0-9 .,&-]+",
        text,
    ):
        ticker_match = _parse_add_remove_tickers(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="add_watchlist", tickers=ticker_match.tickers)
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("add"))

    if (_contains_any(text, ["remove ", "delete "]) and "watchlist" in text) or re.fullmatch(
        r"(remove|delete) [a-z0-9 .,&-]+",
        text,
    ):
        ticker_match = _parse_add_remove_tickers(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="remove_watchlist", tickers=ticker_match.tickers)
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("remove"))

    if _contains_any(text, ["forecast ", "outlook for", "predict ", "show me the forecast for"]):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="forecast", tickers=ticker_match.tickers[:1])
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("forecast"))

    if _contains_any(
        text,
        [
            "shadow model status",
            "forward model status",
            "model promotion status",
            "live model evidence",
            "is the model ready",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="benchmark_shadow", tickers=ticker_match.tickers[:1])
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("model_status"))

    if _contains_any(
        text,
        [
            "model status",
            "latest model signal",
            "latest prediction for",
            "show model status for",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="model_status", tickers=ticker_match.tickers[:1])
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("model_status"))

    if _contains_any(
        text,
        [
            "prediction accuracy",
            "model accuracy",
            "how accurate is the model",
            "show accuracy for",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="model_accuracy", tickers=ticker_match.tickers[:1])
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("model_accuracy"))

    if _contains_any(
        text,
        [
            "show trader status",
            "trader scheduler status",
            "scheduler status",
            "show scheduler status",
        ],
    ):
        return ParsedIntent(intent="trader_scheduler_status")

    if _contains_any(
        text,
        [
            "show last run",
            "last scheduler run",
            "when did trader run last",
        ],
    ):
        return ParsedIntent(intent="trader_scheduler_last_run")

    if _contains_any(
        text,
        [
            "show next run",
            "next scheduler run",
            "when is next trader run",
        ],
    ):
        return ParsedIntent(intent="trader_scheduler_next_run")

    if _contains_any(
        text,
        [
            "run trader now",
            "run virtual trader now",
            "execute trader now",
            "simulate now",
            "show virtual trader summary",
            "virtual trader summary",
            "virtual trader status",
            "show trader summary",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if _contains_any(text, ["run trader now", "run virtual trader now", "execute trader now", "simulate now"]):
            return ParsedIntent(intent="run_virtual_trader_now", tickers=ticker_match.tickers[:1])
        return ParsedIntent(intent="virtual_trader_summary", tickers=ticker_match.tickers[:1])

    if _contains_any(
        text,
        [
            "show last 5 trades",
            "last 5 trades",
            "recent trades",
            "recent virtual trader trades",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        return ParsedIntent(intent="virtual_trader_trades", tickers=ticker_match.tickers[:1])

    if _contains_any(
        text,
        [
            "why did the model buy or sell",
            "why did the model buy",
            "why did the model sell",
            "why did it buy",
            "why did it sell",
            "why trade",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        return ParsedIntent(intent="why_trade", tickers=ticker_match.tickers[:1])

    if _contains_any(
        text,
        [
            "compare virtual trader vs",
            "compare trader vs",
            "virtual trader versus",
            "how is the virtual trader doing versus",
            "compare strategy against",
        ],
    ):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        return ParsedIntent(intent="virtual_trader_compare", tickers=ticker_match.tickers[:1])

    if _contains_any(
        text,
        [
            "show virtual trader account",
            "show account",
            "virtual account",
            "show cash balance",
            "show holdings",
        ],
    ):
        return ParsedIntent(intent="virtual_account_summary")

    if _contains_any(
        text,
        [
            "show last cash deposits",
            "show last deposits",
            "show withdrawals",
            "show cash ledger",
            "ledger events",
        ],
    ):
        return ParsedIntent(intent="virtual_account_ledger")

    if _contains_any(text, ["analyze ", "analysis for", "check ", "what do you think about", "show analysis for"]):
        ticker_match = extract_tickers_from_text(text)
        if ticker_match.ambiguous:
            return ParsedIntent(intent=None, needs_help_hint=True, message=ticker_match.message)
        if ticker_match.tickers:
            return ParsedIntent(intent="analyze", tickers=ticker_match.tickers[:1])
        return ParsedIntent(intent=None, needs_help_hint=True, message=_ticker_help("analyze"))

    if _contains_stock_keywords(text):
        return ParsedIntent(intent=None, needs_help_hint=True, message=_GENERAL_HELP_HINT)

    return ParsedIntent(intent=None)
