"""
audit_split_bug.py
==================
Identifies every ticker-year in the sp500_l1_cache (2010-2026) where a stock
split fell between the last fiscal-year end and the 45-day-lag scoring cutoff,
causing the yfinance-adjusted price and the EDGAR-based EPS to be on different
per-share bases (the same bug found in NFLX year=2026).

Run with: python audit_split_bug.py
"""
import sys, json
from pathlib import Path
from datetime import datetime, timedelta

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))

L1_CACHE   = Path('data/sp500_l1_cache')
START_YEAR = 2010
END_YEAR   = 2026


def _scoring_window(year: int):
    """Return (since_date, until_date) that define the split-mismatch window.

    since_date  = Dec 31 of the last fully-reported FY (year-2 for Dec FY companies).
    until_date  = Jan 1 of the scoring year (the price date used in D4 valuation).
    Any split strictly inside this range means yfinance price (post-split) and
    EDGAR EPS (pre-split, filed before the split) are on different bases.
    """
    since = f'{year - 2}-12-31'
    until = f'{year}-01-01'
    return since, until


def main():
    # ── collect all scored ticker-years from the cache ──────────────────────── #
    ticker_years: dict[str, list[int]] = {}
    for f in L1_CACHE.glob('*.json'):
        parts = f.stem.rsplit('_', 1)
        if len(parts) != 2:
            continue
        ticker, yr_str = parts
        try:
            yr = int(yr_str)
        except ValueError:
            continue
        if START_YEAR <= yr <= END_YEAR:
            ticker_years.setdefault(ticker, []).append(yr)

    total_tickers    = len(ticker_years)
    total_ty_checked = sum(len(v) for v in ticker_years.values())
    affected         = []   # (ticker, year, ratio)
    fetch_errors     = []

    print(f'Tickers in cache ({START_YEAR}-{END_YEAR}): {total_tickers}')
    print(f'Ticker-years to check:            {total_ty_checked}')
    print('Fetching split history from yfinance (one request per ticker)...')
    print()

    for idx, (ticker, years) in enumerate(sorted(ticker_years.items()), 1):
        if idx % 50 == 0:
            print(f'  ... {idx}/{total_tickers} tickers done, '
                  f'{len(affected)} mismatches so far')
        try:
            splits = yf.Ticker(ticker).splits
        except Exception as e:
            fetch_errors.append((ticker, str(e)))
            continue

        for year in sorted(years):
            since, until = _scoring_window(year)
            ratio = 1.0
            for split_date, split_ratio in splits.items():
                sd = str(split_date)[:10]
                if since < sd <= until:
                    ratio *= split_ratio
            if ratio != 1.0:
                affected.append((ticker, year, ratio))

    # ── summary ─────────────────────────────────────────────────────────────── #
    print()
    print('=' * 60)
    print('SPLIT-BUG AUDIT RESULTS')
    print('=' * 60)
    print(f'Ticker-years checked:   {total_ty_checked}')
    print(f'Tickers checked:        {total_tickers}')
    print(f'Fetch errors:           {len(fetch_errors)}')
    print(f'Affected ticker-years:  {len(affected)}')
    print()

    if affected:
        print('Affected list (ticker | year | split ratio in window):')
        print(f'  {"Ticker":<10} {"Year":>4}  {"Ratio":>8}')
        print('  ' + '-' * 28)
        for ticker, year, ratio in sorted(affected):
            since, until = _scoring_window(year)
            print(f'  {ticker:<10} {year:>4}  {ratio:>8.1f}x  (window {since} to {until})')
    else:
        print('No affected ticker-years found.')

    if fetch_errors:
        print()
        print(f'Tickers with fetch errors ({len(fetch_errors)}):',
              ', '.join(t for t, _ in fetch_errors[:20]),
              '...' if len(fetch_errors) > 20 else '')

    print()
    print('Note: cached scores for affected ticker-years used the unadjusted EPS,')
    print('      overstating D4_valuation points (PE and PEG scored too cheap).')
    print('      The fix in _get_split_ratio() applies to all NEW score computations.')


if __name__ == '__main__':
    main()
