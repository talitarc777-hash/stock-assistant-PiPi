"""Thin client for unified user profile/watchlist/alert backend endpoints."""

from __future__ import annotations

import requests

try:
    from .config import BACKEND_BASE_URL
    from .stock_api_client import (
        ApiClientError,
        BackendTimeoutError,
        BackendUnavailableError,
    )
except ImportError:  # pragma: no cover - script execution fallback
    from config import BACKEND_BASE_URL
    from stock_api_client import (
        ApiClientError,
        BackendTimeoutError,
        BackendUnavailableError,
    )


def _request_json(method: str, path: str, payload: dict | None = None):
    """Perform a backend request with the same error style as the stock API client."""
    url = f"{BACKEND_BASE_URL}{path}"
    try:
        response = requests.request(method=method, url=url, json=payload, timeout=15)
    except requests.exceptions.Timeout as exc:
        raise BackendTimeoutError("Backend request timed out.") from exc
    except requests.exceptions.ConnectionError as exc:
        raise BackendUnavailableError("Backend is unavailable.") from exc
    except requests.exceptions.RequestException as exc:
        raise ApiClientError("Backend request failed.") from exc

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        detail = str(data.get("detail", f"HTTP {response.status_code}"))
        if response.status_code >= 500:
            raise BackendUnavailableError(detail)
        raise ApiClientError(detail)

    return data


def fetch_user_profile(user_id: str, display_name: str | None = None, source: str | None = None):
    query = f"/user-profile?user_id={requests.utils.quote(user_id)}"
    if display_name:
        query += f"&display_name={requests.utils.quote(display_name)}"
    if source:
        query += f"&source={requests.utils.quote(source)}"
    return _request_json("GET", query)


def update_user_profile_settings(payload: dict):
    return _request_json("POST", "/user-profile/settings", payload)


def reset_user_profile(payload: dict):
    return _request_json("POST", "/user-profile/reset", payload)


def fetch_user_watchlist(user_id: str):
    return _request_json("GET", f"/user-watchlist?user_id={requests.utils.quote(user_id)}")


def add_user_watchlist_ticker(payload: dict):
    return _request_json("POST", "/user-watchlist/add", payload)


def remove_user_watchlist_ticker(payload: dict):
    return _request_json("POST", "/user-watchlist/remove", payload)


def fetch_user_alert_settings(user_id: str):
    return _request_json("GET", f"/user-alert-settings?user_id={requests.utils.quote(user_id)}")


def update_user_alert_settings(payload: dict):
    return _request_json("POST", "/user-alert-settings/update", payload)


def scan_user_alerts(user_id: str):
    return _request_json("GET", f"/user-alerts/scan?user_id={requests.utils.quote(user_id)}")


def fetch_alert_enabled_users():
    return _request_json("GET", "/user-alerts/enabled-users")


def resolve_discord_profile(discord_user_id: str):
    return _request_json(
        "GET",
        f"/discord-link/resolve?discord_user_id={requests.utils.quote(discord_user_id)}",
    )


def consume_discord_link(payload: dict):
    return _request_json("POST", "/discord-link/consume", payload)
