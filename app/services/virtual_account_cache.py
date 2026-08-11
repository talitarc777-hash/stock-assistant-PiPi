"""Shared in-process cache helpers for virtual-account read endpoints.

This module centralizes cache invalidation so write paths (scheduler/manual runs
and API mutations) can consistently clear stale account views.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.ttl_cache import TTLCache

_SUMMARY_CACHE: TTLCache[dict[str, Any]] = TTLCache(max_items=300)
_HOLDINGS_CACHE: TTLCache[list[dict[str, Any]]] = TTLCache(max_items=300)
_EQUITY_CURVE_CACHE: TTLCache[dict[str, Any]] = TTLCache(max_items=200)


def _summary_key(user_id: str, market: str = "US") -> str:
    return f"summary:{str(user_id).strip()}:{str(market).strip().upper()}"


def _holdings_key(user_id: str, market: str = "US") -> str:
    return f"holdings:{str(user_id).strip()}:{str(market).strip().upper()}"


def _equity_curve_key(user_id: str, limit: int, market: str = "US") -> str:
    return f"equity:{str(user_id).strip()}:{str(market).strip().upper()}:{int(limit)}"


def clear_user_virtual_account_cache(user_id: str) -> None:
    """Clear all cached virtual-account read views for one user."""
    clean_user_id = str(user_id).strip()
    _SUMMARY_CACHE.invalidate_prefix(f"summary:{clean_user_id}:")
    _HOLDINGS_CACHE.invalidate_prefix(f"holdings:{clean_user_id}:")
    _EQUITY_CURVE_CACHE.invalidate_prefix(f"equity:{clean_user_id}:")


def get_cached_summary(
    user_id: str,
    loader: Callable[[], dict[str, Any]],
    *,
    ttl_seconds: float = 8.0,
    market: str = "US",
) -> dict[str, Any]:
    key = _summary_key(user_id, market)
    cached = _SUMMARY_CACHE.get(key)
    if cached is not None:
        return cached
    payload = loader()
    _SUMMARY_CACHE.set(key, payload, ttl_seconds=ttl_seconds)
    return payload


def get_cached_holdings(
    user_id: str,
    loader: Callable[[], list[dict[str, Any]]],
    *,
    ttl_seconds: float = 8.0,
    market: str = "US",
) -> list[dict[str, Any]]:
    key = _holdings_key(user_id, market)
    cached = _HOLDINGS_CACHE.get(key)
    if cached is not None:
        return cached
    payload = loader()
    _HOLDINGS_CACHE.set(key, payload, ttl_seconds=ttl_seconds)
    return payload


def get_cached_equity_curve(
    user_id: str,
    limit: int,
    loader: Callable[[], dict[str, Any]],
    *,
    ttl_seconds: float = 10.0,
    market: str = "US",
) -> dict[str, Any]:
    key = _equity_curve_key(user_id, limit, market)
    cached = _EQUITY_CURVE_CACHE.get(key)
    if cached is not None:
        return cached
    payload = loader()
    _EQUITY_CURVE_CACHE.set(key, payload, ttl_seconds=ttl_seconds)
    return payload

