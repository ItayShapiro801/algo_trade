"""
S&P 500 Historical Backtest — Wikipedia constituents + SEC EDGAR PIT + AI sells
================================================================================
Run with: python sp500_backtest.py

Data sources:
  Wikipedia  https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  SEC EDGAR  XBRL fundamentals, 45-day publication lag
  yfinance   price history
  Anthropic  Claude Haiku sell confirmation (cost cap $2.00)
"""

import os, sys, json, time, re, warnings
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings('ignore')

# ── Bootstrap vix_ai_picker shared infrastructure ────────────────────────────── #
sys.path.insert(0, str(Path(__file__).parent))
import vix_ai_picker as _vix

_vix.QUICK_MODE    = True
_vix.EDGAR_TIMEOUT = 3
_vix.NUM_WORKERS   = 10

_load_cik_map      = _vix._load_cik_map
get_cik            = _vix.get_cik
get_xbrl_facts     = _vix.get_xbrl_facts
_annual_values_pit = _vix._annual_values_pit
_get_price_cache   = _vix._get_price_cache
_price_on_date     = _vix._price_on_date
_last_price_before = _vix._last_price_before
_get_pit_metrics   = _vix._get_pit_metrics
_get_split_ratio   = _vix._get_split_ratio
check_thesis_break = _vix.check_thesis_break
_cagr_series       = _vix._cagr   # takes a list, returns float (CAGR over series)

# ── Constants ─────────────────────────────────────────────────────────────────── #
CACHE_DIR        = Path('data')
SP500_HIST_CACHE = CACHE_DIR / 'sp500_historical'
SP500_L1_CACHE   = CACHE_DIR / 'sp500_l1_cache'
AI_SELL_CACHE    = CACHE_DIR / 'ai_sell_cache'
K8_CACHE         = CACHE_DIR / '8k_cache'
BIZ_CACHE        = CACHE_DIR / 'biz_cache'
SP500_PROGRESS   = CACHE_DIR / 'sp500_progress.json'

for _d in (SP500_HIST_CACHE, SP500_L1_CACHE, AI_SELL_CACHE, K8_CACHE, BIZ_CACHE):
    _d.mkdir(parents=True, exist_ok=True)

INITIAL_CAPITAL     = 100_000.0
ISRAEL_CGT          = 0.25
PIT_L1_PASS         = 65
SP500_MAX_POS       = 25
BACKTEST_START      = 2010
MAX_POSITION_WEIGHT = 0.25

AI_MODEL      = 'claude-haiku-4-5-20251001'  
AI_COST_CAP   = 3.00
AI_INPUT_CPM  = 0.80 / 1_000_000   # $0.80 per MTok (Haiku 4.5 pricing)
AI_OUTPUT_CPM = 4.00 / 1_000_000   # $4.00 per MTok (Haiku 4.5 pricing)

WIKI_SP500_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

# Anonymous macro-environment descriptions keyed by calendar year.
# Used only as generic market-regime color in the blind AI prompt — no company info.
_MACRO_CONTEXT = {
    2010: 'Post-financial-crisis recovery; low rates; cautious consumer spending.',
    2011: 'European debt crisis fears; US credit downgrade; high volatility.',
    2012: 'Slow steady recovery; central bank stimulus; election-year uncertainty.',
    2013: 'Strong equity rally; tapering concerns late in year; low inflation.',
    2014: 'Falling oil prices; dollar strength; steady US growth.',
    2015: 'China slowdown fears; commodity rout; flat overall market.',
    2016: 'Oil price rebound; election volatility; reflation trade emerging.',
    2017: 'Synchronized global growth; low volatility; tax-reform optimism.',
    2018: 'Trade-war tensions; rate hikes; sharp Q4 selloff.',
    2019: 'Rate cuts; trade-deal hopes; strong year-end rally.',
    2020: 'Pandemic shock and rapid recovery; massive fiscal/monetary stimulus.',
    2021: 'Reopening boom; high inflation emerging; speculative-asset mania.',
    2022: 'Aggressive rate hikes; inflation peak; broad market decline.',
    2023: 'Banking-sector stress; AI-driven rally in select names; resilient economy.',
    2024: 'Continued AI investment cycle; rate-cut anticipation; narrow market leadership.',
    2025: 'Elevated valuations; mixed growth signals; policy uncertainty.',
    2026: 'Ongoing normalization; market digesting prior years gains.',
}

_ai_cost_used = 0.0   # running global total


# ── Wikipedia S&P 500 constituent history ─────────────────────────────────────── #

def _fetch_wiki_tables():
    """Fetch and cache Wikipedia S&P 500 tables (7-day TTL)."""
    cache_p = SP500_HIST_CACHE / 'wiki_raw.json'
    if cache_p.exists() and (time.time() - cache_p.stat().st_mtime) < 7 * 86400:
        try:
            raw = json.loads(cache_p.read_text())
            return pd.DataFrame(raw['current']), pd.DataFrame(raw['changes'])
        except Exception:
            pass

    resp = requests.get(
        WIKI_SP500_URL,
        headers={'User-Agent': 'Mozilla/5.0 sp500-backtest research'},
        timeout=30,
    )
    resp.raise_for_status()
    tables  = pd.read_html(resp.text)
    current = tables[0]
    changes = tables[1]

    try:
        cache_p.write_text(json.dumps({
            'current': current.to_dict('records'),
            'changes': changes.to_dict('records'),
        }, default=str))
    except Exception:
        pass
    return current, changes


def get_sp500_for_year(year: int) -> list:
    """Return sorted list of S&P 500 ticker symbols as of Jan 1, {year}."""
    cache_p = SP500_HIST_CACHE / f'{year}.json'
    if cache_p.exists():
        try:
            return json.loads(cache_p.read_text())
        except Exception:
            pass

    cutoff_date = f'{year}-01-01'

    try:
        current_df, changes_df = _fetch_wiki_tables()
    except Exception as e:
        print(f'  [WARN] Wikipedia fetch failed: {e}')
        return []

    # Current constituents — detect ticker column
    cur_cols = [str(c) for c in current_df.columns]
    sym_col  = next(
        (c for c in cur_cols if 'symbol' in c.lower() or 'tick' in c.lower()),
        cur_cols[0],
    )
    tickers = set(
        current_df[sym_col].astype(str)
                           .str.replace('.', '-', regex=False)
                           .str.strip()
                           .tolist()
    )
    tickers.discard('nan')
    tickers.discard('')

    # Flatten multi-level columns in changes table
    if isinstance(changes_df.columns, pd.MultiIndex):
        flat = []
        for col in changes_df.columns:
            parts = [str(c).strip() for c in col if str(c).lower() not in ('nan', '')]
            flat.append('_'.join(parts))
        changes_df.columns = flat
    else:
        changes_df.columns = [str(c).strip() for c in changes_df.columns]

    cols     = list(changes_df.columns)
    date_col = next((c for c in cols if 'date' in c.lower()), cols[0])
    add_tick = next(
        (c for c in cols if 'add' in c.lower() and 'tick' in c.lower()),
        cols[1] if len(cols) > 1 else None,
    )
    rem_tick = next(
        (c for c in cols if ('remov' in c.lower() or 'delet' in c.lower())
         and 'tick' in c.lower()),
        cols[3] if len(cols) > 3 else None,
    )

    # Apply changes that happened AFTER cutoff_date in reverse
    for _, row in changes_df.iterrows():
        try:
            date_str  = str(row[date_col]).strip()
            change_dt = pd.to_datetime(date_str, errors='coerce')
            if pd.isna(change_dt):
                continue
            change_date = change_dt.strftime('%Y-%m-%d')
            if change_date < cutoff_date:
                continue  # this change happened before our target date

            if add_tick:
                added = str(row.get(add_tick, '')).strip().replace('.', '-')
                if added and added not in ('nan', '', 'None'):
                    tickers.discard(added)   # was added AFTER our date

            if rem_tick:
                removed = str(row.get(rem_tick, '')).strip().replace('.', '-')
                if removed and removed not in ('nan', '', 'None'):
                    tickers.add(removed)     # was removed AFTER our date
        except Exception:
            continue

    result = sorted(t for t in tickers if t and t not in ('nan', ''))
    try:
        cache_p.write_text(json.dumps(result))
    except Exception:
        pass
    return result


# ── L1 scoring with 45-day PIT lag ───────────────────────────────────────────── #

def score_layer1_sp500(ticker: str, year: int, gaap: dict, price_hist: dict) -> tuple:
    """
    PIT L1 score using 45-day publication lag: cutoff = Jan 1 - 45 days.
    Separate cache directory from vix_ai_picker pit_l1_cache.
    Returns (score: int, breakdown: dict, n_filings: int).
    """
    cache_p = SP500_L1_CACHE / f'{ticker}_{year}.json'
    if cache_p.exists():
        try:
            d = json.loads(cache_p.read_text())
            return d['score'], d['breakdown'], d['n_filings']
        except Exception:
            pass

    cutoff = (datetime(year, 1, 1) - timedelta(days=45)).strftime('%Y-%m-%d')

    def _av(*fields, n=5):
        for f in fields:
            if f in gaap:
                r = _annual_values_pit(gaap[f], cutoff, n)
                if r:
                    return r
        return None

    revenue      = _av('RevenueFromContractWithCustomerExcludingAssessedTax',
                       'Revenues', 'SalesRevenueNet', 'SalesRevenueGoodsNet')
    net_income   = _av('NetIncomeLoss', 'ProfitLoss')
    gross_profit = _av('GrossProfit')
    op_income    = _av('OperatingIncomeLoss')
    equity       = _av('StockholdersEquity', 'StockholdersEquityAttributableToParent')
    cur_assets   = _av('AssetsCurrent')
    cur_liab     = _av('LiabilitiesCurrent')
    lt_debt      = _av('LongTermDebt', 'LongTermDebtNoncurrent',
                       'LongTermDebtAndCapitalLeaseObligations')
    int_expense  = _av('InterestExpense', 'InterestAndDebtExpense')
    op_cf        = _av('NetCashProvidedByUsedInOperatingActivities')
    capex        = _av('PaymentsToAcquirePropertyPlantAndEquipment',
                       'CapitalExpendituresIncurringDebt', 'PaymentsForCapitalImprovements')
    eps          = _av('EarningsPerShareBasic', 'EarningsPerShareDiluted')

    n_filings = len(revenue) if revenue else 0
    if n_filings < 3:
        result = {'score': 0, 'breakdown': {}, 'n_filings': n_filings}
        try:
            cache_p.write_text(json.dumps(result))
        except Exception:
            pass
        return 0, {}, n_filings

    fcf = None
    if op_cf and capex and len(op_cf) >= 2 and len(capex) >= 2:
        ml  = min(len(op_cf), len(capex))
        fcf = [op_cf[-ml:][i] - capex[-ml:][i] for i in range(ml)]

    score = 0
    breakdown = {}

    # D1: Business Quality (0-25)
    d1 = 0
    if equity and net_income and len(equity) >= 3 and len(net_income) >= 3:
        roe_3y = [ni / max(eq, 1) for ni, eq in zip(net_income[-3:], equity[-3:])]
        if all(r > 0.15 for r in roe_3y):
            d1 += 5
            if roe_3y[-1] > 0.20:
                d1 += 5
    elif equity and net_income:
        roe = net_income[-1] / max(equity[-1], 1)
        if roe > 0.15: d1 += 3
        if roe > 0.20: d1 += 3
    if revenue and net_income:
        nm = net_income[-1] / max(revenue[-1], 1)
        if nm > 0.10: d1 += 5
    if revenue and len(revenue) >= 3:
        rev3 = revenue[-3:]
        if all(rev3[i] < rev3[i + 1] for i in range(len(rev3) - 1)):
            d1 += 5
    if gross_profit and revenue and len(gross_profit) >= 2 and len(revenue) >= 2:
        gm_p = gross_profit[-2] / max(revenue[-2], 1)
        gm_c = gross_profit[-1] / max(revenue[-1], 1)
        if gm_c >= gm_p * 0.98:
            d1 += 5
    score += d1
    breakdown['D1_quality'] = d1

    # D2: Financial Fortress (0-25)
    d2 = 0
    if equity and lt_debt:
        de = abs(lt_debt[-1]) / max(abs(equity[-1]), 1)
        if de < 0.5: d2 += 10
        if de < 0.2: d2 += 3
    if cur_assets and cur_liab and cur_assets[-1] and cur_liab[-1]:
        cr = cur_assets[-1] / max(cur_liab[-1], 1)
        if cr > 1.5: d2 += 5
    if op_income and int_expense and int_expense[-1] and int_expense[-1] > 0:
        ic = op_income[-1] / max(int_expense[-1], 1)
        if ic > 5: d2 += 5
    elif not int_expense or (int_expense and int_expense[-1] == 0):
        d2 += 5
    if fcf and len(fcf) >= 3 and all(f > 0 for f in fcf[-3:]):
        d2 += 5
    elif fcf and fcf[-1] > 0:
        d2 += 3
    score += d2
    breakdown['D2_fortress'] = d2

    # D3: Consistent Growth (0-20)
    d3 = 0
    if eps and len(eps) >= 3 and all(e > 0 for e in eps[-3:]):
        if _cagr_series(eps[-3:]) > 0.10: d3 += 7
    if revenue and len(revenue) >= 3:
        if _cagr_series(revenue[-3:]) > 0.08: d3 += 7
    if fcf and len(fcf) >= 2 and fcf[-1] > fcf[-2]:
        d3 += 6
    score += d3
    breakdown['D3_growth'] = d3

    # D4: Valuation (0-20)
    d4 = 0
    hist_price = _price_on_date(price_hist, f'{year}-01-01')
    if hist_price and hist_price > 0:
        if eps and eps[-1] > 0:
            # Adjust EPS for any split between the last fiscal year end and Jan 1 of the
            # scoring year: yfinance retroactively adjusts prices but EDGAR EPS are not.
            last_fy_end = f'{year - 2}-12-31'
            split_ratio = _get_split_ratio(ticker, last_fy_end, f'{year}-01-01')
            eps_adj = [e / split_ratio for e in eps] if split_ratio != 1.0 else eps
            pe = hist_price / eps_adj[-1]
            if pe < 25: d4 += 4
            if pe < 15: d4 += 4
            if len(eps_adj) >= 3 and all(e > 0 for e in eps_adj[-3:]):
                eps_g = _cagr_series(eps_adj[-3:]) * 100
                if eps_g > 0:
                    peg = pe / eps_g
                    if peg < 1.5: d4 += 4
                    if peg < 1.0: d4 += 3
        if fcf and fcf[-1] > 0: d4 += 3
        if revenue and revenue[-1] > 0: d4 += 2
    score += d4
    breakdown['D4_valuation'] = d4

    # D5: Momentum (0-10)
    d5 = 0
    cut_dt  = datetime(year, 1, 1)
    prev_d  = (cut_dt - timedelta(days=2)).strftime('%Y-%m-%d')
    dt_6m   = (cut_dt - timedelta(days=183)).strftime('%Y-%m-%d')
    dt_12m  = (cut_dt - timedelta(days=365)).strftime('%Y-%m-%d')
    p_now   = _price_on_date(price_hist, prev_d, window=10)
    p_6m    = _price_on_date(price_hist, dt_6m, window=10)
    p_12m   = _price_on_date(price_hist, dt_12m, window=10)
    if p_now and p_6m and p_6m > 0 and p_now > p_6m:    d5 += 5
    if p_now and p_12m and p_12m > 0 and p_now > p_12m: d5 += 5
    score += d5
    breakdown['D5_momentum'] = d5

    score = min(max(score, 0), 110)
    breakdown['total'] = score

    result = {'score': score, 'breakdown': breakdown, 'n_filings': n_filings}
    try:
        cache_p.write_text(json.dumps(result))
    except Exception:
        pass
    return score, breakdown, n_filings


# ── 8-K corporate event context ───────────────────────────────────────────────── #

_8K_ITEM_LABELS = {
    '1.01': 'Entry into Material Agreement',
    '1.02': 'Termination of Material Agreement',
    '1.03': 'Bankruptcy or Receivership',
    '2.01': 'Acquisition/Disposition completed',
    '2.02': 'Results of Operations / Earnings Release',
    '2.03': 'New Direct Financial Obligation',
    '2.04': 'Acceleration Events triggered',
    '2.05': 'Restructuring/Exit costs',
    '2.06': 'Material Impairment recognized',
    '3.01': 'Delisting notice',
    '4.01': 'Change of Accountant',
    '4.02': 'Financial Restatement',
    '5.01': 'Change of Control of Registrant',
    '5.02': 'Director or Officer departure/appointment',
    '5.03': 'Amendment to Articles of Incorporation',
    '7.01': 'Regulation FD / Investor Presentation',
    '8.01': 'Other material corporate event',
    '9.01': 'Financial Statements and Exhibits',
}


def _anonymize_text(text: str, ticker: str) -> str:
    """Remove company-identifying info from 8-K text."""
    text = re.sub(rf'\b{re.escape(ticker)}\b', 'Company_X', text, flags=re.IGNORECASE)
    text = re.sub(
        r'\b[A-Z][a-zA-Z]+\s+(Inc\.?|Corp\.?|Ltd\.?|LLC\.?|Co\.?|'
        r'Corporation|Company|Holdings|Group|Technologies|Technology|'
        r'Systems|Pharmaceuticals|Healthcare)\b',
        'Company_X', text,
    )
    text = re.sub(r'\$\d+\.?\d*\s*[Bb]illion', '$X billion', text)
    text = re.sub(r'\$\d+\.?\d*\s*[Mm]illion', '$X million', text)
    text = re.sub(r'\b(19|20)\d{2}\b', 'YEAR_X', text)
    text = re.sub(r'http[s]?://\S+', '[URL removed]', text)
    return text


def _get_recent_8k(ticker: str, cik: str, year: int) -> str:
    """
    Return a short anonymized description of the most recent 8-K before Jan 1, year.
    Uses EDGAR submissions API (same domain as XBRL). Cached per (ticker, year).
    """
    if not cik:
        return ''

    cache_path = K8_CACHE / f'{ticker}_{year}.txt'
    if cache_path.exists():
        return cache_path.read_text(encoding='utf-8')

    try:
        cik_padded = str(cik).zfill(10)
        url     = f'https://data.sec.gov/submissions/CIK{cik_padded}.json'
        headers = {'User-Agent': 'sp500-backtest research@example.com'}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            cache_path.write_text('', encoding='utf-8')
            return ''

        data      = r.json()
        filings   = data.get('filings', {}).get('recent', {})
        forms     = filings.get('form', [])
        dates     = filings.get('filingDate', [])
        items_arr = filings.get('items', [])

        cutoff = f'{year}-01-01'
        found  = None

        for i, (form, date) in enumerate(zip(forms, dates)):
            if form not in ('8-K', '8-K/A'):
                continue
            if date >= cutoff:
                continue
            items_raw = items_arr[i] if i < len(items_arr) else ''
            found = {'date': date, 'items': str(items_raw)}
            break

        if not found:
            cache_path.write_text('', encoding='utf-8')
            return ''

        codes  = [c.strip() for c in str(found['items']).split(',') if c.strip()]
        labels = [_8K_ITEM_LABELS.get(c, f'Item {c}') for c in codes
                  if c not in ('9.01',)]   # skip exhibits-only item
        if not labels and codes:
            labels = [f'Item {c}' for c in codes]

        items_desc = '; '.join(labels) if labels else 'unspecified items'
        raw_text   = f"8-K filed {found['date']}: {items_desc}"
        result     = _anonymize_text(raw_text, ticker)[:500]

        cache_path.write_text(result, encoding='utf-8')
        return result

    except Exception:
        return ''


# ── Blind AI context helpers (zero data leakage) ─────────────────────────────── #

def _sic_sector_label(sic) -> str:
    """Map an SEC SIC code to a generic, anonymous sector bucket."""
    try:
        code = int(sic)
    except (ValueError, TypeError):
        return 'Diversified company'
    if code < 1000:  return 'Agriculture/Mining sector company'
    if code < 1500:  return 'Energy/Mining sector company'
    if code < 1800:  return 'Construction sector company'
    if code < 4000:  return 'Manufacturing/Industrial company'
    if code < 4900:  return 'Transportation sector company'
    if code < 5000:  return 'Utility sector company'
    if code < 5200:  return 'Wholesale trade company'
    if code < 6000:  return 'Retail sector company'
    if code < 6800:  return 'Financial services company'
    if code < 9000:  return 'Services sector company'
    return 'Diversified company'


def _get_company_profile(ticker: str, cik: str) -> tuple:
    """
    Anonymous (sector_label, business_description) derived ONLY from the SEC
    SIC code/description — never the company name. Cached per ticker forever
    (sector classification doesn't change year to year).
    """
    cache_path = BIZ_CACHE / f'{ticker}.json'
    if cache_path.exists():
        try:
            d = json.loads(cache_path.read_text(encoding='utf-8'))
            return d.get('sector', 'Diversified company'), d.get('biz', '')
        except Exception:
            pass

    sector_label = 'Diversified company'
    biz_desc     = ''
    if cik:
        try:
            cik_padded = str(cik).zfill(10)
            url        = f'https://data.sec.gov/submissions/CIK{cik_padded}.json'
            headers    = {'User-Agent': 'sp500-backtest research@example.com'}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                d            = r.json()
                sector_label = _sic_sector_label(d.get('sic', ''))
                sic_desc     = d.get('sicDescription', '')
                if sic_desc:
                    biz_desc = _anonymize_text(
                        f'Primary business category: {sic_desc}', ticker)[:300]
        except Exception:
            pass

    try:
        cache_path.write_text(json.dumps({'sector': sector_label, 'biz': biz_desc}),
                              encoding='utf-8')
    except Exception:
        pass
    return sector_label, biz_desc


def _get_capex_context(gaap: dict, cutoff: str) -> str:
    """Anonymous CapEx-trend label with % change — numbers only, no names."""
    try:
        def _av(*fields, n=3):
            for f in fields:
                if f in gaap:
                    r = _annual_values_pit(gaap[f], cutoff, n)
                    if r:
                        return r
            return None

        capex = _av('PaymentsToAcquirePropertyPlantAndEquipment',
                    'CapitalExpendituresIncurringDebt', 'PaymentsForCapitalImprovements')
        if not capex or len(capex) < 2:
            return 'CapEx trend: insufficient data'

        c0, c1 = abs(capex[-2]), abs(capex[-1])
        pct = (c1 / c0 - 1) * 100 if c0 else 0.0
        if pct > 100:
            label = 'HEAVY INVESTMENT YEAR'
        elif pct > 30:
            label = 'ELEVATED INVESTMENT'
        elif pct < -20:
            label = 'INVESTMENT DECLINING'
        else:
            label = 'STABLE INVESTMENT'
        return f'{label}: capital expenditure changed {pct:+.0f}% vs prior year'
    except Exception:
        return 'CapEx data unavailable'


def _fcf_positive_and_growing(gaap: dict, cutoff: str) -> bool:
    """True if Free Cash Flow (OCF - CapEx) is positive AND grew vs prior year."""
    try:
        def _av(*fields, n=3):
            for f in fields:
                if f in gaap:
                    r = _annual_values_pit(gaap[f], cutoff, n)
                    if r:
                        return r
            return None

        ocf = _av('NetCashProvidedByUsedInOperatingActivities')
        if not ocf or len(ocf) < 2:
            return False
        capex = _av('PaymentsToAcquirePropertyPlantAndEquipment',
                    'CapitalExpendituresIncurringDebt', 'PaymentsForCapitalImprovements')
        if not capex:
            capex = [0.0] * len(ocf)
        ml  = min(len(ocf), len(capex))
        fcf = [ocf[-ml:][i] - abs(capex[-ml:][i]) for i in range(ml)]
        return len(fcf) >= 2 and fcf[-1] > 0 and fcf[-1] > fcf[-2]
    except Exception:
        return False


_FORBIDDEN_NAMES = [
    'apple', 'amazon', 'google', 'alphabet', 'meta', 'facebook', 'nvidia',
    'microsoft', 'netflix', 'tesla', 'walmart', 'exxon', 'oracle', 'intel',
    'mastercard', 'visa', 'nike', 'starbucks', 'disney', 'boeing', 'jpmorgan',
    'berkshire', 'costco', 'home depot', 'pepsi', 'coca-cola', 'mcdonald',
]


def _validate_no_leakage(reason_text: str, ticker: str) -> str:
    """Scrub the AI's reason of any company/ticker identifiers before use."""
    lowered = (reason_text or '').lower()
    if not lowered:
        return reason_text
    if ticker.lower() in lowered or re.search(rf'\b{re.escape(ticker)}\b', reason_text):
        return 'Financial metrics indicate this decision (numbers only, no company names).'
    for name in _FORBIDDEN_NAMES:
        if name in lowered:
            return 'Financial metrics indicate this decision (numbers only, no company names).'
    return reason_text


# ── AI sell confirmation (BLIND — zero data leakage) ──────────────────────────── #

def _ai_sell_confirm(ticker: str, year: int, reason: str, cost_tracker: list,
                     buy_year: int, buy_price: float, cur_price: float,
                     held_years: int, gaap: dict, hold_count: int = 0) -> tuple:
    """
    Ask Claude Haiku whether to HOLD or SELL a profitable position despite a
    thesis break. The prompt is fully ANONYMIZED: no ticker, company name, or
    exact year is ever sent — only an anonymous sector label, numeric financial
    trends, generic business/macro/event context, and the rule trigger text.
    Returns (decision, confidence, ai_reason).
      decision:   'SELL', 'HOLD', or None if unavailable / budget exceeded.
      confidence: 0-100 integer.
      ai_reason:  short explanation, scrubbed of any identifying info.
    cost_tracker is [float] mutable so cumulative spend is tracked across calls.
    """
    global _ai_cost_used

    if cost_tracker[0] >= AI_COST_CAP:
        return None, 0, 'budget cap reached'

    cik       = get_cik(ticker)
    recent_8k = _get_recent_8k(ticker, cik, year)
    k8_status = 'found' if recent_8k and len(recent_8k) > 20 else 'none'
    print(f'  8-K context: {k8_status}')

    cache_p = AI_SELL_CACHE / f'{ticker}_{year}.json'
    if cache_p.exists():
        try:
            d = json.loads(cache_p.read_text())
            cached_reason = _validate_no_leakage(_san(d.get('reason', '')), ticker)
            return d.get('decision'), d.get('confidence', 0), cached_reason
        except Exception:
            pass

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None, 0, 'no API key'

    # Build financial trend strings from GAAP (PIT as of Jan 1, year)
    cutoff = f'{year}-01-01'

    def _av_pit(*fields, n=4):
        for f in fields:
            if f in gaap:
                r = _annual_values_pit(gaap[f], cutoff, n)
                if r:
                    return r
        return None

    revenue    = _av_pit('RevenueFromContractWithCustomerExcludingAssessedTax',
                         'Revenues', 'SalesRevenueNet', 'SalesRevenueGoodsNet')
    net_income = _av_pit('NetIncomeLoss', 'ProfitLoss')
    equity     = _av_pit('StockholdersEquity', 'StockholdersEquityAttributableToParent')
    lt_debt    = _av_pit('LongTermDebt', 'LongTermDebtNoncurrent')
    op_cf      = _av_pit('NetCashProvidedByUsedInOperatingActivities')
    capex      = _av_pit('PaymentsToAcquirePropertyPlantAndEquipment',
                         'CapitalExpendituresIncurringDebt', 'PaymentsForCapitalImprovements')

    fcf_vals = None
    if op_cf and capex:
        ml = min(len(op_cf), len(capex))
        if ml >= 2:
            fcf_vals = [op_cf[-ml:][i] - abs(capex[-ml:][i]) for i in range(ml)]

    def _scaled(vals):
        scale = 1e9 if max(abs(v) for v in vals[-3:]) > 1e8 else 1e6
        suf   = 'B' if scale == 1e9 else 'M'
        return ' -> '.join(f'${v / scale:.1f}{suf}' for v in vals[-3:])

    def _rev_str():
        return _scaled(revenue) if revenue else 'N/A'

    def _roe_str():
        if not net_income or not equity:
            return 'N/A'
        pairs = list(zip(net_income[-3:], equity[-3:]))
        return ' -> '.join(f'{ni / max(eq, 1) * 100:.0f}%' for ni, eq in pairs)

    def _de_str():
        if not lt_debt or not equity:
            return 'N/A'
        pairs = list(zip(lt_debt[-3:], equity[-3:]))
        return ' -> '.join(f'{abs(d) / max(abs(e), 1):.2f}x' for d, e in pairs)

    def _fcf_str():
        return _scaled(fcf_vals) if fcf_vals else 'N/A'

    ret_pct                = cur_price / buy_price - 1 if buy_price > 0 else 0.0
    sector_label, biz_desc = _get_company_profile(ticker, cik)
    capex_ctx              = _get_capex_context(gaap, cutoff)
    macro                  = _MACRO_CONTEXT.get(year, 'Standard market conditions; no notable regime shift.')
    event_ctx              = recent_8k if (recent_8k and len(recent_8k) > 20) \
                             else 'No significant events found in recent filings.'

    prompt = (
        'You are a strict quantitative analyst reviewing a hold/sell decision.\n\n'
        'CRITICAL: This is a BLIND review. You are NOT told the company name or '
        'ticker — base your decision ONLY on the anonymous data below. Do not '
        'guess which company this is.\n\n'
        '=== ANONYMOUS POSITION ===\n'
        f'Sector: {sector_label}\n'
        f'Years held: {held_years}\n'
        f'Return so far: {ret_pct:+.0%}\n'
        f'Sell trigger detected: {reason}\n'
        f'Prior AI hold overrides on this position: {hold_count}/2 max\n\n'
        '=== FINANCIAL TRENDS (most recent 3 years, oldest -> newest) ===\n'
        f'- Revenue: {_rev_str()}\n'
        f'- ROE: {_roe_str()}\n'
        f'- Debt/Equity: {_de_str()}\n'
        f'- Free Cash Flow (OCF - CapEx): {_fcf_str()}\n\n'
        f'=== CAPITAL INVESTMENT TREND ===\n{capex_ctx}\n\n'
        '=== BUSINESS CONTEXT (anonymized) ===\n'
        f'{biz_desc or "Standard operations for this sector."}\n\n'
        f'=== MACRO ENVIRONMENT AT TIME OF REVIEW ===\n{macro}\n\n'
        '=== MOST RECENT CORPORATE FILING EVENT (anonymized) ===\n'
        f'{event_ctx}\n\n'
        '=== DECISION GUIDANCE ===\n'
        'Lean HOLD when:\n'
        '- Free cash flow is positive and growing despite a falling ROE (signals an '
        'investment cycle, not deterioration)\n'
        '- The revenue/earnings trend is consistent with the macro environment above\n'
        '- The capital-investment trend explains margin or ROE compression\n'
        '- The recent filing event suggests a one-time or transitional item\n'
        '- Debt growth looks deliberate (buybacks/expansion) alongside stable or rising FCF\n\n'
        'Lean SELL when:\n'
        '- Revenue AND free cash flow are both shrinking together\n'
        '- ROE is collapsing with no offsetting rise in capital investment or FCF\n'
        '- Debt is climbing while revenue stalls or declines\n'
        '- The trend spans multiple years with no sign of stabilization\n'
        '- This is already hold #2 and there is no clear improvement in the numbers above\n\n'
        'IMPORTANT: Do NOT name any company, ticker, brand, or product in your reason. '
        'Reference ONLY the sector label, numbers, and trend labels given above.\n\n'
        'Reply ONLY in this exact JSON:\n'
        '{"decision": "HOLD" or "SELL", "confidence": 0-100, '
        '"reason": "max 12 words, numbers/labels only, absolutely no company names"}'
    )

    try:
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': AI_MODEL,
                'max_tokens': 100,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=20,
        )

        if resp.status_code != 200:
            return None, 0, f'HTTP {resp.status_code}'

        data      = resp.json()
        usage     = data.get('usage', {})
        call_cost = (usage.get('input_tokens', 0) * AI_INPUT_CPM +
                     usage.get('output_tokens', 0) * AI_OUTPUT_CPM)
        cost_tracker[0] += call_cost
        _ai_cost_used   += call_cost

        text = data.get('content', [{}])[0].get('text', '')
        m    = re.search(r'\{.*?\}', text, re.DOTALL)
        if m:
            parsed     = json.loads(m.group())
            decision   = parsed.get('decision', 'SELL').upper()
            confidence = int(parsed.get('confidence', 50))
            ai_reason  = _validate_no_leakage(_san(parsed.get('reason', '')), ticker)
            if decision not in ('SELL', 'HOLD'):
                decision = 'SELL'
            try:
                cache_p.write_text(json.dumps({
                    'decision':   decision,
                    'confidence': confidence,
                    'reason':     ai_reason,
                    'cost':       call_cost,
                }))
            except Exception:
                pass
            return decision, confidence, ai_reason
    except Exception:
        pass
    return None, 0, 'error'


# ── Helpers ───────────────────────────────────────────────────────────────────── #

def _san(s: str) -> str:
    """Sanitize AI-generated text for Windows cp1255 output."""
    return (str(s)
            .replace('→', '->')   # right arrow
            .replace('←', '<-')   # left arrow
            .replace('—', '-')    # em dash
            .replace('–', '-')    # en dash
            .replace('•', '*')    # bullet
            .replace('’', "'")    # right single quote
            .replace('‘', "'")    # left single quote
            .replace('“', '"')    # left double quote
            .replace('”', '"')    # right double quote
            .encode('ascii', 'replace').decode('ascii'))


def _slip(year: int) -> float:
    """Slippage: 0.3% before 2010, 0.1% from 2010 onward."""
    return 0.003 if year < 2010 else 0.001


def _cagr(start: float, end: float, years: float) -> float:
    """Point-to-point CAGR from start to end over years."""
    if start <= 0 or end <= 0 or years <= 0:
        return 0.0
    return (end / start) ** (1 / years) - 1


def _compound(returns: list) -> float:
    """Compound a list of annual decimal returns."""
    v = 1.0
    for r in returns:
        v *= (1 + r)
    return v - 1


# ── Progress ──────────────────────────────────────────────────────────────────── #

def _save_progress(year: int, portfolio: dict, cash: float,
                   year_results: list, sells_log: list, ai_log: list):
    try:
        SP500_PROGRESS.write_text(json.dumps({
            'last_completed_year': year,
            'portfolio':    portfolio,
            'cash':         cash,
            'year_results': year_results,
            'sells_log':    sells_log,
            'ai_log':       ai_log,
        }, indent=2))
    except Exception:
        pass


# ── Position concentration cap ────────────────────────────────────────────────── #

def apply_position_cap(portfolio: dict, price_hist: dict, year: int,
                       cash: float, slippage: float, tax_rate: float,
                       sells_log: list) -> tuple:
    """
    Trim any year-end position exceeding MAX_POSITION_WEIGHT (25%) of total
    portfolio value. Sells ONLY the excess shares (down to exactly the cap),
    applying slippage on the proceeds and capital-gains tax on the profitable
    portion. Logs each trim to sells_log with 'cap_trim': True.
    Returns (portfolio, cash, trim_tax).
    """
    dec31 = f'{year}-12-31'

    total_value = cash
    priced      = {}
    for ticker, pos in portfolio.items():
        ph = price_hist.get(ticker, {})
        px = _price_on_date(ph, dec31) or _last_price_before(ph, dec31)
        if px:
            priced[ticker] = px
            total_value   += pos['shares'] * px

    trimmed  = False
    trim_tax = 0.0
    if total_value <= 0:
        return portfolio, cash, trim_tax

    for ticker, px in priced.items():
        pos    = portfolio[ticker]
        val    = pos['shares'] * px
        weight = val / total_value
        if weight <= MAX_POSITION_WEIGHT:
            continue

        trimmed        = True
        target_val     = total_value * MAX_POSITION_WEIGHT
        excess_val     = val - target_val
        shares_to_sell = excess_val / px
        proceeds       = shares_to_sell * px * (1 - slippage)

        tax = 0.0
        if px > pos['buy_price']:
            profit    = shares_to_sell * (px - pos['buy_price'])
            tax       = profit * tax_rate
            trim_tax += tax
            proceeds -= tax

        pos['shares'] -= shares_to_sell
        cash          += proceeds

        print(f'  [25% CAP] {ticker}: weight {weight:.0%} -> {MAX_POSITION_WEIGHT:.0%} | '
              f'trimmed {shares_to_sell:,.1f} sh @ ${px:,.2f} | tax: ${tax:,.0f}')

        sells_log.append({
            'year':       year,
            'ticker':     ticker,
            'reason':     f'Position cap trim: {weight:.0%} -> {MAX_POSITION_WEIGHT:.0%}',
            'buy_year':   pos['buy_year'],
            'buy_price':  pos['buy_price'],
            'sell_price': px,
            'return_pct': (px / pos['buy_price'] - 1) * 100 if pos['buy_price'] else 0.0,
            'held_years': year - pos['buy_year'],
            'cap_trim':   True,
        })

    if not trimmed:
        print('  [25% CAP] No positions exceeded the 25% cap.')

    return portfolio, cash, trim_tax


# ── Main backtest ─────────────────────────────────────────────────────────────── #

def run_sp500_backtest():
    """TRUE S&P 500 point-in-time backtest 2006-2026."""
    global _ai_cost_used

    # Always start fresh
    if SP500_PROGRESS.exists():
        SP500_PROGRESS.unlink()
        print('Deleted stale progress file - starting fresh.')

    _vix.QUICK_MODE    = True
    _vix.EDGAR_TIMEOUT = 3
    _vix.NUM_WORKERS   = 10

    print('=' * 65)
    print(f'  S&P 500 HISTORICAL BACKTEST  {BACKTEST_START}-2026')
    print('  Wikipedia constituents + EDGAR PIT (45-day lag) + AI sells')
    print('  NOTE: Partial survivorship bias (current S&P 500 as base)')
    print('=' * 65)
    print()
    print('=== UPGRADES ACTIVE ===')
    print(f'  [1] {MAX_POSITION_WEIGHT:.0%} position cap trimmed at year-end (esp. mega-winners)')
    print('  [2] FCF override: positive+growing FCF can veto an ROE-collapse sell')
    print('  [3] Max 2 AI HOLD overrides per stock, then forced sell w/o AI review')
    print('  [4] Blind AI context: anonymous sector + capex + business + 8-K + macro')
    print('  [5] Leakage validator scrubs AI reasons of any company/ticker names')
    print(f'  [6] Backtest window starts {BACKTEST_START} (was 2006)')
    print(f'  [7] AI budget cap: ${AI_COST_CAP:,.2f}')
    print()
    print('=== COST ESTIMATE ===')
    print('  Expected AI reviews: ~30-50 (based on prior-run thesis-break frequency)')
    print('  Expected cost: ~$0.02-0.05 (Haiku 4.5 pricing, richer blind prompt)')
    print(f'  Budget remaining: ${AI_COST_CAP:,.2f}')
    print()

    print('Loading CIK map...')
    _load_cik_map()

    print('Pre-fetching SPY / QQQ price history...')
    spy_prices = _get_price_cache('SPY')
    qqq_prices = _get_price_cache('QQQ')

    # portfolio: {ticker: {shares, buy_price, buy_year, initial_metrics, score}}
    portfolio       = {}
    cash            = INITIAL_CAPITAL
    year_results    = []
    sells_log       = []
    ai_log          = []
    ai_cost_tracker = [0.0]    # mutable for pass-by-reference to _ai_sell_confirm
    all_price_hists = {}       # accumulated across all years

    for year in range(BACKTEST_START, 2027):
        print(f'\n{"="*60}\nYEAR {year}\n{"="*60}')

        jan1  = f'{year}-01-01'
        dec31 = f'{year}-12-31'
        slip  = _slip(year)

        universe = get_sp500_for_year(year)
        print(f'  Universe: {len(universe)} S&P 500 stocks')

        # Pre-fetch price histories for any new tickers
        need_ph = (set(universe) | set(portfolio.keys())) - set(all_price_hists.keys())
        if need_ph:
            print(f'  Fetching price histories ({len(need_ph)} new tickers)...')
            def _fetch_ph(t):
                return t, _get_price_cache(t)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                for t, ph in ex.map(_fetch_ph, sorted(need_ph)):
                    all_price_hists[t] = ph

        # ── Step 1: Thesis checks on existing holdings ────────────────────────── #
        tickers_to_sell = []
        for ticker in list(portfolio.keys()):
            cik = get_cik(ticker)
            if not cik:
                continue
            gaap = get_xbrl_facts(cik)
            if not gaap:
                continue
            ph = all_price_hists.get(ticker, {})

            broke, reason = check_thesis_break(
                year, portfolio[ticker]['initial_metrics'], gaap, ph, ticker=ticker)

            if not broke:
                portfolio[ticker]['ai_hold_count'] = 0   # clean year — reset counter
                continue

            # UPGRADE 2: FCF override — positive & growing FCF can veto an
            # ROE-collapse trigger (handles Amazon/Netflix-style heavy investment)
            cutoff_fcf = f'{year}-01-01'
            if reason.startswith('ROE collapsed') and _fcf_positive_and_growing(gaap, cutoff_fcf):
                print(f'  FCF OVERRIDE: keeping {ticker} despite ROE drop — FCF positive+growing')
                continue

            pos         = portfolio[ticker]
            cur_price   = _price_on_date(ph, jan1) or _last_price_before(ph, jan1)
            held_years  = year - pos['buy_year']
            profitable  = cur_price and cur_price > pos['buy_price']
            hold_count  = pos.get('ai_hold_count', 0)

            print(f'  AI REVIEW: {ticker} - trigger: {reason}')

            # UPGRADE 3: Max 2 HOLD overrides — 3rd thesis break forces a sell
            if hold_count >= 2:
                print(f'  FORCED SELL: {ticker} — 2 prior HOLD overrides exhausted')
                pos['ai_hold_count'] = 0
                tickers_to_sell.append((ticker, reason))
                continue

            # AI reviews ALL profitable positions; losing positions sell immediately
            ai_decision = ai_conf = ai_rsn = None
            if profitable and ai_cost_tracker[0] < AI_COST_CAP:
                ai_decision, ai_conf, ai_rsn = _ai_sell_confirm(
                    ticker, year, reason, ai_cost_tracker,
                    pos['buy_year'], pos['buy_price'], cur_price,
                    held_years, gaap, hold_count,
                )
            elif ai_cost_tracker[0] >= AI_COST_CAP:
                print('  [WARN] AI budget cap reached - rule-based sell.')

            rsn_safe = _san(ai_rsn or '')
            if ai_decision == 'HOLD':
                pos['ai_hold_count'] = hold_count + 1
                print(f'  AI DECISION: HOLD ({ai_conf}%) - {rsn_safe}')
                ai_log.append({
                    'year':       year,
                    'ticker':     ticker,
                    'reason':     reason,
                    'decision':   'HOLD',
                    'confidence': ai_conf,
                    'ai_reason':  rsn_safe,
                    'cum_cost':   ai_cost_tracker[0],
                    'sell_price': cur_price,
                    'buy_price':  pos['buy_price'],
                    'shares':     pos['shares'],
                })
            else:
                pos['ai_hold_count'] = 0
                if ai_decision == 'SELL':
                    print(f'  AI DECISION: SELL ({ai_conf}%) - {rsn_safe}')
                elif not profitable:
                    print(f'  SELL (losing position, no AI review)')
                else:
                    print(f'  SELL (AI unavailable)')
                tickers_to_sell.append((ticker, reason))

        # Execute sells
        tax_paid = 0.0
        for ticker, reason in tickers_to_sell:
            pos     = portfolio.pop(ticker)
            ph      = all_price_hists.get(ticker, {})
            sell_px = _price_on_date(ph, jan1) or _last_price_before(ph, jan1)
            if not sell_px:
                portfolio[ticker] = pos   # no price — cannot sell
                continue

            sell_px  *= (1 - slip)
            proceeds  = pos['shares'] * sell_px
            gain      = proceeds - pos['shares'] * pos['buy_price']

            if gain > 0:
                tax       = gain * ISRAEL_CGT
                tax_paid += tax
                proceeds -= tax

            cash += proceeds
            ret   = sell_px / pos['buy_price'] - 1

            sells_log.append({
                'year':       year,
                'ticker':     ticker,
                'reason':     reason,
                'buy_year':   pos['buy_year'],
                'buy_price':  pos['buy_price'],
                'sell_price': sell_px,
                'return_pct': ret * 100,
                'held_years': year - pos['buy_year'],
            })
            print(f'  SELL {ticker}: {reason[:48]} | {ret:+.0%} held {year-pos["buy_year"]}yr')

        # ── Step 2: Score universe and fill slots ─────────────────────────────── #
        n_held   = len(portfolio)
        max_new  = 20 if year == BACKTEST_START else (SP500_MAX_POS - n_held)
        existing = set(portfolio.keys())

        if max_new > 0 and cash > 0:
            print(f'  Scoring {len(universe)} stocks (slots to fill: {max_new})...')
            candidates = []

            def _score_one(ticker):
                if ticker in existing:
                    return None
                cik = get_cik(ticker)
                if not cik:
                    return None
                gaap = get_xbrl_facts(cik)
                if not gaap:
                    return None
                ph = all_price_hists.get(ticker, {})
                s, b, n = score_layer1_sp500(ticker, year, gaap, ph)
                if s >= PIT_L1_PASS:
                    return (ticker, s, gaap, ph)
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                for result in ex.map(_score_one, universe):
                    if result:
                        candidates.append(result)

            candidates.sort(key=lambda x: x[1], reverse=True)
            print(f'  Qualified (>={PIT_L1_PASS}): {len(candidates)} stocks')

            # Equal-weight allocation across slots
            n_to_buy = min(max_new, len(candidates))
            if n_to_buy > 0:
                per_pos = cash / n_to_buy
                bought  = 0

                # 45-day lag cutoff for initial_metrics
                cutoff = (datetime(year, 1, 1) - timedelta(days=45)).strftime('%Y-%m-%d')

                for ticker, score, gaap, ph in candidates:
                    if bought >= max_new:
                        break
                    buy_px = _price_on_date(ph, jan1)
                    if not buy_px or buy_px <= 0:
                        continue
                    buy_px_adj = buy_px * (1 + slip)
                    shares     = per_pos / buy_px_adj
                    if shares <= 0 or cash < per_pos:
                        continue

                    init_m = _get_pit_metrics(gaap, cutoff, ph, ticker=ticker)

                    portfolio[ticker] = {
                        'shares':          shares,
                        'buy_price':       buy_px_adj,
                        'buy_year':        year,
                        'initial_metrics': init_m,
                        'score':           score,
                        'ai_hold_count':   0,
                    }
                    cash   -= per_pos
                    bought += 1
                    print(f'  BUY {ticker}: score={score} px=${buy_px:.2f}')

        # ── UPGRADE 1: Enforce 25% position cap before year-end valuation ─────── #
        print('  Checking 25% position cap...')
        portfolio, cash, trim_tax = apply_position_cap(
            portfolio, all_price_hists, year, cash, slip, ISRAEL_CGT, sells_log)
        tax_paid += trim_tax

        # ── Step 3: Year-end valuation ─────────────────────────────────────────── #
        portfolio_value = cash
        holdings_val    = {}

        for ticker, pos in portfolio.items():
            ph = all_price_hists.get(ticker, {})
            ep = _price_on_date(ph, dec31) or _last_price_before(ph, dec31)
            if ep:
                val                  = pos['shares'] * ep
                portfolio_value     += val
                holdings_val[ticker] = val

        prev_val = year_results[-1]['portfolio_value'] if year_results else INITIAL_CAPITAL
        yr_ret   = portfolio_value / prev_val - 1

        s_p0 = _price_on_date(spy_prices, jan1)
        s_p1 = _price_on_date(spy_prices, dec31) or _last_price_before(spy_prices, dec31)
        q_p0 = _price_on_date(qqq_prices, jan1)
        q_p1 = _price_on_date(qqq_prices, dec31) or _last_price_before(qqq_prices, dec31)

        spy_ret = (s_p1 / s_p0 - 1) if s_p0 and s_p1 else 0.0
        qqq_ret = (q_p1 / q_p0 - 1) if q_p0 and q_p1 else 0.0

        top5 = sorted(holdings_val.items(), key=lambda x: x[1], reverse=True)[:5]

        year_results.append({
            'year':            year,
            'portfolio_value': portfolio_value,
            'year_return':     yr_ret,
            'spy_return':      spy_ret,
            'qqq_return':      qqq_ret,
            'tax_paid':        tax_paid,
            'n_holdings':      len(portfolio),
            'holdings':        list(portfolio.keys()),
            'top5':            [[t, v] for t, v in top5],
        })

        print(f'  Value: ${portfolio_value:,.0f} | Ret: {yr_ret:+.1%} | '
              f'SPY: {spy_ret:+.1%} | QQQ: {qqq_ret:+.1%} | Tax: ${tax_paid:,.0f} | '
              f'AI cost: ${ai_cost_tracker[0]:.4f}')

        _save_progress(year, portfolio, cash, year_results, sells_log, ai_log)

    print_sp500_results(
        year_results, sells_log, ai_log,
        portfolio, all_price_hists, spy_prices, qqq_prices,
    )


# ── Output formatting ─────────────────────────────────────────────────────────── #

def print_sp500_results(year_results: list, sells_log: list, ai_log: list,
                        final_portfolio: dict, all_price_hists: dict,
                        spy_prices: dict, qqq_prices: dict):
    if not year_results:
        print('No results to display.')
        return

    n_years   = len(year_results)
    final_val = year_results[-1]['portfolio_value']

    # Overall stats
    strat_cagr  = _cagr(INITIAL_CAPITAL, final_val, n_years)
    strat_total = final_val / INITIAL_CAPITAL - 1

    spy_s = _price_on_date(spy_prices, f'{BACKTEST_START}-01-01')
    spy_e = _last_price_before(spy_prices, '2027-01-01')
    qqq_s = _price_on_date(qqq_prices, f'{BACKTEST_START}-01-01')
    qqq_e = _last_price_before(qqq_prices, '2027-01-01')

    spy_cagr  = _cagr(spy_s, spy_e, n_years) if spy_s and spy_e else 0.0
    qqq_cagr  = _cagr(qqq_s, qqq_e, n_years) if qqq_s and qqq_e else 0.0
    spy_total = spy_e / spy_s - 1 if spy_s and spy_e else 0.0
    qqq_total = qqq_e / qqq_s - 1 if qqq_s and qqq_e else 0.0

    total_tax  = sum(yr.get('tax_paid', 0) for yr in year_results)
    years_beat = sum(1 for yr in year_results
                     if yr.get('year_return', 0) > yr.get('spy_return', 0))

    # Max drawdown (annual approximation)
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    for yr in year_results:
        v = yr['portfolio_value']
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > max_dd: max_dd = dd

    spy_peak = 0.0
    spy_max_dd = 0.0
    for yr in year_results:
        v = (_price_on_date(spy_prices, f'{yr["year"]}-12-31')
             or _last_price_before(spy_prices, f'{yr["year"]}-12-31'))
        if v:
            if v > spy_peak: spy_peak = v
            dd = (spy_peak - v) / spy_peak if spy_peak > 0 else 0.0
            if dd > spy_max_dd: spy_max_dd = dd

    # ── Main summary table ─────────────────────────────────────────────────────── #
    print('\n' + '=' * 66)
    print('S&P 500 HISTORICAL BACKTEST  2006-2026  (Wikipedia + EDGAR PIT)')
    print('=' * 66)
    print(f'{"Metric":<32} {"Strategy":>10} {"SPY":>10} {"QQQ":>10}')
    print('-' * 66)
    print(f'{"Total Return":<32} {strat_total:>+9.0%} {spy_total:>+9.0%} {qqq_total:>+9.0%}')
    print(f'{"CAGR":<32} {strat_cagr:>+9.1%} {spy_cagr:>+9.1%} {qqq_cagr:>+9.1%}')
    print(f'{"Final Value":<32} ${final_val:>9,.0f}')
    print(f'{"Max Drawdown (annual approx)":<32} {-max_dd:>9.1%} {-spy_max_dd:>9.1%}')
    print(f'{"Years Beating SPY":<32} {years_beat:>9}/{n_years}')
    print(f'{"Total Tax Paid (25% CGT)":<32} ${total_tax:>9,.0f}')
    print(f'{"Total AI Cost":<32} ${_ai_cost_used:>8.4f} / ${AI_COST_CAP:.2f} cap')

    # ── Year-by-year table ─────────────────────────────────────────────────────── #
    print('\n' + '=' * 66)
    print('YEAR-BY-YEAR PERFORMANCE')
    print('=' * 66)
    print(f'{"Year":<6} {"Strategy":>10} {"SPY":>8} {"QQQ":>8} {"Value":>12} '
          f'{"Tax":>8} {"Hold":>5}')
    print('-' * 60)
    for yr in year_results:
        y     = yr['year']
        yr_r  = yr.get('year_return', 0)
        spy_r = yr.get('spy_return', 0)
        qqq_r = yr.get('qqq_return', 0)
        tax   = yr.get('tax_paid', 0)
        n_h   = yr.get('n_holdings', 0)
        pv    = yr['portfolio_value']
        beat  = '*' if yr_r > spy_r else ' '
        print(f'{y:<6} {yr_r:>+9.1%}{beat} {spy_r:>+7.1%} {qqq_r:>+7.1%} '
              f'${pv:>11,.0f} ${tax:>6,.0f} {n_h:>4}')

    # ── Stress test: crisis drawdowns ─────────────────────────────────────────── #
    print('\n' + '=' * 66)
    print('STRESS TEST - CRISIS DRAWDOWNS')
    print('=' * 66)
    print(f'{"Crisis":<24} {"Strategy":>10} {"SPY":>10} {"QQQ":>10}')
    print('-' * 56)

    crises = [
        ('GFC 2007-2009',   2007, 2009),
        ('COVID Mar 2020',  2020, 2020),
        ('Rate Hike 2022',  2022, 2022),
    ]
    for name, s_yr, e_yr in crises:
        in_range = [yr for yr in year_results if s_yr <= yr['year'] <= e_yr]
        if not in_range:
            continue
        sc = _compound([yr.get('year_return', 0) for yr in in_range])
        sp = _compound([yr.get('spy_return', 0)  for yr in in_range])
        qc = _compound([yr.get('qqq_return', 0)  for yr in in_range])
        print(f'{name:<24} {sc:>+9.1%} {sp:>+9.1%} {qc:>+9.1%}')

    # ── Top 5 holdings per year ────────────────────────────────────────────────── #
    print('\n' + '=' * 66)
    print('TOP 5 HOLDINGS PER YEAR (by value)')
    print('=' * 66)
    for yr in year_results:
        top5 = yr.get('top5', [])
        if not top5:
            continue
        parts = ', '.join(f'{t}(${v:,.0f})' for t, v in top5)
        print(f'[{yr["year"]}] {parts}')

    # ── Thesis breaks log ─────────────────────────────────────────────────────── #
    if sells_log:
        print('\n' + '=' * 66)
        print('THESIS BREAKS LOG')
        print('=' * 66)
        print(f'{"Year":<6} {"Ticker":<7} {"Return":>8} {"Held":>5}  {"Reason"}')
        print('-' * 66)
        for s in sells_log:
            print(f'{s["year"]:<6} {s["ticker"]:<7} {s["return_pct"]:>+7.0f}% '
                  f'{s["held_years"]:>4}yr  {s["reason"][:42]}')

    # ── AI OVERRIDE IMPACT ────────────────────────────────────────────────────── #
    holds = [a for a in ai_log if a.get('decision') == 'HOLD']
    print('\n' + '=' * 66)
    print('AI OVERRIDE IMPACT')
    print('=' * 66)
    if not holds:
        print('  No HOLD overrides triggered during backtest.')
    else:
        print(f'{"Ticker":<7} {"Year":>5} {"Return":>7} {"Confidence":>11}  {"AI Reason"}')
        print('-' * 66)
        for a in holds:
            print(f'{a["ticker"]:<7} {a["year"]:>5} '
                  f'{(a["sell_price"]/a["buy_price"]-1):>+6.0%}  '
                  f'{a["confidence"]:>10}%  {_san(str(a.get("ai_reason","")))[:38]}')

        print()
        print(f'{"Ticker":<7} {"Year":>5} {"If Sold ($)":>12} {"By Holding ($)":>15} {"AI Saved ($)":>13}')
        print('-' * 56)
        impact_total = 0.0
        for a in holds:
            ticker    = a['ticker']
            sell_year = a['year']
            shares    = a.get('shares', 0)
            sell_px   = a.get('sell_price', 0)
            slip_yr   = _slip(sell_year)

            # What we would have received if sold (after slippage, before tax — simplified)
            val_if_sold = shares * sell_px * (1 - slip_yr)

            # What it's worth at end of backtest
            ph       = all_price_hists.get(ticker, {})
            cur_px   = _last_price_before(ph, '2027-01-01')
            val_held = shares * cur_px if cur_px else val_if_sold

            ai_saved    = val_held - val_if_sold
            impact_total += ai_saved
            print(f'{ticker:<7} {sell_year:>5} ${val_if_sold:>11,.0f} ${val_held:>14,.0f} '
                  f'${ai_saved:>12,.0f}')

        print('-' * 56)
        print(f'{"TOTAL AI IMPACT":<13} {"":>12} {"":>15} ${impact_total:>12,.0f}')

    if ai_log:
        print(f'\n  Total AI cost: ${_ai_cost_used:.4f} / ${AI_COST_CAP:.2f} cap')
        print(f'  AI reviews triggered: {len(ai_log)} '
              f'({len(holds)} HOLD, {len(ai_log)-len(holds)} SELL confirmed)')

    # ── Final holdings still held ──────────────────────────────────────────────── #
    print('\n' + '=' * 66)
    print('FINAL HOLDINGS (STILL HELD as of 2026)')
    print('=' * 66)
    print(f'{"Ticker":<8} {"Buy Yr":>7} {"Buy $":>8} {"Cur $":>8} '
          f'{"Return":>9} {"Value":>10} {"Held":>5}')
    print('-' * 60)

    rows = []
    for ticker, pos in final_portfolio.items():
        ph = all_price_hists.get(ticker, {})
        cp = _last_price_before(ph, '2027-01-01')
        if not cp:
            continue
        ret  = cp / pos['buy_price'] - 1
        val  = pos['shares'] * cp
        held = 2026 - pos['buy_year']
        rows.append((ticker, pos['buy_year'], pos['buy_price'], cp, ret, val, held))

    rows.sort(key=lambda x: x[5], reverse=True)
    for ticker, by, bp, cp, ret, val, held in rows:
        print(f'{ticker:<8} {by:>7} ${bp:>7.2f} ${cp:>7.2f} '
              f'{ret:>+8.0%} ${val:>9,.0f} {held:>4}yr')

    # ── Tax savings vs annual rebalance ───────────────────────────────────────── #
    print('\n' + '=' * 66)
    print('TAX SAVINGS vs ANNUAL REBALANCE')
    print('=' * 66)

    # Estimate: full rebalance each year -> all gains taxed every positive year
    est_rebalance_tax = sum(
        yr['portfolio_value'] * max(yr.get('year_return', 0), 0) * ISRAEL_CGT
        for yr in year_results
    )
    tax_saved  = est_rebalance_tax - total_tax
    strat_mult = final_val / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 1.0
    compounded = tax_saved * (strat_mult ** 0.5) if tax_saved > 0 else 0.0

    avg_held = (sum(s['held_years'] for s in sells_log) / len(sells_log)
                if sells_log else 0.0)

    print(f'  Est. tax if rebalanced annually:     ${est_rebalance_tax:>10,.0f}')
    print(f'  Actual tax paid (thesis-break sells): ${total_tax:>9,.0f}')
    print(f'  Tax saved:                            ${tax_saved:>9,.0f}')
    print(f'  Tax saved compounded at strat CAGR:  ~${compounded:>9,.0f}')
    if avg_held > 0:
        print(f'  Avg holding period for sold positions: {avg_held:.1f} years')
    print(f'  Total thesis-break sells: {len(sells_log)}')
    print(f'  Total AI reviews triggered: {len(ai_log)} '
          f'({len(holds)} HOLD overrides)')


# ── Entry point ───────────────────────────────────────────────────────────────── #

if __name__ == '__main__':
    run_sp500_backtest()
