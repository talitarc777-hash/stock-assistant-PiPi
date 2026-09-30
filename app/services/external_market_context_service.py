"""Optional external context feeds for virtual-trader decisions.

These feeds are intentionally best-effort:
- missing API keys never block the trader
- network errors are recorded as provider notes
- every output is numeric and explainable for the decision audit trail
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import math
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yfinance as yf

from app.core.settings import get_settings
from app.services.news_sentiment import LexiconSentimentScorer

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CIK_CACHE: tuple[float, dict[str, str]] | None = None
_CACHE_TTL_SECONDS = 60 * 60
_SEC_CIK_TTL_SECONDS = 24 * 60 * 60
_SCORER = LexiconSentimentScorer()

SOURCE_FIELDS = {
    'yfinance_news_lexicon_v1': ['news_sentiment_score'],
    'yfinance_analyst_consensus': ['analyst_consensus_score', 'analyst_revision_score'],
    'reddit_social_search': ['social_mention_count'],
    'alpha_vantage_news_sentiment': ['alpha_news_sentiment_score'],
    'alpha_vantage_earnings_transcript': ['earnings_call_tone_score'],
    'sec_edgar_filings': ['official_event_count'],
}


def source_configuration():
    settings = get_settings()
    sec_ready = '@' in settings.sec_user_agent and 'example.com' not in settings.sec_user_agent
    return {
        'yfinance_news_lexicon_v1': {'configured': True, 'markets': ['US', 'HK'], 'note': 'entity-matched headlines; lexicon quality is unvalidated'},
        'yfinance_analyst_consensus': {'configured': True, 'markets': ['US', 'HK'], 'note': 'coverage varies by ticker'},
        'reddit_social_search': {'configured': settings.reddit_context_enabled, 'markets': ['US', 'HK'], 'note': 'public endpoint may block requests; ambiguous symbols excluded'},
        'alpha_vantage_news_sentiment': {'configured': bool(settings.alpha_vantage_api_key), 'markets': ['US'], 'note': 'key, quota and entitlement required'},
        'alpha_vantage_earnings_transcript': {'configured': bool(settings.alpha_vantage_api_key), 'markets': ['US'], 'note': 'key, quota and entitlement required; latest quarter only'},
        'sec_edgar_filings': {'configured': settings.sec_context_enabled and sec_ready, 'markets': ['US'], 'note': 'real contact User-Agent required; no HK regulatory adapter'},
    }


def source_statuses(context):
    available = context.get('sources_available') or []
    overrides = context.get('source_status') or {}
    settings = get_settings()
    result = {}
    for source, fields in SOURCE_FIELDS.items():
        status = overrides.get(source) or ('usable' if source in available and any(context.get(f) is not None for f in fields) else 'unavailable')
        if str(context.get('ticker', '')).endswith('.HK') and source.startswith(('alpha_', 'sec_')):
            status = 'unsupported_market'
        elif source == 'reddit_social_search' and not settings.reddit_context_enabled:
            status = 'disabled'
        elif source == 'sec_edgar_filings' and not settings.sec_context_enabled:
            status = 'disabled'
        elif source.startswith('alpha_') and not settings.alpha_vantage_api_key:
            status = 'missing_credentials'
        elif source == 'sec_edgar_filings' and ('@' not in settings.sec_user_agent or 'example.com' in settings.sec_user_agent):
            status = 'missing_credentials'
        result[source] = status
    return result


def _entity_matches(text, ticker, company_name=None):
    # Numeric HK codes and one-letter US symbols in prose are ambiguous.
    if company_name:
        # Remove legal suffixes only from the provider-verified entity name, not
        # an arbitrary ticker mapping or a guessed first word.
        name = re.sub(r'\b(incorporated|inc|corp|corporation|limited|ltd|holdings|holding|plc)\b[.,]?', '', company_name, flags=re.I).strip(' .,')
        if len(name) >= 4 and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I):
            return True
    if ticker.endswith('.HK'):
        return bool(re.search(r'(?<![\w.])' + re.escape(ticker) + r'(?!\w)', text, re.I))
    pattern = r'(?<!\w)\$' + re.escape(ticker) + r'(?!\w)' if len(ticker) <= 2 else r'(?<!\w)' + re.escape(ticker) + r'(?!\w)'
    return bool(re.search(pattern, text, re.I))


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _average(values: list[float]) -> float | None:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _score_texts(texts: list[str]) -> float | None:
    clean = [text for text in texts if str(text or "").strip()]
    if not clean:
        return None
    return _average(_SCORER.score_texts(clean))


def _fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 2.5) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "StockAssistantPiPi/1.0",
            **(headers or {}),
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _empty_context(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "fetched_at_utc": _utc_now(),
        "social_sentiment_score": None,
        "social_mention_count": 0,
        "social_engagement_score": None,
        "analyst_revision_score": None,
        "analyst_event_count": 0,
        "analyst_consensus_score": None,
        "official_regulatory_risk_score": None,
        "official_event_count": 0,
        "recent_official_events": [],
        "earnings_call_tone_score": None,
        "earnings_call_available": False,
        "alpha_news_sentiment_score": None,
        "sources_available": [],
        "missing_sources": [],
        "provider_notes": [],
    }


def _append_source(context: dict[str, Any], source: str) -> None:
    if source not in context["sources_available"]:
        context["sources_available"].append(source)


def _append_missing(context: dict[str, Any], source: str) -> None:
    if source not in context["missing_sources"]:
        context["missing_sources"].append(source)


def _append_note(context: dict[str, Any], note: str) -> None:
    # Provider exceptions can contain URLs with API keys. Never return them.
    context["provider_notes"].append(note.split(':', 1)[0][:120])


def _merge_average(existing: float | None, new_value: float | None) -> float | None:
    if existing is None:
        return new_value
    if new_value is None:
        return existing
    return (existing + new_value) / 2


def _add_reddit_context(
    context: dict[str, Any],
    *,
    ticker: str,
    company_name: str | None,
    timeout: float,
) -> None:
    settings = get_settings()
    if not settings.reddit_context_enabled:
        _append_missing(context, "reddit_social_search")
        return

    query_parts = [ticker]
    if company_name:
        query_parts.append(f'"{company_name}"')
    query = " OR ".join(query_parts)
    subreddit_prefix = "+".join(settings.reddit_context_subreddits)
    path = f"https://www.reddit.com/r/{subreddit_prefix}/search.json"
    url = f"{path}?{urlencode({'q': query, 'restrict_sr': 'on', 'sort': 'new', 't': 'week', 'limit': settings.reddit_context_limit})}"

    try:
        payload = _fetch_json(url, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        _append_missing(context, "reddit_social_search")
        _append_note(context, f"Reddit social feed unavailable: {exc}")
        return

    posts = payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []
    texts: list[str] = []
    engagement = 0.0
    for item in posts:
        data = item.get("data", {}) if isinstance(item, dict) else {}
        title = str(data.get("title") or "")
        body = str(data.get("selftext") or "")
        if not _entity_matches(title + ' ' + body, ticker, company_name):
            continue
        if title or body:
            texts.append(f"{title}. {body[:500]}")
        engagement += max(0.0, float(data.get("score") or 0.0))
        engagement += max(0.0, float(data.get("num_comments") or 0.0))

    if not texts:
        _append_missing(context, "reddit_social_search")
        context.setdefault('source_status', {})['reddit_social_search'] = 'empty'
        _append_note(context, "Reddit social feed returned no matching posts.")
        return

    context["social_sentiment_score"] = _score_texts(texts)
    context["social_mention_count"] = len(texts)
    context["social_engagement_score"] = min(100.0, math.log10(engagement + 1.0) * 25.0)
    _append_source(context, "reddit_social_search")


def _latest_recommendation_score(row: Any) -> float | None:
    strong_buy = _safe_float(row.get("strongBuy")) or 0.0
    buy = _safe_float(row.get("buy")) or 0.0
    hold = _safe_float(row.get("hold")) or 0.0
    sell = _safe_float(row.get("sell")) or 0.0
    strong_sell = _safe_float(row.get("strongSell")) or 0.0
    total = strong_buy + buy + hold + sell + strong_sell
    if total <= 0:
        return None
    return ((strong_buy * 1.0) + (buy * 0.5) - (sell * 0.5) - (strong_sell * 1.0)) / total


def _add_yfinance_analyst_context(context: dict[str, Any], *, ticker: str) -> None:
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info or {}
    except Exception as exc:  # pragma: no cover - upstream availability varies
        _append_missing(context, "yfinance_analyst_consensus")
        _append_note(context, f"yfinance analyst info unavailable: {exc}")
        return

    recommendation_mean = _safe_float(info.get("recommendationMean"))
    reported = str(info.get('symbol') or ticker).upper()
    from app.services.market_config import resolve_security
    market = 'HK' if ticker.endswith('.HK') else 'US'
    if resolve_security(reported, market).ticker != resolve_security(ticker, market).ticker:
        context.setdefault('source_status', {})['yfinance_analyst_consensus'] = 'unverified_entity'
        return
    context['verified_company_name'] = str(info.get('shortName') or info.get('longName') or '')
    recommendation_key = str(info.get("recommendationKey") or "").lower()
    consensus_score = None
    if recommendation_mean is not None:
        consensus_score = max(-1.0, min(1.0, (3.0 - recommendation_mean) / 2.0))
    elif recommendation_key:
        consensus_score = {
            "strong_buy": 1.0,
            "buy": 0.6,
            "hold": 0.0,
            "underperform": -0.6,
            "sell": -1.0,
        }.get(recommendation_key)

    event_scores: list[float] = []
    try:
        recommendations = ticker_obj.recommendations
        if recommendations is not None and not recommendations.empty:
            latest = recommendations.tail(1).iloc[0]
            score = _latest_recommendation_score(latest)
            if score is not None:
                event_scores.append(score)
    except Exception as exc:  # pragma: no cover - upstream availability varies
        _append_note(context, f"yfinance recommendation table unavailable: {exc}")

    try:
        upgrades = ticker_obj.upgrades_downgrades
        if upgrades is not None and not upgrades.empty:
            recent = upgrades.tail(20)
            text_columns = [
                column
                for column in recent.columns
                if str(column).lower() in {"action", "tograde", "fromgrade", "firm"}
            ]
            for _, row in recent.iterrows():
                text = " ".join(str(row.get(column) or "").lower() for column in text_columns)
                if "up" in text or "buy" in text or "outperform" in text:
                    event_scores.append(0.75)
                elif "down" in text or "sell" in text or "underperform" in text:
                    event_scores.append(-0.75)
    except Exception as exc:  # pragma: no cover - upstream availability varies
        _append_note(context, f"yfinance upgrades/downgrades unavailable: {exc}")

    if consensus_score is None and not event_scores:
        _append_missing(context, "yfinance_analyst_consensus")
        return

    context["analyst_consensus_score"] = consensus_score
    context["analyst_revision_score"] = _average(event_scores)
    context["analyst_event_count"] = len(event_scores)
    _append_source(context, "yfinance_analyst_consensus")


def _add_yfinance_news_context(context: dict[str, Any], *, ticker: str) -> None:
    """Archive a current headline aggregate; never pretend it is historical news."""
    try:
        articles = yf.Ticker(ticker).news or []
        titles = []
        for item in articles[:30]:
            content = item.get("content", item)
            title = content.get("title")
            related = item.get('relatedTickers') or content.get('relatedTickers') or []
            if not related:
                related = [t.get('symbol') for t in (content.get('finance') or {}).get('stockTickers', []) if isinstance(t, dict)]
            if related:
                if ticker not in [str(t).upper() for t in related]:
                    continue
            elif not _entity_matches(str(title or ''), ticker, context.get('verified_company_name')):
                continue
            if isinstance(title, str) and title.strip():
                titles.append(title)
        context["news_article_count"] = len(titles)
        context["news_sentiment_score"] = _score_texts(titles)
        if titles:
            _append_source(context, "yfinance_news_lexicon_v1")
        else:
            _append_missing(context, "yfinance_news_lexicon_v1")
            context.setdefault('source_status', {})['yfinance_news_lexicon_v1'] = 'empty'
    except Exception:
        _append_missing(context, "yfinance_news_lexicon_v1")


def _iter_recent_quarters(max_quarters: int = 6) -> list[str]:
    now = datetime.now(UTC)
    quarter = ((now.month - 1) // 3) + 1
    year = now.year
    values: list[str] = []
    for _ in range(max_quarters):
        values.append(f"{year}Q{quarter}")
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return values


def _add_alpha_vantage_context(context: dict[str, Any], *, ticker: str, timeout: float) -> None:
    settings = get_settings()
    if not settings.alpha_vantage_api_key:
        _append_missing(context, "alpha_vantage_news_and_earnings")
        context.setdefault('source_status', {}).update({s: 'missing_credentials' for s in SOURCE_FIELDS if s.startswith('alpha_')})
        return

    from app.services.learning_operations import LearningOperations
    budget = LearningOperations()
    if not budget.spend_budget('alpha_vantage', settings.alpha_vantage_daily_budget):
        context.setdefault('source_status', {}).update({s: 'budget_exhausted' for s in SOURCE_FIELDS if s.startswith('alpha_')})
        return

    base_url = "https://www.alphavantage.co/query"
    common = {"apikey": settings.alpha_vantage_api_key}
    try:
        news_url = f"{base_url}?{urlencode({'function': 'NEWS_SENTIMENT', 'tickers': ticker, 'limit': 50, **common})}"
        news_payload = _fetch_json(news_url, timeout=timeout)
        if isinstance(news_payload, dict) and (news_payload.get('Information') or news_payload.get('Note')):
            context.setdefault('source_status', {}).update({s: 'rate_limited' for s in SOURCE_FIELDS if s.startswith('alpha_')})
            return
        feed = news_payload.get("feed", []) if isinstance(news_payload, dict) else []
        sentiment_scores: list[float] = []
        for article in feed:
            for item in article.get("ticker_sentiment", []) if isinstance(article, dict) else []:
                if str(item.get("ticker") or "").upper() == ticker:
                    score = _safe_float(item.get("ticker_sentiment_score"))
                    if score is not None:
                        sentiment_scores.append(score)
        if sentiment_scores:
            context["alpha_news_sentiment_score"] = _average(sentiment_scores)
            _append_source(context, "alpha_vantage_news_sentiment")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        _append_note(context, f"Alpha Vantage news sentiment unavailable: {exc}")

    for quarter in _iter_recent_quarters(max_quarters=2)[1:]:
        if not budget.spend_budget('alpha_vantage', settings.alpha_vantage_daily_budget):
            context.setdefault('source_status', {})['alpha_vantage_earnings_transcript'] = 'budget_exhausted'
            return
        try:
            transcript_url = f"{base_url}?{urlencode({'function': 'EARNINGS_CALL_TRANSCRIPT', 'symbol': ticker, 'quarter': quarter, **common})}"
            payload = _fetch_json(transcript_url, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            _append_note(context, f"Alpha Vantage transcript unavailable for {quarter}: {exc}")
            continue

        if not isinstance(payload, dict) or payload.get("Information") or payload.get("Error Message"):
            continue
        transcript = payload.get("transcript") or payload.get("transcript_chunks") or []
        scores: list[float] = []
        texts: list[str] = []
        if isinstance(transcript, list):
            for turn in transcript:
                if not isinstance(turn, dict):
                    continue
                score = _safe_float(turn.get("sentiment_score") or turn.get("overall_sentiment_score"))
                if score is not None:
                    scores.append(score)
                text = str(turn.get("content") or turn.get("text") or "")
                if text:
                    texts.append(text[:800])
        elif isinstance(transcript, str):
            texts.append(transcript[:4000])

        context["earnings_call_tone_score"] = _average(scores) if scores else _score_texts(texts)
        context["earnings_call_available"] = context["earnings_call_tone_score"] is not None
        if context["earnings_call_available"]:
            _append_source(context, "alpha_vantage_earnings_transcript")
            return

    if not context["earnings_call_available"]:
        _append_missing(context, "alpha_vantage_earnings_transcript")


def _load_sec_cik_map(timeout: float, user_agent: str) -> dict[str, str]:
    global _CIK_CACHE
    now = time.time()
    if _CIK_CACHE and _CIK_CACHE[0] > now:
        return _CIK_CACHE[1]

    payload = _fetch_json(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": user_agent},
        timeout=timeout,
    )
    mapping: dict[str, str] = {}
    if isinstance(payload, dict):
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").upper()
            cik = str(item.get("cik_str") or "").zfill(10)
            if ticker and cik:
                mapping[ticker] = cik
    _CIK_CACHE = (now + _SEC_CIK_TTL_SECONDS, mapping)
    return mapping


def _add_sec_context(context: dict[str, Any], *, ticker: str, timeout: float) -> None:
    settings = get_settings()
    if not settings.sec_context_enabled:
        _append_missing(context, "sec_edgar_filings")
        return
    if '@' not in settings.sec_user_agent or 'example.com' in settings.sec_user_agent:
        context.setdefault('source_status', {})['sec_edgar_filings'] = 'missing_credentials'
        return

    try:
        cik_map = _load_sec_cik_map(timeout=timeout, user_agent=settings.sec_user_agent)
        cik = cik_map.get(ticker) or cik_map.get(ticker.replace('.', '-'))
        if not cik:
            _append_missing(context, "sec_edgar_filings")
            _append_note(context, "SEC CIK mapping not found for ticker.")
            return
        payload = _fetch_json(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": settings.sec_user_agent},
            timeout=timeout,
        )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        _append_missing(context, "sec_edgar_filings")
        _append_note(context, f"SEC EDGAR feed unavailable: {exc}")
        return

    recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload, dict) else {}
    forms = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    accessions = recent.get("accessionNumber", []) or []
    risk_forms = {"8-K", "8-K/A", "10-K/A", "10-Q/A", "NT 10-K", "NT 10-Q", "SC 13D", "SC 13G", "DEFA14A"}
    events: list[dict[str, Any]] = []
    risk_count = 0
    for form, filing_date, accession in list(zip(forms, dates, accessions))[:20]:
        form_text = str(form or "")
        event = {
            "form": form_text,
            "filing_date": str(filing_date or ""),
            "accession": str(accession or ""),
        }
        events.append(event)
        if form_text in risk_forms:
            risk_count += 1

    context["official_event_count"] = len(events)
    context["recent_official_events"] = events[:5]
    context["official_regulatory_risk_score"] = min(100.0, float(risk_count * 18))
    _append_source(context, "sec_edgar_filings")


def build_external_market_context(ticker: str, company_name: str | None = None) -> dict[str, Any]:
    """Build optional external context for one ticker."""
    symbol = str(ticker).strip().upper()
    if not symbol:
        raise ValueError("ticker is required.")
    from app.services.market_config import resolve_security
    identity = resolve_security(symbol, 'HK' if symbol.endswith('.HK') or symbol.isdigit() else 'US')
    symbol = identity.provider_symbol

    settings = get_settings()
    if not settings.external_context_enabled:
        context = _empty_context(symbol)
        context["missing_sources"] = ["external_context_disabled"]
        return context

    cache_key = symbol
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > now:
        return dict(cached[1])
    if settings.context_archive_enabled:
        try:
            from app.services.context_observation_store import get_context_observation_store, utc_time
            saved = get_context_observation_store().as_of(symbol, identity.market, datetime.now(UTC))
            if saved and (datetime.now(UTC) - utc_time(saved['available_at_utc'])).total_seconds() < _CACHE_TTL_SECONDS:
                context = {**_empty_context(symbol), **saved['features'], 'sources_available': saved['sources'],
                           'missing_sources': saved['missing_sources'], 'fetched_at_utc': saved['source_observed_at_utc'],
                           'observation_id': saved['id']}
                context['source_status'] = source_statuses(context)
                return context
        except Exception:
            logger.warning('Persistent context cache unavailable ticker=%s', symbol)

    context = _empty_context(symbol)
    timeout = max(0.5, float(settings.external_context_timeout_seconds))

    _add_yfinance_analyst_context(context, ticker=symbol)
    if settings.context_archive_enabled:
        _add_yfinance_news_context(context, ticker=symbol)
    _add_reddit_context(context, ticker=symbol, company_name=context.get('verified_company_name'), timeout=timeout)
    if identity.market == 'US':
        _add_alpha_vantage_context(context, ticker=symbol, timeout=timeout)
        _add_sec_context(context, ticker=symbol, timeout=timeout)
    else:
        context.setdefault('source_status', {}).update({s: 'unsupported_market' for s in SOURCE_FIELDS if s.startswith(('alpha_', 'sec_'))})

    if context["alpha_news_sentiment_score"] is not None:
        context["social_sentiment_score"] = _merge_average(
            context["social_sentiment_score"],
            context["alpha_news_sentiment_score"],
        )

    # Timestamp after collection: none of these snapshot features were available
    # to this process at the publication date of an older article or filing.
    context["fetched_at_utc"] = _utc_now()
    context['source_status'] = source_statuses(context)
    for source, state in context['source_status'].items():
        if state != 'usable':
            _append_missing(context, source)
    if settings.context_archive_enabled:
        try:
            from app.services.context_observation_store import get_context_observation_store
            market = "HK" if symbol.endswith(".HK") else "US"
            context["observation_id"] = get_context_observation_store().record(symbol, market, context)
        except Exception:
            logger.exception("Context archive write failed ticker=%s", symbol)
    _CACHE[cache_key] = (now + _CACHE_TTL_SECONDS, dict(context))
    logger.info(
        "External context ticker=%s sources=%s missing=%s",
        symbol,
        ",".join(context["sources_available"]),
        ",".join(context["missing_sources"]),
    )
    return context
