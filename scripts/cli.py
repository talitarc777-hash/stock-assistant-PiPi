"""Automation-friendly CLI for stock-assistant workflows."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from app.backtest.engine import BacktestInputError, run_backtest
from app.services.benchmark import BenchmarkAnalysisError, compare_to_benchmark
from app.services.indicators import IndicatorInputError, add_technical_indicators
from app.services.market_data import EmptyDataError, InvalidTickerError, MarketDataError, get_price_history
from app.services.scoring import ScoringInputError, score_from_indicators

logger = logging.getLogger(__name__)

try:
    from app.services.model_training import (
        ModelTrainingError,
        train_baseline_models_for_ticker,
        train_baseline_models_for_watchlist,
        train_pooled_baseline_models,
    )
except Exception:  # pragma: no cover - optional dependency path
    ModelTrainingError = ValueError  # type: ignore[assignment]
    train_baseline_models_for_ticker = None
    train_baseline_models_for_watchlist = None
    train_pooled_baseline_models = None

try:
    from app.services.virtual_trader import (
        VirtualTraderError,
        run_virtual_trader_from_model,
    )
except Exception:  # pragma: no cover - optional dependency path
    VirtualTraderError = ValueError  # type: ignore[assignment]
    run_virtual_trader_from_model = None


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _analyze_one(ticker: str, period: str, benchmark: str) -> dict[str, Any]:
    """Run analysis pipeline for one ticker and return structured data."""
    ticker_df = get_price_history(ticker=ticker, period=period)
    benchmark_df = get_price_history(ticker=benchmark, period=period)
    indicators_df = add_technical_indicators(ticker_df)
    score = score_from_indicators(indicators_df)
    benchmark_cmp = compare_to_benchmark(
        ticker_df=ticker_df,
        benchmark_df=benchmark_df,
        benchmark_symbol=benchmark,
    )

    latest = indicators_df.iloc[-1]
    latest_close = float(latest["close"])
    latest_rsi = _safe_float(latest["rsi_14"])
    macd_bullish = (
        bool(float(latest["macd_line"]) > float(latest["macd_signal"]))
        if latest["macd_line"] is not None and latest["macd_signal"] is not None
        else False
    )

    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "latest_close": latest_close,
        "score_breakdown": {
            "trend_score": score.trend_score,
            "momentum_score": score.momentum_score,
            "confirmation_score": score.confirmation_score,
            "risk_penalty": score.risk_penalty,
            "total_score": score.total_score,
        },
        "label": score.label,
        "action_summary": score.action_summary,
        "explanation_bullets": score.explanations,
        "benchmark_relative": {
            "benchmark": benchmark_cmp.benchmark,
            "returns_pct": benchmark_cmp.returns,
            "benchmark_returns_pct": benchmark_cmp.benchmark_returns,
            "excess_returns_pct": benchmark_cmp.excess_returns,
            "benchmark_strength_score": benchmark_cmp.benchmark_strength_score,
        },
        "indicator_snapshot": {
            "sma_20": _safe_float(latest["sma_20"]),
            "sma_50": _safe_float(latest["sma_50"]),
            "sma_200": _safe_float(latest["sma_200"]),
            "rsi_14": latest_rsi,
            "macd_line": _safe_float(latest["macd_line"]),
            "macd_signal": _safe_float(latest["macd_signal"]),
            "macd_bullish": macd_bullish,
        },
    }


def _load_watchlist_from_config(config_path: str) -> tuple[list[str], str, str]:
    """Load watchlist config used for CLI automation tasks."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    watchlist = [str(item).strip().upper() for item in payload.get("watchlist", []) if str(item).strip()]
    period = str(payload.get("period", "5y"))
    benchmark = str(payload.get("benchmark", "VOO")).strip().upper()
    return watchlist, period, benchmark


def cmd_analyze_ticker(args: argparse.Namespace) -> int:
    """Handle analyze-ticker command."""
    result = _analyze_one(args.ticker, args.period, args.benchmark)
    _print_section(f"ANALYZE TICKER: {result['ticker']}")
    print(f"Period: {result['period']}")
    print(f"Latest close: {result['latest_close']:.2f}")
    print(f"Score: {result['score_breakdown']['total_score']}")
    print(f"Label: {result['label']}")
    print(f"Action: {result['action_summary']}")
    print(f"Benchmark strength score: {result['benchmark_relative']['benchmark_strength_score']}")
    print("\nExplanation:")
    for bullet in result["explanation_bullets"]:
        print(f"- {bullet}")
    return 0


def cmd_analyze_watchlist(args: argparse.Namespace) -> int:
    """Handle analyze-watchlist command."""
    watchlist, default_period, default_benchmark = _load_watchlist_from_config(args.config)
    period = args.period or default_period
    benchmark = args.benchmark or default_benchmark

    if not watchlist:
        raise ValueError("Watchlist is empty in config.")

    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for ticker in watchlist:
        try:
            rows.append(_analyze_one(ticker, period, benchmark))
        except (
            InvalidTickerError,
            EmptyDataError,
            MarketDataError,
            IndicatorInputError,
            ScoringInputError,
            BenchmarkAnalysisError,
        ) as exc:
            failures.append(f"{ticker}: {exc}")

    rows.sort(key=lambda item: item["score_breakdown"]["total_score"], reverse=True)

    _print_section("WATCHLIST RANKING")
    print("Rank | Ticker | Score | Label                       | Action")
    print("-----+--------+-------+-----------------------------+------------------------")
    for index, item in enumerate(rows, start=1):
        print(
            f"{index:>4} | {item['ticker']:<6} | {item['score_breakdown']['total_score']:>5} | "
            f"{item['label']:<27} | {item['action_summary']}"
        )

    if failures:
        _print_section("WATCHLIST FAILURES")
        for failure in failures:
            print(f"- {failure}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Handle backtest command."""
    price_df = get_price_history(args.ticker, args.period)
    indicators_df = add_technical_indicators(price_df)
    result = run_backtest(indicators_df, transaction_cost_pct=args.transaction_cost_pct)

    _print_section(f"BACKTEST: {args.ticker.strip().upper()}")
    print(f"Period: {args.period}")
    print(f"Transaction cost pct: {args.transaction_cost_pct}")
    print("Metrics:")
    for key, value in result.metrics.items():
        if isinstance(value, float):
            print(f"- {key}: {value:.4f}")
        else:
            print(f"- {key}: {value}")

    print("\nTrade preview (latest 10):")
    preview = result.trades[-10:]
    if not preview:
        print("- No trades generated.")
    else:
        for trade in preview:
            print(
                f"- {trade.entry_date} -> {trade.exit_date} | "
                f"entry {trade.entry_price:.2f} | exit {trade.exit_price:.2f} | "
                f"return {trade.return_pct:.2f}% | {trade.exit_reason}"
            )
    return 0


def cmd_export_report(args: argparse.Namespace) -> int:
    """Handle export-report command."""
    ticker = args.ticker.strip().upper()
    analysis = _analyze_one(ticker, args.period, args.benchmark)
    price_df = get_price_history(ticker, args.period)
    indicators_df = add_technical_indicators(price_df)
    backtest = run_backtest(indicators_df, transaction_cost_pct=args.transaction_cost_pct)

    report_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "period": args.period,
        "benchmark": args.benchmark.strip().upper(),
        "analysis": analysis,
        "backtest_metrics": backtest.metrics,
        "backtest_trade_preview": [
            {
                "entry_date": trade.entry_date,
                "entry_price": trade.entry_price,
                "exit_date": trade.exit_date,
                "exit_price": trade.exit_price,
                "return_pct": trade.return_pct,
                "exit_reason": trade.exit_reason,
            }
            for trade in backtest.trades[-20:]
        ],
    }

    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = reports_dir / f"{ticker}_{timestamp}.json"
    output_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    _print_section("REPORT EXPORTED")
    print(f"Ticker: {ticker}")
    print(f"Path: {output_path}")
    return 0


def cmd_train_models(args: argparse.Namespace) -> int:
    """Handle train-models command for one ticker or a watchlist."""
    if (
        train_baseline_models_for_ticker is None
        or train_baseline_models_for_watchlist is None
        or train_pooled_baseline_models is None
    ):
        raise ValueError(
            "Model training dependencies are not available. Install requirements.txt to use train-models."
        )

    if bool(args.ticker) == bool(args.watchlist_config):
        raise ValueError("Provide exactly one of --ticker or --watchlist-config.")
    if args.pooled and not args.watchlist_config:
        raise ValueError("--pooled requires --watchlist-config with at least three tickers.")

    if args.ticker:
        tickers = [args.ticker.strip().upper()]
        period = args.period
        benchmark = args.benchmark
    else:
        tickers, default_period, default_benchmark = _load_watchlist_from_config(args.watchlist_config)
        period = args.period or default_period
        benchmark = args.benchmark or default_benchmark

    if not tickers:
        raise ValueError("No tickers available for training.")

    _print_section("MODEL TRAINING")
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Period: {period}")
    print(f"Benchmark: {benchmark}")
    print(f"News sentiment: {'on' if not args.no_news_sentiment else 'off'}")
    print(f"Gradient boosting: {'on' if not args.no_gradient_boosting else 'off'}")
    individual_targets = {
        "standard": None,
        "direction": ("target_5d_updown",),
        "return": ("target_5d_return",),
        "outperform": ("target_5d_outperform",),
    }[args.target_mode]

    if args.pooled:
        pooled_targets = {
            "return": ("target_5d_return",),
            "direction": ("target_5d_updown",),
            "outperform": ("target_5d_outperform",),
            "both": ("target_5d_updown", "target_5d_return"),
        }[args.pooled_target]
        training_map = {
            "GLOBAL": train_pooled_baseline_models(
                tickers=tickers,
                period=period,
                benchmark=benchmark,
                include_news_sentiment=not args.no_news_sentiment,
                sentiment_model=args.sentiment_model,
                output_dir=args.output_dir,
                include_gradient_boosting=not args.no_gradient_boosting,
                target_names=pooled_targets,
            )
        }
    elif len(tickers) == 1:
        training_map = {
            tickers[0]: train_baseline_models_for_ticker(
                ticker=tickers[0],
                period=period,
                benchmark=benchmark,
                include_news_sentiment=not args.no_news_sentiment,
                sentiment_model=args.sentiment_model,
                output_dir=args.output_dir,
                include_gradient_boosting=not args.no_gradient_boosting,
                target_names=individual_targets,
            )
        }
    else:
        training_map = train_baseline_models_for_watchlist(
            tickers=tickers,
            period=period,
            benchmark=benchmark,
            include_news_sentiment=not args.no_news_sentiment,
            sentiment_model=args.sentiment_model,
            output_dir=args.output_dir,
            include_gradient_boosting=not args.no_gradient_boosting,
            target_names=individual_targets,
        )

    skipped_tickers = [] if args.pooled else [ticker for ticker in tickers if ticker not in training_map]
    if skipped_tickers:
        _print_section("SKIPPED TICKERS")
        for ticker in skipped_tickers:
            print(f"- {ticker} (training skipped due to data/dependency error; check logs above)")

    for ticker, results in training_map.items():
        _print_section(f"TRAINED: {ticker}")
        for result in results:
            summary_metrics = result.metrics.get("metrics", {})
            if result.task_type == "classification":
                metric_text = (
                    f"accuracy={summary_metrics.get('accuracy', 0.0):.3f}, "
                    f"f1={summary_metrics.get('f1', 0.0):.3f}"
                )
            else:
                metric_text = (
                    f"mae={summary_metrics.get('mae', 0.0):.3f}, "
                    f"rmse={summary_metrics.get('rmse', 0.0):.3f}"
                )

            print(
                f"- {result.target_name} | {result.model_name} | "
                f"rows={result.metrics.get('row_count', 0)} | {metric_text}"
            )
            print(f"  model: {result.artifact.model_path}")
            print(f"  predictions: {result.artifact.predictions_path}")
            print(f"  walk-forward evaluation: {result.artifact.evaluation_table_path}")

    return 0


def cmd_virtual_trader(args: argparse.Namespace) -> int:
    """Handle virtual-trader command."""
    if run_virtual_trader_from_model is None:
        raise ValueError(
            "Virtual trader dependencies are not available. Install requirements.txt to use virtual-trader."
        )

    result = run_virtual_trader_from_model(
        ticker=args.ticker,
        period=args.period,
        benchmark=args.benchmark,
        target_name=args.target_name,
        task_type=args.task_type,
        model_name=args.model_name,
        monthly_contribution_usd=args.monthly_contribution_usd,
        initial_cash=args.initial_cash,
        confidence_threshold=args.confidence_threshold,
        max_position_size_pct=args.max_position_size_pct,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        min_predicted_return_pct=args.min_predicted_return_pct,
        include_news_sentiment=not args.no_news_sentiment,
        sentiment_model=args.sentiment_model,
        output_dir=args.output_dir,
    )

    _print_section("VIRTUAL TRADER")
    print(f"Ticker: {result.ticker}")
    print(f"Model: {result.model_name}")
    print(f"Final equity: {result.summary['final_equity']:.2f}")
    print(f"Total contributions: {result.summary['total_contributions']:.2f}")
    print(f"Return on contributions: {result.summary['return_on_contributions_pct']:.2f}%")
    print(f"Benchmark ({result.benchmark_comparison['benchmark']}) equity: {result.benchmark_comparison['final_equity']:.2f}")
    print(
        "Outperformance vs benchmark: "
        f"{result.summary['outperformance_vs_benchmark_pct_points']:.2f} pct points"
    )
    print(f"Trades: {result.summary['trade_count']}")
    print(f"Trade log: {result.artifact.trade_log_path}")
    print(f"Equity curve: {result.artifact.equity_curve_path}")
    print(f"Contributions: {result.artifact.contribution_history_path}")
    print(f"Summary: {result.artifact.summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build root argument parser and subcommands."""
    parser = argparse.ArgumentParser(description="Stock Assistant automation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_ticker = subparsers.add_parser("analyze-ticker", help="Analyze one ticker.")
    analyze_ticker.add_argument("--ticker", required=True, help="Ticker symbol, e.g. VOO")
    analyze_ticker.add_argument("--period", default="5y", help="History period, default 5y")
    analyze_ticker.add_argument("--benchmark", default="VOO", help="Benchmark ticker, default VOO")
    analyze_ticker.set_defaults(func=cmd_analyze_ticker)

    analyze_watchlist = subparsers.add_parser(
        "analyze-watchlist", help="Analyze watchlist from config/watchlist.json."
    )
    analyze_watchlist.add_argument(
        "--config",
        default="config/watchlist.json",
        help="Watchlist config path, default config/watchlist.json",
    )
    analyze_watchlist.add_argument("--period", default=None, help="Override period from config.")
    analyze_watchlist.add_argument("--benchmark", default=None, help="Override benchmark from config.")
    analyze_watchlist.set_defaults(func=cmd_analyze_watchlist)

    backtest = subparsers.add_parser("backtest", help="Run strategy backtest for one ticker.")
    backtest.add_argument("--ticker", required=True, help="Ticker symbol, e.g. VOO")
    backtest.add_argument("--period", default="10y", help="History period, default 10y")
    backtest.add_argument(
        "--transaction-cost-pct",
        type=float,
        default=0.0,
        help="Optional one-way transaction cost in decimal, e.g. 0.001",
    )
    backtest.set_defaults(func=cmd_backtest)

    export_report = subparsers.add_parser(
        "export-report", help="Export analysis + backtest report to reports/*.json"
    )
    export_report.add_argument("--ticker", required=True, help="Ticker symbol, e.g. VOO")
    export_report.add_argument("--period", default="5y", help="History period, default 5y")
    export_report.add_argument("--benchmark", default="VOO", help="Benchmark ticker, default VOO")
    export_report.add_argument(
        "--transaction-cost-pct",
        type=float,
        default=0.0,
        help="Optional one-way transaction cost in decimal, e.g. 0.001",
    )
    export_report.set_defaults(func=cmd_export_report)

    train_models = subparsers.add_parser(
        "train-models",
        help="Train baseline prediction models for one ticker or a watchlist.",
    )
    train_models.add_argument("--ticker", default=None, help="Train one ticker, e.g. VOO")
    train_models.add_argument(
        "--watchlist-config",
        default=None,
        help="Train all tickers from a watchlist config JSON file.",
    )
    train_models.add_argument("--period", default="5y", help="History period, default 5y")
    train_models.add_argument("--benchmark", default="VOO", help="Benchmark ticker, default VOO")
    train_models.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for saved model artifacts.",
    )
    train_models.add_argument(
        "--sentiment-model",
        default="finbert",
        help="Sentiment model name for dataset generation, default finbert.",
    )
    train_models.add_argument(
        "--no-news-sentiment",
        action="store_true",
        help="Disable news sentiment features during dataset generation.",
    )
    train_models.add_argument(
        "--no-gradient-boosting",
        action="store_true",
        help="Skip gradient boosting baselines and train only the simpler models.",
    )
    train_models.add_argument(
        "--pooled",
        action="store_true",
        help="Train one experimental GLOBAL model across a watchlist using date-grouped validation.",
    )
    train_models.add_argument(
        "--pooled-target",
        choices=["return", "direction", "outperform", "both"],
        default="return",
        help="Target for pooled training; default return preserves the existing workflow.",
    )
    train_models.add_argument(
        "--target-mode",
        choices=["standard", "direction", "return", "outperform"],
        default="standard",
        help="Targets for ticker/watchlist training; standard trains direction and return.",
    )
    train_models.set_defaults(func=cmd_train_models)

    virtual_trader = subparsers.add_parser(
        "virtual-trader",
        help="Run a simulation-only model-driven trader with monthly contributions.",
    )
    virtual_trader.add_argument("--ticker", required=True, help="Ticker symbol, e.g. VOO")
    virtual_trader.add_argument("--period", default="5y", help="History period, default 5y")
    virtual_trader.add_argument("--benchmark", default="VOO", help="Benchmark ticker, default VOO")
    virtual_trader.add_argument(
        "--target-name",
        default="target_5d_updown",
        help="Model target to use for signals, default target_5d_updown",
    )
    virtual_trader.add_argument(
        "--task-type",
        default="classification",
        choices=["classification", "regression"],
        help="Prediction task type used for signal generation.",
    )
    virtual_trader.add_argument(
        "--model-name",
        default="logistic_regression",
        help="Baseline model name, default logistic_regression",
    )
    virtual_trader.add_argument(
        "--monthly-contribution-usd",
        type=float,
        default=1000.0,
        help="Monthly cash contribution added on the first trading day of each month.",
    )
    virtual_trader.add_argument(
        "--initial-cash",
        type=float,
        default=0.0,
        help="Optional starting cash before monthly contributions begin.",
    )
    virtual_trader.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.55,
        help="Minimum model confidence needed to act on a signal, default 0.55",
    )
    virtual_trader.add_argument(
        "--max-position-size-pct",
        type=float,
        default=0.25,
        help="Maximum fraction of total equity allowed in one position, default 0.25",
    )
    virtual_trader.add_argument(
        "--stop-loss-pct",
        type=float,
        default=0.10,
        help="Stop loss percentage as a decimal, default 0.10",
    )
    virtual_trader.add_argument(
        "--take-profit-pct",
        type=float,
        default=None,
        help="Optional take profit percentage as a decimal.",
    )
    virtual_trader.add_argument(
        "--min-predicted-return-pct",
        type=float,
        default=0.0,
        help="For regression signals, require predicted return >= this threshold.",
    )
    virtual_trader.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for saved virtual trader artifacts.",
    )
    virtual_trader.add_argument(
        "--sentiment-model",
        default="finbert",
        help="Sentiment model name for dataset generation, default finbert.",
    )
    virtual_trader.add_argument(
        "--no-news-sentiment",
        action="store_true",
        help="Disable news sentiment features during dataset generation.",
    )
    virtual_trader.set_defaults(func=cmd_virtual_trader)

    return parser


def main() -> int:
    """CLI entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    logger.info("CLI command invoked: %s", args.command)

    try:
        return int(args.func(args))
    except (
        FileNotFoundError,
        ValueError,
        InvalidTickerError,
        EmptyDataError,
        MarketDataError,
        IndicatorInputError,
        ScoringInputError,
        BenchmarkAnalysisError,
        BacktestInputError,
        ModelTrainingError,
        VirtualTraderError,
    ) as exc:
        _print_section("ERROR")
        print(str(exc))
        logger.exception("CLI command failed")
        return 1
    except Exception as exc:  # pragma: no cover - defensive guard
        _print_section("ERROR")
        print("Unexpected error occurred.")
        logger.exception("Unexpected CLI failure: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
