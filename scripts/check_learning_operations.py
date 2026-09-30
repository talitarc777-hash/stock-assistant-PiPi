"""Public-safe diagnostics. GET by default; --probe makes two bounded provider probes.

No credentials, response bodies, exception strings or backend paths are printed.
Probes do not archive data, fit models, place trades or send Discord messages.
"""
import argparse
import json
import logging
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def probe():
    from app.services.external_market_context_service import (
        _empty_context, _add_yfinance_analyst_context, _add_yfinance_news_context,
        _add_reddit_context, source_statuses, source_configuration,
    )
    results = {}
    for symbol in ('AAPL', '0700.HK'):
        context = _empty_context(symbol)
        for operation in (_add_yfinance_analyst_context, _add_yfinance_news_context):
            try:
                operation(context, ticker=symbol)
            except Exception:
                pass
        try:
            _add_reddit_context(context, ticker=symbol, company_name=context.get('verified_company_name'), timeout=3)
        except Exception:
            pass
        results[symbol] = {k: v for k, v in source_statuses(context).items() if k.startswith(('yfinance', 'reddit'))}
    return {'probe_scope': 'this host, now; not proof of NanoPi connectivity or historical coverage',
            'configuration': source_configuration(), 'results': results,
            'not_probed': ['alpha_vantage_news_sentiment', 'alpha_vantage_earnings_transcript', 'sec_edgar_filings']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.probe:
        print(json.dumps(probe(), indent=2))
    else:
        try:
            with urlopen(args.base_url.rstrip('/') + '/model-lifecycle/learning-health', timeout=30) as response:
                result = json.load(response)
            # Endpoint is deliberately allowlisted; don't print arbitrary error bodies.
            print(json.dumps({k: result[k] for k in ('status', 'blockers', 'checked_at_utc', 'heartbeat', 'sources', 'plans', 'research', 'training_admission') if k in result}, indent=2))
        except Exception:
            print(json.dumps({'status': 'unreachable', 'message': 'Check API service, URL and Tailscale access.'}))
            sys.exit(1)
