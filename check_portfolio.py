"""
check_portfolio.py
Prints the Layer-1 PIT score (plus ROE / D-E) for a fixed watchlist of tickers,
using the exact same scoring function as the live strategy.

Run with: python check_portfolio.py
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import vix_ai_picker as _vix
import sp500_backtest as _bt

_vix.QUICK_MODE    = True
_vix.EDGAR_TIMEOUT = 5
_vix.NUM_WORKERS   = 10

_load_cik_map      = _vix._load_cik_map
get_cik            = _vix.get_cik
get_xbrl_facts     = _vix.get_xbrl_facts
_get_price_cache   = _vix._get_price_cache
_get_pit_metrics   = _vix._get_pit_metrics
score_layer1_sp500 = _bt.score_layer1_sp500

TICKERS = [
    'NVDA', 'DOV', 'FIX', 'GOOG', 'GOOGL', 'RMD', 'META',
    'PAYC', 'MNST', 'MPWR', 'STE', 'ANET', 'GL', 'ORLY',
    'SNPS', 'CBOE', 'CMG', 'RJF', 'JKHY', 'PCAR', 'PHM',
    'APH', 'LULU', 'MOH', 'SLB',
]


def _fmt_pct(v):
    """_get_pit_metrics floors the equity denominator to max(equity, 1), so
    companies with negative book equity (e.g. heavy buyback names) produce
    nonsensical multi-billion-percent ratios. Flag those as undefined."""
    if v is None:
        return 'N/A'
    if abs(v) > 5:
        return 'n/m*'
    return f'{v:.0%}'


def main():
    year   = date.today().year
    cutoff = (datetime(year, 1, 1) - timedelta(days=45)).strftime('%Y-%m-%d')

    print('Loading CIK map...')
    _load_cik_map()

    print(f'\n{"Ticker":<8} | {"Score":>5} | {"ROE":>6} | {"D/E":>6}')
    print('-' * 36)

    for ticker in TICKERS:
        cik = get_cik(ticker)
        if not cik:
            print(f'{ticker:<8} | {"N/A":>5} | {"N/A":>6} | {"N/A":>6}   (no CIK)')
            continue
        gaap = get_xbrl_facts(cik)
        if not gaap:
            print(f'{ticker:<8} | {"N/A":>5} | {"N/A":>6} | {"N/A":>6}   (no GAAP data)')
            continue
        ph = _get_price_cache(ticker)

        score, _, _ = score_layer1_sp500(ticker, year, gaap, ph)
        metrics = _get_pit_metrics(gaap, cutoff, ph, ticker=ticker)
        roe = metrics.get('roe')
        de  = metrics.get('de')

        roe_s = _fmt_pct(roe)
        de_s  = f'{de:.2f}' if de is not None else 'N/A'
        print(f'{ticker:<8} | {score:>5} | {roe_s:>6} | {de_s:>6}')

    print('\n* n/m = not meaningful (negative/near-zero book equity distorts the ratio)')


if __name__ == '__main__':
    main()
