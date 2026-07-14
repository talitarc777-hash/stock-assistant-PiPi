import requests

try:
    from .config import BACKEND_BASE_URL
except ImportError:  # pragma: no cover - script execution fallback
    from config import BACKEND_BASE_URL


class ApiClientError(Exception):
    """Base API client error for Discord bot messaging."""


class InvalidTickerApiError(ApiClientError):
    """Raised when backend reports an invalid ticker or bad ticker query."""


class BackendUnavailableError(ApiClientError):
    """Raised when backend cannot be reached."""


class BackendTimeoutError(ApiClientError):
    """Raised when backend request times out."""


def _get_json(url: str):
    """Perform GET request and return parsed JSON with clear API errors."""
    try:
        response = requests.get(url, timeout=15)
    except requests.exceptions.Timeout as exc:
        raise BackendTimeoutError("Backend request timed out.") from exc
    except requests.exceptions.ConnectionError as exc:
        raise BackendUnavailableError("Backend is unavailable.") from exc
    except requests.exceptions.RequestException as exc:
        raise ApiClientError("Backend request failed.") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        detail = str(payload.get("detail", f"HTTP {response.status_code}"))
        detail_lower = detail.lower()

        if response.status_code in (400, 404, 422) and (
            "ticker" in detail_lower or "symbol" in detail_lower
        ):
            raise InvalidTickerApiError(detail)

        if response.status_code >= 500:
            raise BackendUnavailableError(detail)

        raise ApiClientError(detail)
    return payload


def analyze(ticker: str):
    url = f"{BACKEND_BASE_URL}/analyze?ticker={ticker}"
    return _get_json(url)


def forecast(ticker: str, period: str = "2y"):
    url = f"{BACKEND_BASE_URL}/forecast?ticker={ticker}&period={period}"
    return _get_json(url)


def watchlist(tickers_csv: str, period: str = "5y"):
    url = f"{BACKEND_BASE_URL}/watchlist-analyze?tickers={tickers_csv}&period={period}"
    return _get_json(url)


def chart_data(ticker: str, period: str = "6mo"):
    """Fetch chart + indicator history for one ticker from backend."""
    url = f"{BACKEND_BASE_URL}/chart-data?ticker={ticker}&period={period}"
    return _get_json(url)


def model_latest(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
):
    """Fetch the latest saved model prediction for one ticker."""
    url = (
        f"{BACKEND_BASE_URL}/model-latest?ticker={ticker}"
        f"&period={period}&target_name={target_name}&model_name={model_name}"
    )
    return _get_json(url)


def model_accuracy(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    window: int = 20,
):
    """Fetch saved model accuracy metrics and rolling hit rate."""
    url = (
        f"{BACKEND_BASE_URL}/model-accuracy?ticker={ticker}"
        f"&period={period}&target_name={target_name}&model_name={model_name}&window={window}"
    )
    return _get_json(url)


def virtual_trader_summary(
    ticker: str,
    period: str = "5y",
    model_name: str = "logistic_regression",
    equity_limit: int = 200,
):
    """Fetch the saved virtual trader summary and recent equity curve."""
    url = (
        f"{BACKEND_BASE_URL}/virtual-trader-summary?ticker={ticker}"
        f"&period={period}&model_name={model_name}&equity_limit={equity_limit}"
    )
    return _get_json(url)


def virtual_trader_trades(
    ticker: str,
    period: str = "5y",
    model_name: str = "logistic_regression",
    limit: int = 50,
):
    """Fetch the saved virtual trader trade log and contribution history."""
    url = (
        f"{BACKEND_BASE_URL}/virtual-trader-trades?ticker={ticker}"
        f"&period={period}&model_name={model_name}&limit={limit}"
    )
    return _get_json(url)


def virtual_trader_live_status(
    user_id: str,
    ticker: str | None = None,
    model_name: str | None = None,
    auto_run: bool = False,
):
    """Fetch current live virtual trader status."""
    ticker_query = f"&ticker={ticker}" if ticker else ""
    model_query = f"&model_name={model_name}" if model_name else ""
    url = (
        f"{BACKEND_BASE_URL}/virtual-trader/live-status?user_id={user_id}"
        f"{ticker_query}{model_query}&auto_run={'true' if auto_run else 'false'}"
    )
    return _get_json(url)


def benchmark_shadow_feedback(
    ticker: str,
    period: str = "10y",
    model_name: str = "random_forest",
    limit: int = 20,
):
    """Fetch the same forward benchmark evidence shown by the web app."""
    url = (
        f"{BACKEND_BASE_URL}/model-lifecycle/benchmark-shadow-feedback?ticker={ticker}"
        f"&model_period={period}&model_name={model_name}&limit={limit}"
    )
    return _get_json(url)


def virtual_trader_live_sync(user_id: str):
    """Fetch the consolidated read-only state shared by web and Discord."""
    encoded_user_id = requests.utils.quote(str(user_id))
    url = f"{BACKEND_BASE_URL}/virtual-trader/live-sync?user_id={encoded_user_id}"
    return _get_json(url)


def virtual_trader_run_now(
    user_id: str,
    tickers: list[str] | None = None,
    model_name: str | None = None,
):
    """Run live virtual trader now and return updated status."""
    url = f"{BACKEND_BASE_URL}/virtual-trader/run-now"
    payload = {"user_id": user_id}
    if tickers:
        payload["tickers"] = tickers
    if model_name:
        payload["model_name"] = model_name
    try:
        response = requests.post(url, json=payload, timeout=20)
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


def virtual_trader_live_trades(
    user_id: str,
    ticker: str | None = None,
    limit: int = 20,
):
    """Fetch latest live virtual trader trade/decision logs."""
    ticker_query = f"&ticker={ticker}" if ticker else ""
    url = (
        f"{BACKEND_BASE_URL}/virtual-trader/live-trades?user_id={user_id}"
        f"{ticker_query}&limit={int(limit)}"
    )
    return _get_json(url)


def trader_scheduler_status(log_limit: int = 8):
    """Fetch trader scheduler runtime status."""
    url = f"{BACKEND_BASE_URL}/virtual-trader/scheduler-status?log_limit={int(log_limit)}"
    return _get_json(url)


def virtual_account_summary(user_id: str):
    """Fetch immutable virtual account summary."""
    url = f"{BACKEND_BASE_URL}/virtual-account/summary?user_id={user_id}"
    return _get_json(url)


def virtual_account_ledger(
    user_id: str,
    limit: int = 50,
):
    """Fetch immutable virtual account ledger events."""
    url = f"{BACKEND_BASE_URL}/virtual-account/ledger?user_id={user_id}&limit={int(limit)}"
    return _get_json(url)


def _post_account_json(path: str, payload: dict):
    """Post one virtual-account change with consistent Discord-facing errors."""
    url = f"{BACKEND_BASE_URL}{path}"
    try:
        response = requests.post(url, json=payload, timeout=15)
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


def virtual_account_deposit(user_id: str, amount: float):
    """Add simulation-only cash to the shared account."""
    return _post_account_json(
        "/virtual-account/deposit",
        {"user_id": user_id, "amount": amount, "source": "discord"},
    )


def virtual_account_withdraw(user_id: str, amount: float):
    """Withdraw simulation-only cash from the shared account."""
    return _post_account_json(
        "/virtual-account/withdraw",
        {"user_id": user_id, "amount": amount, "source": "discord"},
    )


def virtual_account_set_monthly_contribution(user_id: str, amount: float):
    """Set the shared recurring monthly simulation contribution."""
    return _post_account_json(
        "/virtual-account/monthly-contribution-input",
        {"user_id": user_id, "amount": amount, "source": "discord"},
    )
