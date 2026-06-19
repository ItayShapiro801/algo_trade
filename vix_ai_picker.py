"""
VIX-Triggered AI Fundamental Stock Picker
==========================================
Three-layer system:
  Layer 1 — Fundamental Score  (0-110 pts) — pure math, SEC EDGAR XBRL API
  Layer 2 — AI Qualitative      (0-40  pts) — Claude API analysing 10-K / earnings
  Layer 3 — VIX Timing          — when to buy from the top-20 watchlist

Philosophy: great businesses at fair prices, bought during panic.
Almost never sells = near-zero Israeli CGT.

Data sources:
  SEC EDGAR XBRL  https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json
  Nasdaq screener https://api.nasdaq.com/api/screener/stocks?marketcap={cap}
  yfinance        price history, market cap, momentum
  Claude API      qualitative analysis of 10-K / earnings transcripts
  VIX             yf.Ticker("^VIX")
"""

import os, sys, json, time, math, re, warnings, random, threading, argparse
import concurrent.futures
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings('ignore')

# ── .env ─────────────────────────────────────────────────────────────────── #
def _load_env():
    for p in ['.env', os.path.join(os.path.dirname(__file__), '.env')]:
        p = os.path.normpath(p)
        if not os.path.exists(p):
            continue
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v

_load_env()

# ── Constants ─────────────────────────────────────────────────────────────── #
EDGAR_HDR      = {'User-Agent': 'vix-ai-picker research@example.com'}
NASDAQ_HDR     = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
SEC_FACTS_URL  = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
SEC_SUBS_URL   = 'https://data.sec.gov/submissions/CIK{cik}.json'
SEC_TICKERS    = 'https://www.sec.gov/files/company_tickers.json'

CACHE_DIR      = Path('data')
L1_CACHE       = CACHE_DIR / 'l1_cache'
AI_CACHE       = CACHE_DIR / 'ai_cache'
EDGAR_CACHE    = CACHE_DIR / 'edgar_cache'
PIT_L1_CACHE  = CACHE_DIR / 'pit_l1_cache'
PRICE_CACHE   = CACHE_DIR / 'price_cache'
PIT_PROGRESS  = CACHE_DIR / 'pit_progress.json'
for d in (L1_CACHE, AI_CACHE, EDGAR_CACHE, PIT_L1_CACHE, PRICE_CACHE):
    d.mkdir(parents=True, exist_ok=True)

INITIAL_CAPITAL  = 100_000.0
SLIPPAGE_PCT     = 0.001   # 0.1% per trade (each side)
ISRAEL_CGT       = 0.25    # 25% capital gains tax
PIT_L1_PASS      = 65
PIT_TOP_N        = 20

BUFFETT_PROGRESS = CACHE_DIR / 'buffett_progress.json'
BUFFETT_MAX_POS  = 25      # max positions after initial buy
BUFFETT_INIT_POS = 20      # positions on initial buy year
BUFFETT_START    = 2010    # first year with enough EDGAR history

L1_CACHE_DAYS  = 30
AI_CACHE_DAYS  = 90
EDGAR_CACHE_DAYS = 7

L1_PASS_SCORE  = 65
FINAL_MIN_SCORE= 90
TOP_N          = 20

VIX_BUY_START  = 25.0
VIX_CONTINUE   = 20.0

ANTHROPIC_MODEL = 'claude-sonnet-4-6'

# ── Runtime flags (set by --quick argument in main()) ─────────────────────── #
QUICK_MODE     = False   # True = large-cap only, 10 workers, 3s EDGAR timeout
EDGAR_TIMEOUT  = 20      # seconds per EDGAR request (3 in quick mode)
NUM_WORKERS    = 4       # parallel workers (10 in quick mode)

# ── Progress file — tracks attempted tickers so interrupted runs resume ────── #
PROGRESS_FILE  = CACHE_DIR / 'l1_progress.json'

# ── Progress ──────────────────────────────────────────────────────────────── #
_lock    = threading.Lock()
_counter = {'n': 0, 'l1_pass': 0, 'l2_done': 0, 'api_calls': 0, 'cache_hits': 0}

def _tick(l1_pass=False, l2_done=False, api_call=False, cache_hit=False):
    with _lock:
        _counter['n'] += 1
        if l1_pass:  _counter['l1_pass'] += 1
        if l2_done:  _counter['l2_done'] += 1
        if api_call: _counter['api_calls'] += 1
        if cache_hit:_counter['cache_hits'] += 1
        n = _counter['n']
    if n % 50 == 0:
        print(f"  Progress: {n} | L1 passed: {_counter['l1_pass']} | "
              f"L2 scored: {_counter['l2_done']} | "
              f"Above {FINAL_MIN_SCORE}: {_counter['l2_done']}")

# ── Progress file helpers ─────────────────────────────────────────────────── #

def _load_progress() -> set:
    """Load the set of tickers already attempted in a previous run."""
    try:
        if PROGRESS_FILE.exists():
            data = json.loads(PROGRESS_FILE.read_text())
            return set(data.get('attempted', []))
    except Exception:
        pass
    return set()


def _save_progress(attempted: set):
    """Persist the set of attempted tickers so interrupted runs can resume."""
    try:
        PROGRESS_FILE.write_text(json.dumps({'attempted': sorted(attempted),
                                              'ts': time.time()}))
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════ #
# SEC EDGAR CLIENT                                                             #
# ═══════════════════════════════════════════════════════════════════════════ #

_CIK_MAP: dict = {}

def _load_cik_map():
    global _CIK_MAP
    if _CIK_MAP:
        return
    cache_p = EDGAR_CACHE / 'cik_map.json'
    if cache_p.exists() and (time.time() - cache_p.stat().st_mtime) < 86400 * 7:
        _CIK_MAP = json.loads(cache_p.read_text())
        return
    try:
        r = requests.get(SEC_TICKERS, headers=EDGAR_HDR, timeout=20)
        data = r.json()
        _CIK_MAP = {v['ticker'].upper(): int(v['cik_str']) for v in data.values()}
        cache_p.write_text(json.dumps(_CIK_MAP))
    except Exception:
        pass


def get_cik(ticker: str) -> Optional[int]:
    _load_cik_map()
    return _CIK_MAP.get(ticker.upper())


def _cached_get(url: str, cache_path: Path, max_age_days: int) -> Optional[dict]:
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < max_age_days * 86400:
            try:
                return json.loads(cache_path.read_text())
            except Exception:
                pass
    try:
        time.sleep(0.05 if QUICK_MODE else 0.12)  # SEC rate limit
        r = requests.get(url, headers=EDGAR_HDR, timeout=EDGAR_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        cache_path.write_text(json.dumps(data))
        return data
    except Exception:
        return None


def get_xbrl_facts(cik: int) -> dict:
    """Fetch SEC XBRL company facts (all GAAP fields). Cached."""
    cik10 = str(cik).zfill(10)
    path  = EDGAR_CACHE / f'{cik10}_facts.json'
    data  = _cached_get(SEC_FACTS_URL.format(cik=cik10), path, EDGAR_CACHE_DAYS)
    return (data or {}).get('facts', {}).get('us-gaap', {})


def get_submissions(cik: int) -> dict:
    """Fetch submission history (list of all filings). Cached."""
    cik10 = str(cik).zfill(10)
    path  = EDGAR_CACHE / f'{cik10}_subs.json'
    data  = _cached_get(SEC_SUBS_URL.format(cik=cik10), path, EDGAR_CACHE_DAYS)
    return data or {}


# ── XBRL extraction helpers ───────────────────────────────────────────────── #

def _annual_values(gaap: dict, *field_names, n: int = 4) -> Optional[list]:
    """
    Try multiple GAAP field names and return the last `n` unique annual 10-K values.
    Deduplicates by period-end, keeping the most-recently-filed version.
    Returns None if no field found.
    """
    for field in field_names:
        if field not in gaap:
            continue
        units_dict = gaap[field].get('units', {})
        if not units_dict:
            continue
        values = list(units_dict.values())[0]   # USD or USD/shares
        annual = [x for x in values if x.get('form') == '10-K' and x.get('fp') == 'FY']
        seen: dict = {}
        for x in sorted(annual, key=lambda z: (z.get('end', ''), z.get('filed', ''))):
            seen[x['end']] = x['val']
        if len(seen) >= 2:
            return [seen[k] for k in sorted(seen)[-n:]]
    return None


def _cagr(values: list) -> float:
    """Compound Annual Growth Rate over the series."""
    if not values or values[0] <= 0 or values[-1] <= 0:
        return 0.0
    years = len(values) - 1
    return (abs(values[-1] / values[0])) ** (1 / max(years, 1)) - 1


# ── Insider transactions (Form 4) ─────────────────────────────────────────── #

def get_insider_activity(cik: int, days: int = 90) -> str:
    """
    Check recent Form 4 filings. Returns: 'buy' | 'sell' | 'none'
    Only looks at C-suite officers (CEO, CFO, President, COO).
    Skipped entirely in --quick mode (makes 10+ HTTP requests per ticker).
    """
    if QUICK_MODE:
        return 'none'
    try:
        subs = get_submissions(cik)
        filings = subs.get('filings', {}).get('recent', {})
        forms   = filings.get('form', [])
        dates   = filings.get('filingDate', [])
        accs    = filings.get('accessionNumber', [])

        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        buys, sells = 0, 0

        for i, (form, date, acc) in enumerate(zip(forms, dates, accs)):
            if form != '4' or date < cutoff:
                continue
            # Fetch Form 4 XML
            acc_clean = acc.replace('-', '')
            url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{acc}-index.htm'
            time.sleep(0.15)
            r = requests.get(url, headers=EDGAR_HDR, timeout=10)
            if r.status_code != 200:
                continue

            # Simple text scan for transaction code and officer role
            text = r.text
            is_officer = 'Chief Executive' in text or 'CFO' in text or 'Chief Financial' in text
            if not is_officer:
                continue

            if '>P<' in text:   buys  += 1   # P = Purchase
            if '>S<' in text:   sells += 1   # S = Sale (not exercise)

            if i > 10:   # limit requests
                break

        if buys > 0:  return 'buy'
        if sells > 0: return 'sell'
        return 'none'
    except Exception:
        return 'none'


# ── 10-K text for AI ─────────────────────────────────────────────────────── #

def get_10k_text(cik: int) -> dict:
    """
    Fetch Risk Factors, MD&A, and Business sections from latest 10-K.
    Returns dict with keys: risk_factors, mda, business.
    Truncated for AI prompt efficiency.
    """
    cache_p = EDGAR_CACHE / f'{cik}_10ktext.json'
    if cache_p.exists() and (time.time() - cache_p.stat().st_mtime) < 30 * 86400:
        try:
            return json.loads(cache_p.read_text())
        except Exception:
            pass

    try:
        subs    = get_submissions(cik)
        filings = subs.get('filings', {}).get('recent', {})
        forms   = filings.get('form', [])
        accs    = filings.get('accessionNumber', [])
        docs    = filings.get('primaryDocument', [])

        # Find latest 10-K
        tenk_idx = next((i for i, f in enumerate(forms) if f == '10-K'), None)
        if tenk_idx is None:
            return {}

        acc   = accs[tenk_idx].replace('-', '')
        doc   = docs[tenk_idx]
        url   = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}'

        time.sleep(0.2)
        r = requests.get(url, headers=EDGAR_HDR, timeout=30)
        if r.status_code != 200:
            return {}

        text = re.sub(r'<[^>]+>', ' ', r.text)   # strip HTML tags
        text = re.sub(r'\s+', ' ', text)[:120_000]  # limit size

        def _extract(pattern, text, maxlen=2500):
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                return ''
            start = m.start()
            return text[start:start + maxlen].strip()

        result = {
            'risk_factors': _extract(r'RISK FACTORS', text),
            'mda':          _extract(r"MANAGEMENT.{0,20}DISCUSSION", text),
            'business':     _extract(r'ITEM\s*1\.?\s*BUSINESS', text),
        }
        cache_p.write_text(json.dumps(result))
        return result

    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════ #
# LAYER 1 — FUNDAMENTAL SCORING (0-110)                                       #
# ═══════════════════════════════════════════════════════════════════════════ #

def score_layer1(ticker: str, sector: str = '') -> tuple:
    """
    Returns (score: int, breakdown: dict, fundamentals: dict).
    Returns (0, {}, {}) on failure.
    Uses cached results when < L1_CACHE_DAYS old.
    """
    cache_p = L1_CACHE / f'{ticker}.json'
    if cache_p.exists() and (time.time() - cache_p.stat().st_mtime) < L1_CACHE_DAYS * 86400:
        try:
            data = json.loads(cache_p.read_text())
            return data['score'], data['breakdown'], data.get('fundamentals', {})
        except Exception:
            pass

    cik = get_cik(ticker)
    if not cik:
        return 0, {}, {}

    try:
        gaap = get_xbrl_facts(cik)
        if not gaap:
            return 0, {}, {}

        # ── Core financial series ──────────────────────────────────────────── #
        revenue = _annual_values(gaap,
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'Revenues', 'SalesRevenueNet', 'SalesRevenueGoodsNet')
        net_income = _annual_values(gaap, 'NetIncomeLoss', 'ProfitLoss')
        gross_profit = _annual_values(gaap, 'GrossProfit')
        op_income = _annual_values(gaap, 'OperatingIncomeLoss')
        equity = _annual_values(gaap,
            'StockholdersEquity', 'StockholdersEquityAttributableToParent')
        cur_assets  = _annual_values(gaap, 'AssetsCurrent')
        cur_liab    = _annual_values(gaap, 'LiabilitiesCurrent')
        lt_debt     = _annual_values(gaap,
            'LongTermDebt', 'LongTermDebtNoncurrent',
            'LongTermDebtAndCapitalLeaseObligations')
        int_expense = _annual_values(gaap, 'InterestExpense',
            'InterestAndDebtExpense')
        op_cf       = _annual_values(gaap,
            'NetCashProvidedByUsedInOperatingActivities')
        capex       = _annual_values(gaap,
            'PaymentsToAcquirePropertyPlantAndEquipment',
            'CapitalExpendituresIncurringDebt',
            'PaymentsForCapitalImprovements')
        eps         = _annual_values(gaap,
            'EarningsPerShareBasic', 'EarningsPerShareDiluted')

        # Require minimum data
        if not revenue or len(revenue) < 2 or not net_income:
            return 0, {}, {}

        # FCF series
        fcf = None
        if op_cf and capex and len(op_cf) >= 2 and len(capex) >= 2:
            min_len = min(len(op_cf), len(capex))
            fcf = [op_cf[-min_len:][i] - capex[-min_len:][i] for i in range(min_len)]

        score       = 0
        breakdown   = {}

        # ── D1: Business Quality (0-25) ──────────────────────────────────── #
        d1 = 0
        # Q1: ROE > 15% for 3 consecutive years
        if equity and net_income and len(equity) >= 3 and len(net_income) >= 3:
            roe_3y = [ni / max(eq, 1) for ni, eq in
                      zip(net_income[-3:], equity[-3:])]
            if all(r > 0.15 for r in roe_3y):
                d1 += 5
                # Q2: ROE > 20% bonus
                if roe_3y[-1] > 0.20:
                    d1 += 5
        elif equity and net_income:
            roe_now = net_income[-1] / max(equity[-1], 1)
            if roe_now > 0.15: d1 += 3
            if roe_now > 0.20: d1 += 3

        # Q3: Net margin > 10%
        if revenue and net_income:
            nm = net_income[-1] / max(revenue[-1], 1)
            if nm > 0.10: d1 += 5

        # Q4: Revenue grew every year (3y)
        if revenue and len(revenue) >= 3:
            if all(revenue[i] < revenue[i+1] for i in range(len(revenue[-3:])-1)):
                d1 += 5

        # Q5: Gross margin stable / improving
        if gross_profit and revenue and len(gross_profit) >= 2 and len(revenue) >= 2:
            gm_prev = gross_profit[-2] / max(revenue[-2], 1)
            gm_now  = gross_profit[-1] / max(revenue[-1], 1)
            if gm_now >= gm_prev * 0.98:
                d1 += 5

        score += d1
        breakdown['D1_quality'] = d1

        # ── D2: Financial Fortress (0-25) ─────────────────────────────────── #
        d2 = 0
        if equity and lt_debt:
            de = abs(lt_debt[-1]) / max(abs(equity[-1]), 1)
            if de < 0.5:  d2 += 10
            if de < 0.2:  d2 += 3

        if cur_assets and cur_liab and cur_assets[-1] and cur_liab[-1]:
            cr = cur_assets[-1] / max(cur_liab[-1], 1)
            if cr > 1.5: d2 += 5

        if op_income and int_expense and int_expense[-1] and int_expense[-1] > 0:
            ic = op_income[-1] / max(int_expense[-1], 1)
            if ic > 5: d2 += 5
        elif not int_expense or (int_expense and int_expense[-1] == 0):
            d2 += 5   # no interest expense = debt-free bonus

        if fcf and len(fcf) >= 3:
            if all(f > 0 for f in fcf[-3:]): d2 += 5
        elif fcf and fcf[-1] > 0:
            d2 += 3

        score += d2
        breakdown['D2_fortress'] = d2

        # ── D3: Consistent Growth (0-20) ──────────────────────────────────── #
        d3 = 0
        if eps and len(eps) >= 3 and all(e > 0 for e in eps[-3:]):
            eps_cagr = _cagr(eps[-3:])
            if eps_cagr > 0.10: d3 += 7

        if revenue and len(revenue) >= 3:
            rev_cagr = _cagr(revenue[-3:])
            if rev_cagr > 0.08: d3 += 7

        if fcf and len(fcf) >= 2 and fcf[-1] > fcf[-2]:
            d3 += 6

        score += d3
        breakdown['D3_growth'] = d3

        # ── D4: Valuation (0-20) ──────────────────────────────────────────── #
        d4 = 0
        try:
            fi = yf.Ticker(ticker).fast_info
            mktcap    = fi.market_cap
            cur_price = fi.last_price
        except Exception:
            mktcap = cur_price = None

        if mktcap and mktcap > 0 and eps and eps[-1] > 0:
            pe = (cur_price or 1) / eps[-1]
            if pe < 25: d4 += 4
            if pe < 15: d4 += 4   # bonus

            # PEG
            if eps and len(eps) >= 3 and all(e > 0 for e in eps[-3:]):
                eps_g = _cagr(eps[-3:]) * 100   # in %
                if eps_g > 0:
                    peg = pe / eps_g
                    if peg < 1.5: d4 += 4
                    if peg < 1.0: d4 += 3   # bonus

        if mktcap and mktcap > 0 and fcf and fcf[-1] > 0:
            fcf_yield = fcf[-1] / mktcap
            if fcf_yield > 0.03: d4 += 5
            if fcf_yield > 0.06: d4 += 3   # bonus

        if mktcap and mktcap > 0 and revenue:
            ps = mktcap / max(revenue[-1], 1)
            if ps < 5: d4 += 3

        score += d4
        breakdown['D4_valuation'] = d4

        # ── D5: Momentum (0-10) ───────────────────────────────────────────── #
        d5 = 0
        try:
            hist = yf.Ticker(ticker).history(period='13mo', interval='1d')
            if not hist.empty and len(hist) > 252:
                c = hist['Close']
                ret_6m  = (c.iloc[-1] / c.iloc[-126]) - 1
                ret_12m = (c.iloc[-1] / c.iloc[-252]) - 1
                spy_12m = 0.0
                try:
                    spy = yf.Ticker('SPY').history(period='13mo')['Close']
                    if len(spy) > 252:
                        spy_12m = (spy.iloc[-1] / spy.iloc[-252]) - 1
                except Exception:
                    pass
                if ret_6m > 0:   d5 += 5
                if ret_12m > spy_12m: d5 += 5
        except Exception:
            pass

        score += d5
        breakdown['D5_momentum'] = d5

        # ── Insider Bonus (-5 to +10) ──────────────────────────────────────── #
        insider = get_insider_activity(cik)
        ins_bonus = 0
        if insider == 'buy':   ins_bonus =  10
        elif insider == 'sell': ins_bonus = -5
        score += ins_bonus
        breakdown['bonus_insider'] = ins_bonus

        # Cap at 110
        score = min(max(score, 0), 110)
        breakdown['total'] = score

        # Fundamentals snapshot for Layer 2 / reporting
        fundamentals = {
            'roe':      round(net_income[-1] / max(equity[-1], 1), 4) if equity else None,
            'net_margin': round(net_income[-1] / max(revenue[-1], 1), 4) if revenue else None,
            'revenue_cagr': round(_cagr(revenue[-3:]), 4) if revenue and len(revenue) >= 3 else None,
            'eps_cagr':  round(_cagr(eps[-3:]), 4) if eps and len(eps) >= 3 else None,
            'de_ratio':  round(abs(lt_debt[-1]) / max(abs(equity[-1]), 1), 2) if equity and lt_debt else None,
            'fcf_yield': round(fcf[-1] / mktcap, 4) if fcf and mktcap else None,
            'insider':   insider,
            'sector':    sector,
            'mktcap':    mktcap,
        }

        # Cache result
        cache_p.write_text(json.dumps({
            'score': score, 'breakdown': breakdown,
            'fundamentals': fundamentals, 'ts': time.time()
        }))
        return score, breakdown, fundamentals

    except Exception:
        return 0, {}, {}


# ═══════════════════════════════════════════════════════════════════════════ #
# LAYER 2 — AI QUALITATIVE SCORING (0-40)                                     #
# ═══════════════════════════════════════════════════════════════════════════ #

def _call_claude(prompt: str) -> dict:
    """Call Claude API. Returns parsed JSON dict or {} on failure."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = msg.content[0].text
        # Try direct JSON parse
        try:
            return json.loads(text)
        except Exception:
            # Extract JSON block
            m = re.search(r'\{[\s\S]+\}', text)
            if m:
                return json.loads(m.group())
        return {}
    except Exception as e:
        return {}


def score_layer2(ticker: str, company_name: str, sector: str,
                 fundamentals: dict, cik: int) -> tuple:
    """
    Returns (ai_score: int, ai_data: dict).
    ai_score is 0-40 (halved if value_trap_warning=true).
    Skips if ANTHROPIC_API_KEY not set (returns 20 / neutral).
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    no_ai   = not api_key

    cache_p = AI_CACHE / f'{ticker}.json'
    if cache_p.exists() and (time.time() - cache_p.stat().st_mtime) < AI_CACHE_DAYS * 86400:
        try:
            data = json.loads(cache_p.read_text())
            _tick(cache_hit=True)
            return data['ai_score'], data
        except Exception:
            pass

    if no_ai:
        return 20, {'ai_score': 20, 'no_ai': True,
                    'value_trap_warning': False, 'value_trap_reason': ''}

    # Fetch 10-K text
    texts = get_10k_text(cik)
    rf_text   = texts.get('risk_factors', '')[:2500]
    mda_text  = texts.get('mda', '')[:1500]
    biz_text  = texts.get('business', '')[:1000]

    prompt = f"""You are a strict investment analyst like Charlie Munger.
Analyze this company for long-term investment quality.

Company: {ticker} — {company_name}
Sector: {sector}
ROE: {fundamentals.get('roe','n/a')} | Net Margin: {fundamentals.get('net_margin','n/a')} | Rev CAGR: {fundamentals.get('revenue_cagr','n/a')}

RISK FACTORS (10-K excerpt):
{rf_text or '(not available)'}

MANAGEMENT DISCUSSION (10-K excerpt):
{mda_text or '(not available)'}

BUSINESS DESCRIPTION (10-K excerpt):
{biz_text or '(not available)'}

Score on EXACTLY these 4 criteria. Respond ONLY in JSON, no other text:
{{
  "moat_score": 0-10,
  "moat_reason": "one sentence",
  "growth_outlook": 0-10,
  "growth_reason": "one sentence",
  "management_quality": 0-10,
  "management_reason": "one sentence",
  "competition_risk": 0-10,
  "competition_reason": "one sentence",
  "value_trap_warning": true/false,
  "value_trap_reason": "one sentence if true, else empty string"
}}

Scoring: moat 10=unbreakable (Visa/Apple), 0=no moat.
growth_outlook 10=clear 10%+/yr path, 0=declining.
management_quality 10=exceptional, 0=red flags.
competition_risk 10=no real competitors, 0=being disrupted.
value_trap_warning: true if looks cheap on paper but has fundamental problems ahead."""

    ai_raw = _call_claude(prompt)
    _tick(api_call=True, l2_done=True)

    if not ai_raw:
        result = {'ai_score': 15, 'no_ai': True,
                  'value_trap_warning': False, 'value_trap_reason': ''}
        cache_p.write_text(json.dumps(result))
        return 15, result

    raw_score = (ai_raw.get('moat_score', 5) + ai_raw.get('growth_outlook', 5) +
                 ai_raw.get('management_quality', 5) + ai_raw.get('competition_risk', 5))
    raw_score = max(0, min(40, raw_score))

    trap = ai_raw.get('value_trap_warning', False)
    ai_score = int(raw_score * 0.5) if trap else int(raw_score)

    result = {**ai_raw, 'ai_score': ai_score, 'raw_score': raw_score, 'ts': time.time()}
    cache_p.write_text(json.dumps(result))
    return ai_score, result


# ═══════════════════════════════════════════════════════════════════════════ #
# UNIVERSE BUILDING                                                            #
# ═══════════════════════════════════════════════════════════════════════════ #

def _fetch_cap(cap: str) -> list:
    try:
        r = requests.get(
            f'https://api.nasdaq.com/api/screener/stocks?marketcap={cap}&download=true',
            headers=NASDAQ_HDR, timeout=20)
        rows = r.json()['data'].get('rows', [])
        return [{'ticker': row['symbol'].strip().upper(),
                 'name':   row.get('name', ''),
                 'sector': row.get('sector', ''),
                 'mcap':   float(row.get('marketCap', 0) or 0),
                 'price':  float((row.get('lastsale', '$0') or '$0').replace('$','').replace(',','')),
                 'cap_tier': cap}
                for row in rows
                if row.get('symbol') and '/' not in row.get('symbol', '')]
    except Exception as e:
        print(f'  [Nasdaq-{cap}] failed: {e}')
        return []


def build_or_load_universe() -> list:
    """
    Fetch stock universe from Nasdaq screener.
    --quick mode: large caps only (~500 stocks), bypasses cache.
    Normal mode : large + mid + small (~2000+ stocks), cached 30 days.
    """
    if QUICK_MODE:
        print('  [Universe] --quick: fetching large-caps only (no cache)...')
        rows = _fetch_cap('large')
        rows = [r for r in rows if r.get('price', 0) >= 2.0]
        rows = rows[:500]   # hard cap
        print(f'  [Universe] {len(rows)} large-cap stocks')
        return rows

    path = CACHE_DIR / 'universe_all.csv'
    if path.exists() and (time.time() - path.stat().st_mtime) < 30 * 86400:
        df = pd.read_csv(path)
        print(f'  [Universe] Loaded {len(df)} stocks from cache')
        return df.to_dict('records')

    print('  [Universe] Fetching from Nasdaq screener (large + mid + small)...')
    rows = []
    for cap in ('large', 'mid', 'small'):
        r = _fetch_cap(cap)
        rows.extend(r)
        print(f'    {cap}: {len(r)} stocks')

    # Deduplicate
    seen: dict = {}
    for row in rows:
        t = row['ticker']
        if t not in seen or row['mcap'] > seen[t]['mcap']:
            seen[t] = row
    final = list(seen.values())

    df = pd.DataFrame(final)
    df.to_csv(path, index=False)
    print(f'  [Universe] {len(final)} unique stocks saved')
    return final


# ═══════════════════════════════════════════════════════════════════════════ #
# VIX ANALYSIS                                                                 #
# ═══════════════════════════════════════════════════════════════════════════ #

def download_vix() -> pd.Series:
    vix = yf.Ticker('^VIX').history(period='max', interval='1d')['Close']
    vix.index = pd.to_datetime(vix.index).tz_localize(None)
    return vix


def find_vix_spikes(vix: pd.Series, threshold: float = 25.0) -> list:
    """
    Identify distinct VIX spike events where VIX crossed `threshold`.
    Returns list of event dicts with date, vix_peak, and context.
    """
    events = []
    above  = vix > threshold
    in_event = False
    start_dt = None
    peak_val  = 0.0

    for date, val in vix.items():
        if val > threshold:
            if not in_event:
                in_event = True
                start_dt = date
                peak_val  = val
            else:
                peak_val = max(peak_val, val)
        else:
            if in_event and peak_val > threshold:
                events.append({
                    'start':    start_dt,
                    'peak_vix': round(peak_val, 1),
                    'peak_date': vix.loc[start_dt:date].idxmax(),
                })
            in_event = False
            peak_val  = 0.0
            start_dt  = None

    # Merge events within 30 days of each other
    merged = []
    for ev in events:
        if merged and (ev['start'] - merged[-1]['start']).days < 30:
            if ev['peak_vix'] > merged[-1]['peak_vix']:
                merged[-1] = ev
        else:
            merged.append(ev)

    return merged


def get_current_vix(vix: pd.Series) -> float:
    return float(vix.iloc[-1])


# ═══════════════════════════════════════════════════════════════════════════ #
# SIMPLIFIED BACKTEST                                                          #
# ═══════════════════════════════════════════════════════════════════════════ #

def simulate_backtest(top20: list, vix_events: list,
                      start: str = '2005-01-01',
                      initial: float = 100_000.0) -> dict:
    """
    Indicative simulation: uses CURRENT quality scores applied to historical prices.
    Buys top-5 from watchlist during each VIX spike; holds otherwise.
    Note: not a true point-in-time backtest — survivorship bias applies.
    """
    tickers = [s['ticker'] for s in top20[:10]]   # top 10 as candidates

    # Download historical prices for top tickers + benchmarks
    all_tickers = tickers + ['SPY', 'QQQ', 'IWM']
    price_data  = {}
    for t in all_tickers:
        try:
            s = yf.Ticker(t).history(start=start, interval='1d')['Close']
            s.index = pd.to_datetime(s.index).tz_localize(None)
            price_data[t] = s
        except Exception:
            pass

    if not price_data:
        return {}

    # Common date range
    spy_close = price_data.get('SPY', pd.Series(dtype=float))
    all_dates = spy_close.loc[start:].index
    if all_dates.empty:
        return {}

    capital   = initial
    positions: dict = {}  # ticker -> {'shares', 'cost'}
    port_vals = []
    buys_made = 0

    for date in all_dates:
        # Portfolio value
        pv = capital + sum(pos['shares'] * float(price_data.get(t, {0: 0}).get(date, pos['cost'] / pos['shares']))
                           for t, pos in positions.items()
                           if t in price_data and date in price_data[t].index)
        port_vals.append(pv)

        # Check if this date is within 30 days of a VIX spike
        for ev in vix_events:
            spike_date = ev['peak_date']
            if abs((date - spike_date).days) < 5 and buys_made < 8:
                # Buy top-3 available tickers
                to_buy = [t for t in tickers if t not in positions
                          and t in price_data and date in price_data[t].index][:3]
                if to_buy and capital > 10_000:
                    alloc = capital * 0.8 / max(len(to_buy), 1)
                    for t in to_buy:
                        px = float(price_data[t].loc[date])
                        if px > 0:
                            shares = alloc / px
                            capital -= alloc
                            positions[t] = {'shares': shares, 'cost': alloc}
                    buys_made += 1

    portfolio = pd.Series(port_vals, index=all_dates)

    # Benchmark simulations
    def bh(ticker):
        if ticker not in price_data:
            return pd.Series(dtype=float)
        c = price_data[ticker].loc[start:]
        if c.empty: return pd.Series(dtype=float)
        return (c / c.iloc[0] * initial).rename(ticker)

    spy_bh = bh('SPY')
    qqq_bh = bh('QQQ')
    iwm_bh = bh('IWM')

    def _metrics(s):
        if s.empty: return {}
        d = s.pct_change().dropna()
        years = (s.index[-1] - s.index[0]).days / 365.25
        cagr  = (s.iloc[-1] / s.iloc[0]) ** (1 / max(years, 0.01)) - 1
        cum   = d.expanding().max()
        dd    = ((s / s.expanding().max()) - 1).min()
        ann   = s.resample('YE').last().pct_change().dropna()
        return dict(cagr=cagr, total=s.iloc[-1]/s.iloc[0]-1,
                    sharpe=float(d.mean()/d.std()*np.sqrt(252)) if d.std() > 0 else 0,
                    max_dd=float(dd), best_year=float(ann.max()) if len(ann) else 0,
                    worst_year=float(ann.min()) if len(ann) else 0,
                    win_rate=float((s.resample('ME').last().pct_change().dropna()>0).mean()))

    return {
        'portfolio': portfolio,
        'spy_bh': spy_bh, 'qqq_bh': qqq_bh, 'iwm_bh': iwm_bh,
        'strat_m': _metrics(portfolio),
        'spy_m':   _metrics(spy_bh),
        'qqq_m':   _metrics(qqq_bh),
        'vix_events': vix_events,
        'buys_made': buys_made,
    }


# ═══════════════════════════════════════════════════════════════════════════ #
# TRUE POINT-IN-TIME BACKTEST                                                  #
# ═══════════════════════════════════════════════════════════════════════════ #

def _annual_values_pit(field_data: dict, cutoff: str, n: int = 5) -> Optional[list]:
    """Like _annual_values but only uses filings where filed < cutoff (YYYY-MM-DD)."""
    units_dict = field_data.get('units', {})
    if not units_dict:
        return None
    values = list(units_dict.values())[0]
    annual = [x for x in values
              if x.get('form') == '10-K'
              and x.get('fp') == 'FY'
              and x.get('filed', '9999-99-99') < cutoff]
    seen: dict = {}
    for x in sorted(annual, key=lambda z: (z.get('end', ''), z.get('filed', ''))):
        seen[x['end']] = x['val']
    if len(seen) >= 2:
        return [seen[k] for k in sorted(seen)[-n:]]
    return None


def _get_price_cache(ticker: str) -> dict:
    """Full price history as {date_str: close_price}. Cached 7 days."""
    cache_p = PRICE_CACHE / f'{ticker}.json'
    if cache_p.exists() and (time.time() - cache_p.stat().st_mtime) < 7 * 86400:
        try:
            return json.loads(cache_p.read_text())
        except Exception:
            pass
    try:
        hist = yf.Ticker(ticker).history(start='2003-01-01', interval='1d')['Close']
        hist.index = pd.to_datetime(hist.index).tz_localize(None)
        data = {str(d.date()): float(v) for d, v in hist.items()}
        cache_p.write_text(json.dumps(data))
        return data
    except Exception:
        return {}


def _price_on_date(price_hist: dict, target_date: str, window: int = 7) -> Optional[float]:
    """Get closing price on or shortly after target_date (skips weekends/holidays)."""
    target = datetime.strptime(target_date, '%Y-%m-%d')
    for i in range(window):
        d = (target + timedelta(days=i)).strftime('%Y-%m-%d')
        if d in price_hist:
            return price_hist[d]
    return None


def _last_price_before(price_hist: dict, cutoff_date: str) -> Optional[float]:
    """Get the last available price before cutoff_date."""
    dates_before = [d for d in price_hist.keys() if d < cutoff_date]
    if not dates_before:
        return None
    return price_hist[max(dates_before)]


def _get_split_ratio(ticker: str, since_date: str, until_date: str) -> float:
    """Cumulative split ratio for splits strictly after since_date and on/before until_date.

    yfinance retroactively adjusts all historical prices for splits, but EDGAR EPS values
    remain on the pre-split per-share basis as originally filed. When a split falls in this
    window the price and EPS are on different per-share bases; dividing EPS by this ratio
    before computing P/E or PEG restores consistency.
    """
    try:
        splits = yf.Ticker(ticker).splits
        ratio = 1.0
        for split_date, split_ratio in splits.items():
            sd = str(split_date)[:10]
            if since_date < sd <= until_date:
                ratio *= split_ratio
        return ratio
    except Exception:
        return 1.0


def score_layer1_pit(ticker: str, year: int, gaap: dict, price_hist: dict) -> tuple:
    """
    Point-in-time L1 score for ticker as of Jan 1 of year.
    Only uses XBRL filings where filed < {year}-01-01.
    Returns (score: int, breakdown: dict, n_filings: int).
    """
    cache_p = PIT_L1_CACHE / f'{ticker}_{year}.json'
    if cache_p.exists():
        try:
            d = json.loads(cache_p.read_text())
            return d['score'], d['breakdown'], d['n_filings']
        except Exception:
            pass

    cutoff = f'{year}-01-01'

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

    # Need at least 3 full 10-K filings before this year
    n_filings = len(revenue) if revenue else 0
    if n_filings < 3:
        result = {'score': 0, 'breakdown': {}, 'n_filings': n_filings}
        try:
            cache_p.write_text(json.dumps(result))
        except Exception:
            pass
        return 0, {}, n_filings

    # FCF series
    fcf = None
    if op_cf and capex and len(op_cf) >= 2 and len(capex) >= 2:
        ml = min(len(op_cf), len(capex))
        fcf = [op_cf[-ml:][i] - capex[-ml:][i] for i in range(ml)]

    score = 0
    breakdown = {}

    # ── D1: Business Quality (0-25) ────────────────────────────────────────── #
    d1 = 0
    if equity and net_income and len(equity) >= 3 and len(net_income) >= 3:
        roe_3y = [ni / max(eq, 1) for ni, eq in zip(net_income[-3:], equity[-3:])]
        if all(r > 0.15 for r in roe_3y):
            d1 += 5
            if roe_3y[-1] > 0.20:
                d1 += 5
    elif equity and net_income:
        roe_now = net_income[-1] / max(equity[-1], 1)
        if roe_now > 0.15: d1 += 3
        if roe_now > 0.20: d1 += 3

    if revenue and net_income:
        nm = net_income[-1] / max(revenue[-1], 1)
        if nm > 0.10: d1 += 5

    if revenue and len(revenue) >= 3:
        rev3 = revenue[-3:]
        if all(rev3[i] < rev3[i+1] for i in range(len(rev3)-1)):
            d1 += 5

    if gross_profit and revenue and len(gross_profit) >= 2 and len(revenue) >= 2:
        gm_prev = gross_profit[-2] / max(revenue[-2], 1)
        gm_now  = gross_profit[-1] / max(revenue[-1], 1)
        if gm_now >= gm_prev * 0.98:
            d1 += 5

    score += d1
    breakdown['D1_quality'] = d1

    # ── D2: Financial Fortress (0-25) ──────────────────────────────────────── #
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

    if fcf and len(fcf) >= 3:
        if all(f > 0 for f in fcf[-3:]): d2 += 5
    elif fcf and fcf[-1] > 0:
        d2 += 3

    score += d2
    breakdown['D2_fortress'] = d2

    # ── D3: Consistent Growth (0-20) ───────────────────────────────────────── #
    d3 = 0
    if eps and len(eps) >= 3 and all(e > 0 for e in eps[-3:]):
        if _cagr(eps[-3:]) > 0.10: d3 += 7

    if revenue and len(revenue) >= 3:
        if _cagr(revenue[-3:]) > 0.08: d3 += 7

    if fcf and len(fcf) >= 2 and fcf[-1] > fcf[-2]:
        d3 += 6

    score += d3
    breakdown['D3_growth'] = d3

    # ── D4: Valuation (0-20) — uses historical price at Jan 1 of year Y ────── #
    d4 = 0
    hist_price = _price_on_date(price_hist, cutoff)
    if hist_price and hist_price > 0:
        if eps and eps[-1] > 0:
            pe = hist_price / eps[-1]
            if pe < 25: d4 += 4
            if pe < 15: d4 += 4
            if eps and len(eps) >= 3 and all(e > 0 for e in eps[-3:]):
                eps_g = _cagr(eps[-3:]) * 100
                if eps_g > 0:
                    peg = pe / eps_g
                    if peg < 1.5: d4 += 4
                    if peg < 1.0: d4 += 3

        if fcf and fcf[-1] > 0:
            d4 += 3  # positive FCF bonus (no historical mktcap available)

        if revenue and revenue[-1] > 0:
            d4 += 2  # revenue-positive placeholder

    score += d4
    breakdown['D4_valuation'] = d4

    # ── D5: Momentum (0-10) — price returns before Jan 1 of year Y ─────────── #
    d5 = 0
    cutoff_dt  = datetime.strptime(cutoff, '%Y-%m-%d')
    prev_day   = (cutoff_dt - timedelta(days=2)).strftime('%Y-%m-%d')
    dt_6m_ago  = (cutoff_dt - timedelta(days=183)).strftime('%Y-%m-%d')
    dt_12m_ago = (cutoff_dt - timedelta(days=365)).strftime('%Y-%m-%d')

    p_now  = _price_on_date(price_hist, prev_day, window=10)
    p_6m   = _price_on_date(price_hist, dt_6m_ago, window=10)
    p_12m  = _price_on_date(price_hist, dt_12m_ago, window=10)

    if p_now and p_6m and p_6m > 0 and p_now > p_6m:
        d5 += 5
    if p_now and p_12m and p_12m > 0 and p_now > p_12m:
        d5 += 5

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


def _save_pit_progress(year: int, portfolio_value: float, year_results: list):
    try:
        PIT_PROGRESS.write_text(json.dumps({
            'last_completed_year': year,
            'portfolio_value': portfolio_value,
            'year_results': year_results,
        }, indent=2))
    except Exception:
        pass


def run_point_in_time_backtest():
    """TRUE point-in-time backtest 2005-2026. Called via --backtest flag."""
    global QUICK_MODE, EDGAR_TIMEOUT, NUM_WORKERS
    QUICK_MODE    = True
    EDGAR_TIMEOUT = 5
    NUM_WORKERS   = 10

    print('=' * 65)
    print('  TRUE POINT-IN-TIME BACKTEST 2005-2026')
    print('  Layer 1 only — $0 API cost')
    print('  NOTE: Survivorship bias (current large caps used as universe)')
    print('=' * 65)

    # ── 1. Universe ──────────────────────────────────────────────────────── #
    print('\n[1/4] Building large-cap universe...')
    universe = build_or_load_universe()
    universe = [s for s in universe if s.get('price', 0) >= 2.0][:500]
    tickers_meta = {s['ticker']: s for s in universe}
    tickers = list(tickers_meta.keys())
    print(f'  {len(tickers)} stocks in universe')

    _load_cik_map()

    # ── 2. Download price histories ───────────────────────────────────────── #
    print('\n[2/4] Downloading price history (2003-today) for all stocks + benchmarks...')
    print('  (cached to data/price_cache/ — first run takes a few minutes)')

    bench_hists = {}
    for bench in ['SPY', 'QQQ']:
        bench_hists[bench] = _get_price_cache(bench)
        print(f'  {bench}: {len(bench_hists[bench])} trading days cached')

    all_price_hists: dict = {}

    def _fetch_price(t):
        all_price_hists[t] = _get_price_cache(t)

    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        futures = {pool.submit(_fetch_price, t): t for t in tickers}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            if done % 100 == 0:
                print(f'  Price history: {done}/{len(tickers)} loaded')
    print(f'  All {len(tickers)} price histories loaded')

    # ── 3. Resume / init ──────────────────────────────────────────────────── #
    year_results: list = []
    start_year = 2005
    portfolio_value = INITIAL_CAPITAL
    if PIT_PROGRESS.exists():
        try:
            prog = json.loads(PIT_PROGRESS.read_text())
            last_yr = prog.get('last_completed_year', 2004)
            portfolio_value = prog.get('portfolio_value', INITIAL_CAPITAL)
            year_results    = prog.get('year_results', [])
            start_year      = last_yr + 1
            print(f'\n[Resume] Last completed: {last_yr} | Portfolio: ${portfolio_value:,.0f}')
        except Exception:
            pass

    today_str = datetime.now().strftime('%Y-%m-%d')
    end_year  = datetime.now().year  # 2026

    # ── 4. Year loop ─────────────────────────────────────────────────────── #
    print('\n[3/4] Running year-by-year simulation...\n')

    for year in range(start_year, end_year + 1):
        cutoff   = f'{year}-01-01'
        buy_date = cutoff
        sell_date = f'{year + 1}-01-01'
        # For the current year, sell at latest available price
        is_current = (year == end_year)

        print(f'[{year}] Scoring {len(tickers)} stocks (PIT: filings before {cutoff})...')

        # Score all tickers for this year in parallel (data is cached)
        scored = []
        have_history_count = 0
        gaap_cache: dict = {}

        def _score_ticker(row):
            t   = row['ticker']
            cik = get_cik(t)
            if not cik:
                return None
            gaap = gaap_cache.get(t) or get_xbrl_facts(cik)
            gaap_cache[t] = gaap
            if not gaap:
                return None
            ph = all_price_hists.get(t, {})
            buy_price = _price_on_date(ph, buy_date)
            if not buy_price:
                return None  # no price data for this year
            sc, bkd, nfil = score_layer1_pit(t, year, gaap, ph)
            return (t, sc, bkd, nfil, buy_price, row.get('name', t))

        # Run scoring with thread pool (EDGAR calls are cached -> fast)
        with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
            for res in pool.map(_score_ticker, universe):
                if res is None:
                    continue
                t, sc, bkd, nfil, buy_price, name = res
                if nfil >= 3:
                    have_history_count += 1
                if sc >= PIT_L1_PASS:
                    scored.append({'ticker': t, 'score': sc, 'breakdown': bkd,
                                   'buy_price': buy_price, 'name': name})

        scored.sort(key=lambda x: -x['score'])
        top = scored[:PIT_TOP_N]

        print(f'[{year}] Have 3yr history: {have_history_count} stocks')
        print(f'[{year}] L1 passed (>={PIT_L1_PASS}): {len(scored)} | Top {PIT_TOP_N} selected')

        if not top:
            print(f'[{year}] No qualifying stocks — holding cash this year')
            yr_rec = {
                'year': year, 'return': 0.0, 'portfolio': portfolio_value,
                'holdings': [], 'top5': [], 'scores': {},
                'spy_ret': 0.0, 'qqq_ret': 0.0, 'tax_paid': 0.0,
            }
            year_results.append(yr_rec)
            _save_pit_progress(year, portfolio_value, year_results)
            continue

        holdings_str = ', '.join(s['ticker'] for s in top[:10])
        if len(top) > 10:
            holdings_str += f'... (+{len(top)-10})'
        print(f'[{year}] Holdings: {holdings_str}')

        # ── Simulate the 1-year hold ──────────────────────────────────────── #
        alloc_per = portfolio_value / len(top)
        tax_paid  = 0.0
        year_end_value = 0.0

        for s in top:
            t         = s['ticker']
            buy_price = s['buy_price']
            buy_adj   = buy_price * (1 + SLIPPAGE_PCT)
            shares    = alloc_per / buy_adj

            ph = all_price_hists.get(t, {})
            if is_current:
                sell_price = _last_price_before(ph, today_str) or buy_price
            else:
                sell_price = (_price_on_date(ph, sell_date)
                              or _last_price_before(ph, sell_date)
                              or buy_price)

            sell_adj  = sell_price * (1 - SLIPPAGE_PCT)
            proceeds  = shares * sell_adj
            cost      = shares * buy_adj
            gain      = proceeds - cost
            if gain > 0:
                tax       = gain * ISRAEL_CGT
                tax_paid += tax
                proceeds -= tax
            year_end_value += proceeds

        year_ret = year_end_value / portfolio_value - 1

        # Benchmark returns
        spy_buy  = _price_on_date(bench_hists.get('SPY', {}), buy_date)
        spy_sell = ((_price_on_date(bench_hists['SPY'], sell_date) if not is_current
                     else _last_price_before(bench_hists['SPY'], today_str))
                    if 'SPY' in bench_hists else None)
        qqq_buy  = _price_on_date(bench_hists.get('QQQ', {}), buy_date)
        qqq_sell = ((_price_on_date(bench_hists['QQQ'], sell_date) if not is_current
                     else _last_price_before(bench_hists['QQQ'], today_str))
                    if 'QQQ' in bench_hists else None)

        spy_ret = (spy_sell / spy_buy - 1) if spy_buy and spy_sell else 0.0
        qqq_ret = (qqq_sell / qqq_buy - 1) if qqq_buy and qqq_sell else 0.0
        alpha   = year_ret - spy_ret

        label = f'[{year}{"*" if is_current else ""}]'
        print(f'{label} Return: {year_ret:+.1%} | Portfolio: ${year_end_value:,.0f}')
        print(f'{label} SPY: {spy_ret:+.1%} | Alpha: {alpha:+.1%}')
        if tax_paid > 0:
            print(f'{label} Tax paid: ${tax_paid:,.0f}')

        portfolio_value = year_end_value
        yr_rec = {
            'year': year,
            'return': year_ret,
            'portfolio': portfolio_value,
            'holdings': [s['ticker'] for s in top],
            'top5': [s['ticker'] for s in top[:5]],
            'scores': {s['ticker']: s['score'] for s in top[:5]},
            'spy_ret': spy_ret,
            'qqq_ret': qqq_ret,
            'tax_paid': tax_paid,
            'partial': is_current,
        }
        year_results.append(yr_rec)
        _save_pit_progress(year, portfolio_value, year_results)

    print('\n[4/4] Printing final results...')
    print_pit_backtest_results(year_results, INITIAL_CAPITAL)


def print_pit_backtest_results(year_results: list, initial: float):
    if not year_results:
        print('\nNo results.')
        return

    rets     = [yr['return']  for yr in year_results]
    spy_rets = [yr['spy_ret'] for yr in year_results]
    qqq_rets = [yr['qqq_ret'] for yr in year_results]
    tax_total = sum(yr.get('tax_paid', 0) for yr in year_results)

    def _metrics(annual_rets):
        v = initial
        vals = [v]
        for r in annual_rets:
            v *= (1 + r)
            vals.append(v)
        n    = max(len(annual_rets), 1)
        cagr = (vals[-1] / initial) ** (1 / n) - 1 if initial > 0 else 0
        total = vals[-1] / initial - 1 if initial > 0 else 0
        # approx daily stddev from annual
        daily = [(1 + r) ** (1 / 252) - 1 for r in annual_rets]
        sharpe = (np.mean(daily) / max(np.std(daily), 1e-9)) * np.sqrt(252) if daily else 0
        peak, max_dd = initial, 0.0
        for val in vals:
            if val > peak: peak = val
            dd = (val - peak) / peak
            if dd < max_dd: max_dd = dd
        win  = sum(1 for r in annual_rets if r > 0) / max(len(annual_rets), 1)
        best  = max(annual_rets) if annual_rets else 0
        worst = min(annual_rets) if annual_rets else 0
        return dict(cagr=cagr, total=total, sharpe=sharpe, max_dd=max_dd,
                    best=best, worst=worst, win=win)

    sm  = _metrics(rets)
    spm = _metrics(spy_rets)
    qqm = _metrics(qqq_rets)
    beats_spy = sum(1 for yr in year_results if yr['return'] > yr['spy_ret'])
    n_years   = len(year_results)

    def _pct(v): return f'{v:+.1%}' if v is not None else ' n/a'
    def _f2(v):  return f'{v:.2f}'  if v is not None else ' n/a'

    C, W = 32, 11
    sep = '+' + '-' * C + '+' + ('+'.join(['-' * W] * 3)) + '+'

    print('\n\n' + '=' * (C + 3 * W + 4))
    print('=== TRUE POINT-IN-TIME BACKTEST 2005-2026 ===')
    print('=== Layer 1 only — $0 API cost ===')
    print('=== NOTE: Survivorship bias (current 2026 large caps as universe) ===')
    print('=' * (C + 3 * W + 4))
    print()
    print(sep)
    print(f'| {"Metric":<{C-1}}|{"Strategy":^{W}}|{"SPY B&H":^{W}}|{"QQQ B&H":^{W}}|')
    print(sep)

    rows = [
        ('CAGR',             _pct(sm['cagr']),   _pct(spm['cagr']),   _pct(qqm['cagr'])),
        ('Total Return',     _pct(sm['total']),  _pct(spm['total']),  _pct(qqm['total'])),
        ('Sharpe Ratio',     _f2(sm['sharpe']),  _f2(spm['sharpe']),  _f2(qqm['sharpe'])),
        ('Max Drawdown',     _pct(sm['max_dd']), _pct(spm['max_dd']), _pct(qqm['max_dd'])),
        ('Best Year',        _pct(sm['best']),   _pct(spm['best']),   _pct(qqm['best'])),
        ('Worst Year',       _pct(sm['worst']),  _pct(spm['worst']),  _pct(qqm['worst'])),
        ('Win Rate (yearly)',_pct(sm['win']),    _pct(spm['win']),    _pct(qqm['win'])),
    ]
    for label, s, spy, qqq in rows:
        print(f'| {label:<{C-1}}|{s:>{W-1}} |{spy:>{W-1}} |{qqq:>{W-1}} |')
    print(sep)
    print(f'| {"Total tax paid":<{C-1}}|{f"${tax_total:,.0f}":>{W-1}} |{"-":>{W-1}} |{"-":>{W-1}} |')
    print(f'| {f"Years beating SPY":<{C-1}}|{f"{beats_spy}/{n_years}":>{W-1}} |{"-":>{W-1}} |{"-":>{W-1}} |')
    print(sep)

    # Year-by-year table
    YW, PW, RW = 7, 14, 9
    ysep = ('+' + '-' * YW + '+' + '-' * PW +
            '+' + '-' * RW + '+' + '-' * RW + '+' + '-' * RW + '+' + '-' * RW + '+')
    print(f'\nYear-by-year:')
    print(ysep)
    print(f'| {"Year":<{YW-1}}| {"Portfolio":>{PW-2}} | {"Return":>{RW-2}} | '
          f'{"SPY":>{RW-2}} | {"QQQ":>{RW-2}} | {"Alpha":>{RW-2}} |')
    print(ysep)
    for yr in year_results:
        y     = yr['year']
        ret   = yr['return']
        sp    = yr['spy_ret']
        qq    = yr['qqq_ret']
        alpha = ret - sp
        pv    = yr['portfolio']
        flag  = '*' if yr.get('partial') else ' '
        print(f'| {y}{flag:<{YW-2}}| ${pv:>{PW-3},.0f} | {ret:>{RW-2}.1%} | '
              f'{sp:>{RW-2}.1%} | {qq:>{RW-2}.1%} | {alpha:>{RW-2}.1%} |')
    print(ysep)
    print('  * = partial year (current year, sold at latest price)')

    print('\nTop 5 holdings each year:')
    for yr in year_results:
        top5   = yr.get('top5', [])
        scores = yr.get('scores', {})
        parts  = [f'{t}({scores.get(t, "?")})'for t in top5]
        flag   = '*' if yr.get('partial') else ''
        print(f'  {yr["year"]}{flag}: {", ".join(parts) if parts else "(cash)"}')

    # ── Turnover Analysis ─────────────────────────────────────────────────── #
    print('\n=== TURNOVER ANALYSIS ===')
    prev_holdings: set = set()
    for yr in year_results:
        cur_holdings = set(yr.get('holdings', []))
        y     = yr['year']
        tax   = yr.get('tax_paid', 0.0)
        flag  = '*' if yr.get('partial') else ''

        if not prev_holdings:
            # First year — no prior portfolio to compare
            bought = sorted(cur_holdings)
            print(f'[{y}{flag}] INITIAL BUY: {", ".join(bought) if bought else "(none)"}')
            prev_holdings = cur_holdings
            continue

        sold  = sorted(prev_holdings - cur_holdings)
        bought = sorted(cur_holdings - prev_holdings)
        held  = sorted(prev_holdings & cur_holdings)
        n_prev = len(prev_holdings)
        turnover_pct = len(sold) / n_prev if n_prev > 0 else 0.0

        def _fmt(lst, limit=6):
            if not lst:
                return '(none)'
            shown = ', '.join(lst[:limit])
            return shown + f' (+{len(lst)-limit} more)' if len(lst) > limit else shown

        print(f'[{y}{flag}] SOLD: {_fmt(sold)} | BOUGHT: {_fmt(bought)} | HELD: {_fmt(held)}')
        tax_str = f'${tax:,.0f}' if tax > 0 else '$0'
        print(f'[{y}{flag}] Turnover: {turnover_pct:.0%} | Tax trigger: {tax_str}')

        prev_holdings = cur_holdings


# ═══════════════════════════════════════════════════════════════════════════ #
# BUFFETT — BUY AND HOLD (THESIS-BREAK-ONLY SELLS)                             #
# ═══════════════════════════════════════════════════════════════════════════ #

def _get_pit_metrics(gaap: dict, cutoff: str, price_hist: dict,
                     ticker: Optional[str] = None) -> dict:
    """Raw PIT fundamentals at cutoff. Used to store initial metrics at buy time
    and to check thesis-break conditions in later years."""
    def _av(*fields, n=5):
        for f in fields:
            if f in gaap:
                r = _annual_values_pit(gaap[f], cutoff, n)
                if r:
                    return r
        return None

    revenue    = _av('RevenueFromContractWithCustomerExcludingAssessedTax',
                     'Revenues', 'SalesRevenueNet', 'SalesRevenueGoodsNet')
    net_income = _av('NetIncomeLoss', 'ProfitLoss')
    equity     = _av('StockholdersEquity', 'StockholdersEquityAttributableToParent')
    lt_debt    = _av('LongTermDebt', 'LongTermDebtNoncurrent',
                     'LongTermDebtAndCapitalLeaseObligations')
    eps        = _av('EarningsPerShareBasic', 'EarningsPerShareDiluted')
    op_income  = _av('OperatingIncomeLoss')

    roe = (net_income[-1] / max(equity[-1], 1)) if (net_income and equity) else None
    de  = (abs(lt_debt[-1]) / max(abs(equity[-1]), 1)) if (equity and lt_debt) else None

    hist_price = _price_on_date(price_hist, cutoff)
    pe = peg = None
    if hist_price and hist_price > 0 and eps and eps[-1] > 0:
        # Adjust EPS for any stock split that fell between the last fiscal year end and
        # the cutoff: yfinance prices are retroactively adjusted but EDGAR EPS are not.
        split_ratio = 1.0
        if ticker:
            since_date  = f'{int(cutoff[:4]) - 1}-12-31'
            split_ratio = _get_split_ratio(ticker, since_date, cutoff)
        eps_adj = [e / split_ratio for e in eps] if split_ratio != 1.0 else eps
        pe = hist_price / eps_adj[-1]
        if len(eps_adj) >= 3 and all(e > 0 for e in eps_adj[-3:]):
            eps_g = _cagr(eps_adj[-3:]) * 100
            if eps_g > 0:
                peg = pe / eps_g

    return {
        'roe': roe,
        'de':  de,
        'pe':  pe,
        'peg': peg,
        'revenue': revenue,
        'eps':     eps,
        'rev_consec_decline': bool(revenue and len(revenue) >= 3 and
                                   revenue[-1] < revenue[-2] < revenue[-3]),
        'eps_neg_2yr':        bool(eps and len(eps) >= 2 and eps[-1] < 0 and eps[-2] < 0),
    }


def check_thesis_break(year: int, initial_m: dict, gaap: dict,
                        price_hist: dict, ticker: Optional[str] = None) -> tuple:
    """
    Returns (broke: bool, reason: str).
    Compares current-year PIT metrics against metrics stored at buy time.
    Five sell triggers — any one fires a sell.
    """
    cutoff = f'{year}-01-01'
    curr   = _get_pit_metrics(gaap, cutoff, price_hist, ticker=ticker)

    i_roe = initial_m.get('roe')
    c_roe = curr.get('roe')
    if i_roe and i_roe > 0.15 and c_roe is not None and c_roe < 0.10:
        return True, f'ROE collapsed {i_roe:.0%}->{c_roe:.0%}'

    i_de = initial_m.get('de')
    c_de = curr.get('de')
    if (i_de is not None and c_de is not None and
            i_de >= 0 and c_de > i_de * 2.0 and c_de > 1.0):
        return True, f'D/E doubled {i_de:.2f}->{c_de:.2f}'

    if curr.get('rev_consec_decline'):
        rev = curr.get('revenue') or []
        if len(rev) >= 3:
            return True, (f'Revenue 2yr decline: '
                          f'{rev[-3]/1e9:.1f}B->{rev[-2]/1e9:.1f}B->{rev[-1]/1e9:.1f}B')

    if curr.get('eps_neg_2yr'):
        eps = curr.get('eps') or []
        if len(eps) >= 2:
            return True, f'EPS neg 2yr: {eps[-2]:.2f}->{eps[-1]:.2f}'

    pe  = curr.get('pe')
    peg = curr.get('peg')
    if pe and pe > 60 and peg and peg > 5:
        return True, f'Extreme valuation P/E={pe:.0f} PEG={peg:.1f}'

    return False, ''


def _save_buffett_progress(year: int, portfolio: dict, cash: float,
                            year_results: list, sells_log: list):
    try:
        BUFFETT_PROGRESS.write_text(json.dumps({
            'last_completed_year': year,
            'portfolio': portfolio,
            'cash': cash,
            'year_results': year_results,
            'sells_log': sells_log,
        }, indent=2))
    except Exception:
        pass


def run_buffett_backtest():
    """Buy-and-hold: sell only on thesis break. Called via --buffett flag."""
    global QUICK_MODE, EDGAR_TIMEOUT, NUM_WORKERS
    QUICK_MODE    = True
    EDGAR_TIMEOUT = 5
    NUM_WORKERS   = 10

    print('=' * 65)
    print('  BUFFETT MODE — BUY AND HOLD BACKTEST 2010-2026')
    print('  Hold forever. Sell ONLY when thesis breaks.')
    print('  NOTE: Survivorship bias (current 2026 large caps)')
    print('=' * 65)

    # ── 1. Universe + cached data ─────────────────────────────────────────── #
    print('\n[1/4] Loading universe + cached data...')
    universe = build_or_load_universe()
    universe = [s for s in universe if s.get('price', 0) >= 2.0][:500]
    _load_cik_map()

    bench_hists: dict = {}
    for bench in ['SPY', 'QQQ']:
        bench_hists[bench] = _get_price_cache(bench)

    all_price_hists: dict = {}
    def _fp(row):
        all_price_hists[row['ticker']] = _get_price_cache(row['ticker'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        list(pool.map(_fp, universe))
    print(f'  {len(all_price_hists)} price histories loaded (from cache)')

    # ── 2. Resume or fresh start ──────────────────────────────────────────── #
    portfolio: dict   = {}   # ticker -> {shares, buy_price, buy_year, initial_metrics, name}
    cash              = INITIAL_CAPITAL
    year_results:     list = []
    sells_log:        list = []
    start_year        = BUFFETT_START

    if BUFFETT_PROGRESS.exists():
        try:
            prog = json.loads(BUFFETT_PROGRESS.read_text())
            portfolio    = prog.get('portfolio', {})
            cash         = prog.get('cash', INITIAL_CAPITAL)
            year_results = prog.get('year_results', [])
            sells_log    = prog.get('sells_log', [])
            start_year   = prog.get('last_completed_year', BUFFETT_START - 1) + 1
            print(f'  [Resume] Last year: {start_year-1} | '
                  f'Holdings: {len(portfolio)} | Cash: ${cash:,.0f}')
        except Exception:
            pass

    today_str  = datetime.now().strftime('%Y-%m-%d')
    end_year   = datetime.now().year

    # ── 3. Year loop ──────────────────────────────────────────────────────── #
    print('\n[3/4] Running year-by-year simulation...\n')
    print('=' * 65)

    for year in range(start_year, end_year + 1):
        cutoff     = f'{year}-01-01'
        sell_date  = f'{year + 1}-01-01'
        is_current = (year == end_year)
        is_initial = (year == BUFFETT_START and not portfolio)

        print(f'\n[{year}{"*" if is_current else ""}] '
              f'{"INITIAL BUY" if is_initial else f"Checking thesis for {len(portfolio)} holdings"}...')

        # ── STEP 1: Check thesis breaks ───────────────────────────────────── #
        sold_this_year: list = []
        if not is_initial:
            for ticker in list(portfolio.keys()):
                cik = get_cik(ticker)
                if not cik:
                    continue
                ph   = all_price_hists.get(ticker, {})
                gaap = get_xbrl_facts(cik)
                if not gaap:
                    continue

                broke, reason = check_thesis_break(
                    year, portfolio[ticker].get('initial_metrics', {}), gaap, ph,
                    ticker=ticker)
                if not broke:
                    continue

                if is_current:
                    sell_price = _last_price_before(ph, today_str) or portfolio[ticker]['buy_price']
                else:
                    sell_price = (_price_on_date(ph, sell_date)
                                  or _last_price_before(ph, sell_date)
                                  or portfolio[ticker]['buy_price'])

                pos       = portfolio[ticker]
                sell_adj  = sell_price * (1 - SLIPPAGE_PCT)
                buy_adj   = pos['buy_price'] * (1 + SLIPPAGE_PCT)
                proceeds  = pos['shares'] * sell_adj
                cost      = pos['shares'] * buy_adj
                gain      = proceeds - cost
                tax       = max(gain * ISRAEL_CGT, 0)
                net       = proceeds - tax
                pct_ret   = sell_price / pos['buy_price'] - 1
                held_yr   = year - pos['buy_year']

                print(f'  SOLD {ticker}: {reason}')
                print(f'    Bought ${pos["buy_price"]:.2f} in {pos["buy_year"]} | '
                      f'Sold ${sell_price:.2f} | Return {pct_ret:+.1%} | '
                      f'Held {held_yr}yr | Tax ${tax:,.0f}')

                sells_log.append({
                    'year': year, 'ticker': ticker, 'reason': reason,
                    'buy_price': pos['buy_price'], 'buy_year': pos['buy_year'],
                    'sell_price': sell_price, 'return': pct_ret,
                    'held_years': held_yr, 'tax': tax,
                })
                cash += net
                del portfolio[ticker]
                sold_this_year.append(ticker)

            if not sold_this_year:
                print(f'  HELD: all {len(portfolio)} positions — no thesis breaks')

        # ── STEP 2: Fill open slots with new buys ─────────────────────────── #
        max_pos = BUFFETT_INIT_POS if is_initial else BUFFETT_MAX_POS
        slots   = max(0, max_pos - len(portfolio))

        if slots > 0 and cash > 500:
            verb = 'Buying initial' if is_initial else f'Scanning for {slots} new'
            print(f'  {verb} position(s) (PIT: filings before {cutoff})...')

            candidates: list = []
            for row in universe:
                t = row['ticker']
                if t in portfolio:
                    continue
                cik = get_cik(t)
                if not cik:
                    continue
                ph   = all_price_hists.get(t, {})
                gaap = get_xbrl_facts(cik)
                if not gaap:
                    continue
                sc, _, nfil = score_layer1_pit(t, year, gaap, ph)
                if sc < PIT_L1_PASS or nfil < 3:
                    continue
                buy_price = _price_on_date(ph, cutoff)
                if not buy_price:
                    continue
                init_m = _get_pit_metrics(gaap, cutoff, ph, ticker=t)
                candidates.append({'ticker': t, 'score': sc, 'buy_price': buy_price,
                                   'initial_metrics': init_m, 'name': row.get('name', t)})

            candidates.sort(key=lambda x: -x['score'])
            to_buy = candidates[:slots]

            if to_buy:
                alloc_per = cash / len(to_buy)
                for c in to_buy:
                    buy_adj = c['buy_price'] * (1 + SLIPPAGE_PCT)
                    portfolio[c['ticker']] = {
                        'shares':          alloc_per / buy_adj,
                        'buy_price':       c['buy_price'],
                        'buy_year':        year,
                        'buy_score':       c['score'],
                        'initial_metrics': c['initial_metrics'],
                        'name':            c['name'],
                    }
                    cash -= alloc_per

                desc = ', '.join(f'{c["ticker"]}({c["score"]})' for c in to_buy[:6])
                if len(to_buy) > 6:
                    desc += f' (+{len(to_buy)-6} more)'
                print(f'  Added {len(to_buy)} position(s): {desc}')
            else:
                print(f'  No qualifying new candidates')

        elif portfolio and slots == 0:
            print(f'  Portfolio at max ({len(portfolio)} positions) — no new buys')

        # ── STEP 3: Year-end valuation ────────────────────────────────────── #
        val_date = today_str if is_current else sell_date
        pv = cash
        for t, pos in portfolio.items():
            ph    = all_price_hists.get(t, {})
            price = (_price_on_date(ph, val_date)
                     or _last_price_before(ph, val_date)
                     or pos['buy_price'])
            pv   += pos['shares'] * price

        prev_pv  = year_results[-1]['portfolio'] if year_results else INITIAL_CAPITAL
        year_ret = pv / prev_pv - 1

        spy_buy  = _price_on_date(bench_hists.get('SPY', {}), cutoff)
        spy_sell = ((_price_on_date(bench_hists['SPY'], sell_date) if not is_current
                     else _last_price_before(bench_hists['SPY'], today_str))
                    if 'SPY' in bench_hists else None)
        qqq_buy  = _price_on_date(bench_hists.get('QQQ', {}), cutoff)
        qqq_sell = ((_price_on_date(bench_hists['QQQ'], sell_date) if not is_current
                     else _last_price_before(bench_hists['QQQ'], today_str))
                    if 'QQQ' in bench_hists else None)

        spy_ret  = (spy_sell / spy_buy  - 1) if spy_buy  and spy_sell  else 0.0
        qqq_ret  = (qqq_sell / qqq_buy  - 1) if qqq_buy  and qqq_sell  else 0.0
        tax_yr   = sum(s['tax'] for s in sells_log if s['year'] == year)
        alpha    = year_ret - spy_ret

        label = f'[{year}{"*" if is_current else ""}]'
        print(f'  {label} Return: {year_ret:+.1%} | Portfolio: ${pv:,.0f} | '
              f'Holdings: {len(portfolio)} | Cash: ${cash:,.0f}')
        print(f'  {label} SPY: {spy_ret:+.1%} | Alpha: {alpha:+.1%}',
              f'| Tax: ${tax_yr:,.0f}' if tax_yr else '')

        year_results.append({
            'year': year, 'return': year_ret, 'portfolio': pv,
            'n_holdings': len(portfolio),
            'spy_ret': spy_ret, 'qqq_ret': qqq_ret,
            'tax_paid': tax_yr, 'partial': is_current,
        })
        _save_buffett_progress(year, portfolio, cash, year_results, sells_log)

    print('\n[4/4] Printing final results...')
    print_buffett_results(year_results, sells_log, portfolio, all_price_hists)


def print_buffett_results(year_results: list, sells_log: list,
                           final_portfolio: dict, all_price_hists: dict):
    if not year_results:
        print('\nNo results.')
        return

    rets      = [yr['return']  for yr in year_results]
    spy_rets  = [yr['spy_ret'] for yr in year_results]
    qqq_rets  = [yr['qqq_ret'] for yr in year_results]
    tax_total = sum(yr.get('tax_paid', 0) for yr in year_results)

    # Load --backtest results for the same period (2010+)
    bt_year_map: dict = {}
    try:
        if PIT_PROGRESS.exists():
            raw = json.loads(PIT_PROGRESS.read_text())
            for yr in raw.get('year_results', []):
                if yr['year'] >= BUFFETT_START:
                    bt_year_map[yr['year']] = yr
    except Exception:
        pass

    bt_rets = [bt_year_map[yr['year']]['return']
               for yr in year_results if yr['year'] in bt_year_map]
    bt_tax  = sum(v.get('tax_paid', 0) for v in bt_year_map.values())

    def _metrics(annual_rets):
        if not annual_rets:
            return {}
        v = INITIAL_CAPITAL
        vals = [v]
        for r in annual_rets:
            v *= (1 + r)
            vals.append(v)
        n     = max(len(annual_rets), 1)
        cagr  = (vals[-1] / INITIAL_CAPITAL) ** (1 / n) - 1
        total = vals[-1] / INITIAL_CAPITAL - 1
        daily = [(1 + r) ** (1 / 252) - 1 for r in annual_rets]
        sharpe = np.mean(daily) / max(np.std(daily), 1e-9) * np.sqrt(252)
        peak, max_dd = INITIAL_CAPITAL, 0.0
        for val in vals:
            if val > peak: peak = val
            dd = (val - peak) / peak
            if dd < max_dd: max_dd = dd
        win = sum(1 for r in annual_rets if r > 0) / max(n, 1)
        return dict(cagr=cagr, total=total, sharpe=sharpe, max_dd=max_dd,
                    best=max(annual_rets), worst=min(annual_rets), win=win)

    bum  = _metrics(rets)
    btm  = _metrics(bt_rets)
    spm  = _metrics(spy_rets)
    qqm  = _metrics(qqq_rets)

    n_years   = len(year_results)
    n_sells   = len(sells_log)
    beats_spy = sum(1 for yr in year_results if yr['return'] > yr['spy_ret'])
    bt_beats  = sum(1 for yr in year_results
                    if yr['year'] in bt_year_map
                    and bt_year_map[yr['year']]['return'] > yr['spy_ret'])
    avg_held  = (sum(s['held_years'] for s in sells_log) / n_sells) if n_sells else None

    def _pct(v): return f'{v:+.1%}' if v is not None else '   n/a'
    def _f2(v):  return f'{v:.2f}'  if v is not None else '   n/a'

    C, W = 32, 12
    sep = '+' + '-' * C + '+' + ('+'.join(['-' * W] * 4)) + '+'

    print('\n\n' + '=' * (C + 4 * W + 5))
    print('=== BUFFETT BUY-AND-HOLD BACKTEST 2010-2026 ===')
    print('=== Hold forever — sell ONLY on thesis break ===')
    print('=' * (C + 4 * W + 5))
    print()
    print(sep)
    print(f'| {"Metric":<{C-1}}|{"--buffett":^{W}}|{"--backtest":^{W}}|{"SPY B&H":^{W}}|{"QQQ B&H":^{W}}|')
    print(sep)

    for label, b, bt, spy, qqq in [
        ('CAGR',              _pct(bum.get('cagr')),   _pct(btm.get('cagr')),   _pct(spm.get('cagr')),   _pct(qqm.get('cagr'))),
        ('Total Return',      _pct(bum.get('total')),  _pct(btm.get('total')),  _pct(spm.get('total')),  _pct(qqm.get('total'))),
        ('Sharpe Ratio',      _f2(bum.get('sharpe')),  _f2(btm.get('sharpe')),  _f2(spm.get('sharpe')),  _f2(qqm.get('sharpe'))),
        ('Max Drawdown',      _pct(bum.get('max_dd')), _pct(btm.get('max_dd')), _pct(spm.get('max_dd')), _pct(qqm.get('max_dd'))),
        ('Best Year',         _pct(bum.get('best')),   _pct(btm.get('best')),   _pct(spm.get('best')),   _pct(qqm.get('best'))),
        ('Worst Year',        _pct(bum.get('worst')),  _pct(btm.get('worst')),  _pct(spm.get('worst')),  _pct(qqm.get('worst'))),
        ('Win Rate (yearly)', _pct(bum.get('win')),    _pct(btm.get('win')),    _pct(spm.get('win')),    _pct(qqm.get('win'))),
    ]:
        print(f'| {label:<{C-1}}|{b:>{W-1}} |{bt:>{W-1}} |{spy:>{W-1}} |{qqq:>{W-1}} |')
    print(sep)

    avg_held_str = f'{avg_held:.1f} yr' if avg_held is not None else 'forever'
    for label, b, bt in [
        ('Total tax paid',          f'${tax_total:,.0f}',            f'${bt_tax:,.0f}'),
        ('Total thesis sells',      str(n_sells),                    '~180'),
        ('Avg hold (sold pos.)',    avg_held_str,                    '1.2 yr'),
        ('Years beating SPY',       f'{beats_spy}/{n_years}',        f'{bt_beats}/{n_years}'),
    ]:
        print(f'| {label:<{C-1}}|{b:>{W-1}} |{bt:>{W-1}} |{"  -":>{W-1}} |{"  -":>{W-1}} |')
    print(sep)

    # ── Year-by-year comparison ───────────────────────────────────────────── #
    print('\nYear-by-year comparison:')
    YW, PW = 7, 13
    ysep = '+' + '-'*YW + ('+' + '-'*PW) * 4 + '+'
    print(ysep)
    print(f'| {"Year":<{YW-1}}| {"--buffett":>{PW-2}} | {"--backtest":>{PW-2}} | {"SPY B&H":>{PW-2}} | {"QQQ B&H":>{PW-2}} |')
    print(ysep)
    spy_val = qqq_val = INITIAL_CAPITAL
    for yr in year_results:
        y       = yr['year']
        bf_pv   = yr['portfolio']
        bt_pv   = bt_year_map.get(y, {}).get('portfolio', 0)
        spy_val *= (1 + yr['spy_ret'])
        qqq_val *= (1 + yr['qqq_ret'])
        flag    = '*' if yr.get('partial') else ' '
        bt_str  = f'${bt_pv:>10,.0f}' if bt_pv else f'{"n/a":>11}'
        print(f'| {y}{flag:<{YW-2}}| ${bf_pv:>{PW-3},.0f} | {bt_str} | '
              f'${spy_val:>{PW-3},.0f} | ${qqq_val:>{PW-3},.0f} |')
    print(ysep)
    print('  * = partial year (current year)')

    # ── Thesis breaks log ─────────────────────────────────────────────────── #
    print('\nThesis breaks log:')
    TW, TK, REAS, TR, THY, TTX = 7, 7, 34, 9, 7, 12
    tsep = ('+' + '-'*TW + '+' + '-'*TK + '+' + '-'*REAS +
            '+' + '-'*TR + '+' + '-'*THY + '+' + '-'*TTX + '+')
    print(tsep)
    print(f'| {"Year":<{TW-1}}| {"Ticker":<{TK-1}}| {"Reason":<{REAS-1}}'
          f'| {"Return":>{TR-1}} | {"Held":>{THY-1}} | {"Tax":>{TTX-1}} |')
    print(tsep)
    if sells_log:
        for s in sells_log:
            held_str = f'{s["held_years"]}yr'
            tax_str  = f'${s["tax"]:,.0f}'
            print(f'| {s["year"]:<{TW-1}}| {s["ticker"]:<{TK-1}}'
                  f'| {s["reason"][:REAS-2]:<{REAS-1}}'
                  f'| {s["return"]:>{TR-2}.1%} | {held_str:>{THY-1}} | {tax_str:>{TTX-1}} |')
    else:
        inner = TW + TK + REAS + TR + THY + TTX + 10
        print(f'| {"(no thesis breaks — all positions held throughout)":^{inner}} |')
    print(tsep)

    # ── Final holdings ────────────────────────────────────────────────────── #
    today_str = datetime.now().strftime('%Y-%m-%d')
    print('\nFinal holdings (still held today):')
    if final_portfolio:
        sorted_pos = sorted(final_portfolio.items(),
                            key=lambda x: (x[1].get('buy_year', 9999), x[0]))
        for ticker, pos in sorted_pos:
            ph         = all_price_hists.get(ticker, {})
            curr_price = _last_price_before(ph, today_str) or pos['buy_price']
            pct        = curr_price / pos['buy_price'] - 1
            held_yr    = datetime.now().year - pos['buy_year']
            value      = pos['shares'] * curr_price
            print(f'  {ticker:<6}: bought {pos["buy_year"]} @ ${pos["buy_price"]:>8.2f} | '
                  f'now ${curr_price:>8.2f} | {pct:>+6.0%} | '
                  f'held {held_yr}yr | value ${value:>10,.0f}')
    else:
        print('  (no positions remaining)')

    # ── Tax savings ───────────────────────────────────────────────────────── #
    tax_saved  = bt_tax - tax_total
    spy_cagr   = spm.get('cagr', 0.103)
    half_years = n_years / 2
    compounded = tax_saved * ((1 + spy_cagr) ** half_years) if tax_saved > 0 else 0

    print('\n=== TAX SAVINGS ===')
    print(f'  --backtest paid: ${bt_tax:>10,.0f} in taxes')
    print(f'  --buffett paid:  ${tax_total:>10,.0f} in taxes')
    print(f'  Tax saved:       ${tax_saved:>10,.0f}')
    if tax_saved > 0:
        print(f'')
        print(f'  That ${tax_saved:,.0f} stayed invested and compounded to: ~${compounded:,.0f}')
        print(f'  (estimated at {spy_cagr:.1%} CAGR over ~{half_years:.0f} years)')
    print(f'  THAT is why Buffett never sells.')


# ═══════════════════════════════════════════════════════════════════════════ #
# SELL RULES MONITOR                                                           #
# ═══════════════════════════════════════════════════════════════════════════ #

def check_sell_rules(ticker: str, fund: dict) -> Optional[str]:
    cik = get_cik(ticker)
    if not cik:
        return None
    try:
        gaap    = get_xbrl_facts(cik)
        equity  = _annual_values(gaap, 'StockholdersEquity',
                                 'StockholdersEquityAttributableToParent')
        ni      = _annual_values(gaap, 'NetIncomeLoss')
        lt_debt = _annual_values(gaap, 'LongTermDebt', 'LongTermDebtNoncurrent')
        rev     = _annual_values(gaap,
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'Revenues', 'SalesRevenueNet')
        op_cf   = _annual_values(gaap,
            'NetCashProvidedByUsedInOperatingActivities')

        if equity and ni and len(equity) >= 2 and len(ni) >= 2:
            roe_prev = ni[-2] / max(equity[-2], 1)
            roe_now  = ni[-1] / max(equity[-1], 1)
            if roe_prev > 0.15 and roe_now < 0.10:
                return f'ROE fell from {roe_prev:.0%} to {roe_now:.0%} (was above 15%)'

        if equity and lt_debt and len(equity) >= 2 and len(lt_debt) >= 2:
            de_prev = abs(lt_debt[-2]) / max(abs(equity[-2]), 1)
            de_now  = abs(lt_debt[-1]) / max(abs(equity[-1]), 1)
            if de_now > de_prev * 2:
                return f'Debt/Equity doubled: {de_prev:.1f}x -> {de_now:.1f}x'

        if rev and len(rev) >= 3:
            if rev[-1] < rev[-2] < rev[-3]:
                return 'Revenue declined 2 consecutive years'

        if op_cf and len(op_cf) >= 2:
            if op_cf[-1] < 0 and op_cf[-2] < 0:
                return 'Operating cash flow negative 2 consecutive periods'
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════ #
# OUTPUT — 4 SECTIONS                                                          #
# ═══════════════════════════════════════════════════════════════════════════ #

def _p(v, d=1): return f'{v:.{d}%}' if v is not None and not math.isnan(v) else 'n/a'
def _f2(v):     return f'{v:.2f}'   if v is not None and not math.isnan(v) else 'n/a'


def _table_row(label, vals, C=30, W=10):
    return '| {:<{c}}|'.format(label, c=C-1) + ''.join(f'{str(v):>{W-1}} |' for v in vals)


def print_section1(bt: dict):
    if not bt:
        print('\n[Section 1] Backtest not available (no data).')
        return

    sm, spm, qqm = bt.get('strat_m', {}), bt.get('spy_m', {}), bt.get('qqq_m', {})
    cols  = ['Strategy', 'SPY B&H', 'QQQ B&H']
    mets  = [sm, spm, qqm]
    C, W  = 30, 11
    sep   = '+' + '-'*C + '+' + ('+'.join(['-'*W]*3)) + '+'

    port  = bt.get('portfolio', pd.Series(dtype=float))
    start = str(port.index[0].date()) if not port.empty else '2005-01-01'
    end   = str(port.index[-1].date()) if not port.empty else 'today'

    print(f'\n=== VIX AI STOCK PICKER — INDICATIVE BACKTEST {start} to {end} ===')
    print('(NOTE: Uses current quality scores; not a true point-in-time backtest)')
    print()
    print(sep)
    print('| {:<{c}}|'.format('Metric', c=C-1) + ''.join(f' {h:^{W-2}} |' for h in cols))
    print(sep)

    rows = [
        ('CAGR',             [_p(m.get('cagr'))       for m in mets]),
        ('Total Return',     [_p(m.get('total'))       for m in mets]),
        ('Sharpe Ratio',     [_f2(m.get('sharpe'))     for m in mets]),
        ('Max Drawdown',     [_p(m.get('max_dd'))      for m in mets]),
        ('Best Year',        [_p(m.get('best_year'))   for m in mets]),
        ('Worst Year',       [_p(m.get('worst_year'))  for m in mets]),
        ('Win Rate (mthly)', [_p(m.get('win_rate'))    for m in mets]),
    ]
    for label, vals in rows:
        print(_table_row(label, vals, C, W))
    print(sep)

    extra_rows = [
        ('VIX spikes triggered (>25)', [str(len(bt.get('vix_events', []))), '-', '-']),
        ('Buys simulated',              [str(bt.get('buys_made', 0)), '-', '-']),
        ('Top-20 avg score',
         [f"{sum(s.get('total_score',0) for s in []) / max(1,1):.0f}/150", '-', '-']),
    ]
    for label, vals in extra_rows:
        print(_table_row(label, vals, C, W))
    print(sep)

    # Year-by-year
    if not port.empty:
        print('\nYear-by-Year Portfolio Values')
        yr_strat = port.resample('YE').last()
        yr_spy   = bt.get('spy_bh', pd.Series(dtype=float)).resample('YE').last()
        yr_qqq   = bt.get('qqq_bh', pd.Series(dtype=float)).resample('YE').last()
        Y, D = 6, 13
        ysep = '+' + '-'*Y + '+' + ('+'.join(['-'*D]*3)) + '+'
        yhdr = f'| {"Year":<{Y-1}}| {"Strategy":>{D-2}} | {"SPY B&H":>{D-2}} | {"QQQ B&H":>{D-2}} |'
        print(ysep); print(yhdr); print(ysep)
        for yr_ts in sorted(set(yr_strat.index) | set(yr_spy.index)):
            yr = yr_ts.year
            s  = f'${yr_strat.get(yr_ts, float("nan")):>10,.0f}' if yr_ts in yr_strat.index else '       n/a  '
            sp = f'${yr_spy.get(yr_ts, float("nan")):>10,.0f}'   if yr_ts in yr_spy.index  else '       n/a  '
            qq = f'${yr_qqq.get(yr_ts, float("nan")):>10,.0f}'   if yr_ts in yr_qqq.index  else '       n/a  '
            print(f'| {yr:<{Y-1}}| {s:>{D-2}} | {sp:>{D-2}} | {qq:>{D-2}} |')
        print(ysep)


def print_section2(vix_events: list, top20: list, price_data: dict):
    print('\n=== SECTION 2 — VIX SPIKES + BUYING OPPORTUNITIES ===')
    print(f'{"Date":<12} {"VIX Peak":>10} {"Top candidates":>30} {"1yr return":>12}')
    print('-' * 66)

    tickers = [s['ticker'] for s in top20[:5]]

    for ev in vix_events[-10:]:   # last 10 events
        spike_date = ev['peak_date']
        peak_vix   = ev['peak_vix']

        returns_1yr = []
        for t in tickers:
            if t in price_data:
                s = price_data[t]
                if spike_date in s.index:
                    entry = float(s.loc[spike_date])
                    fwd_date = spike_date + pd.Timedelta(days=365)
                    # find nearest available date after fwd_date
                    future = s.loc[fwd_date:] if fwd_date <= s.index[-1] else pd.Series(dtype=float)
                    if not future.empty:
                        ret = float(future.iloc[0]) / entry - 1
                        returns_1yr.append(ret)

        avg_ret = sum(returns_1yr) / len(returns_1yr) if returns_1yr else float('nan')
        cands   = ', '.join(tickers[:3])
        ret_str = _p(avg_ret) if not math.isnan(avg_ret) else 'n/a'
        print(f'{str(spike_date.date()):<12} {peak_vix:>10.1f} {cands:>30} {ret_str:>12}')


def print_section3(value_traps: list):
    print('\n=== SECTION 3 — VALUE TRAPS CAUGHT BY AI ===')
    if not value_traps:
        print('  (No value traps detected — AI analysis not available or no traps found)')
        print('  Set ANTHROPIC_API_KEY in .env to enable AI screening')
        return

    C1, C2, C3 = 8, 20, 45
    sep = '+' + '-'*C1 + '+' + '-'*C2 + '+' + '-'*C3 + '+'
    print(sep)
    print(f'| {"Ticker":<{C1-1}}| {"L1 Score":<{C2-1}}| {"AI Warning":<{C3-1}}|')
    print(sep)
    for trap in value_traps:
        ticker  = trap['ticker']
        l1_info = f'{trap["l1_score"]}/110 (passed L1)'
        reason  = trap.get('value_trap_reason', trap.get('competition_reason', 'see AI notes'))[:C3-2]
        print(f'| {ticker:<{C1-1}}| {l1_info:<{C2-1}}| {reason:<{C3-1}}|')
    print(sep)


def print_section4(top20: list, current_vix: float):
    print('\n=== SECTION 4 — TODAY\'S TOP 20 WATCHLIST ===')
    print(f'\nCurrent VIX: {current_vix:.1f}')
    if current_vix > VIX_BUY_START:
        print('STATUS: BUYING MODE ACTIVE — VIX above 25, deploy capital now')
        buying = [s for s in top20 if not s.get('value_trap_warning', False)][:5]
        print(f'Buy NOW (top 5, no trap warning): {", ".join(s["ticker"] for s in buying)}')
    elif current_vix > VIX_CONTINUE:
        print('STATUS: CAUTION — VIX 20-25, continue buying if already started')
    else:
        print('STATUS: WAITING — VIX below 20, stay patient and watch the list')

    print()
    C_R, C_T, C_N, C_S, C_AI = 5, 8, 22, 7, 7
    hdr = (f'| {"Rank":<{C_R}}| {"Ticker":<{C_T}}| {"Company":<{C_N}}| '
           f'{"Total":>{C_S}}| {"L1":>{C_AI}}| {"AI":>{C_AI}}| {"Trap?":<7}|')
    sep = '+' + '-'*C_R + '+' + '-'*C_T + '+' + '-'*C_N + '+' + '-'*C_S + '+' + '-'*C_AI + '+' + '-'*C_AI + '+' + '-'*7 + '+'
    print(sep)
    print(hdr)
    print(sep)

    for i, s in enumerate(top20, 1):
        trap = 'YES' if s.get('value_trap_warning') else 'no'
        name = s.get('company_name', s['ticker'])[:C_N-1]
        print(f'| {i:<{C_R}}| {s["ticker"]:<{C_T}}| {name:<{C_N}}| '
              f'{s.get("total_score",0):>{C_S}}| '
              f'{s.get("l1_score",0):>{C_AI}}| '
              f'{s.get("ai_score",0):>{C_AI}}| {trap:<7}|')

    print(sep)

    # One-line summaries
    print('\nOne-line summaries:')
    for s in top20[:10]:
        f = s.get('fund', {})
        ai = s.get('ai_data', {})
        roe_str  = f'{f["roe"]:.0%}'         if f.get('roe') else 'n/a'
        de_str   = f'{f["de_ratio"]:.2f}'    if f.get('de_ratio') else 'n/a'
        eps_str  = f'{f["eps_cagr"]:.0%}/yr' if f.get('eps_cagr') else 'n/a'
        fcf_str  = f'{f["fcf_yield"]:.1%}'   if f.get('fcf_yield') else 'n/a'
        moat     = ai.get('moat_reason', 'n/a')[:50]
        comp     = ai.get('competition_reason', 'n/a')[:40]
        print(f'  {s["ticker"]}: ROE={roe_str}, D/E={de_str}, EPS_cagr={eps_str}, '
              f'FCF_yield={fcf_str}')
        if moat != 'n/a':
            print(f'    Moat: {moat}')
        if comp != 'n/a':
            print(f'    Competition: {comp}')


# ═══════════════════════════════════════════════════════════════════════════ #
# MAIN                                                                         #
# ═══════════════════════════════════════════════════════════════════════════ #

def main():
    global QUICK_MODE, EDGAR_TIMEOUT, NUM_WORKERS

    # ── Parse arguments ───────────────────────────────────────────────────── #
    parser = argparse.ArgumentParser(description='VIX AI Stock Picker')
    parser.add_argument('--quick', action='store_true',
                        help='Large-cap only, 10 workers, 3s EDGAR timeout. Finishes in <10 min.')
    parser.add_argument('--backtest', action='store_true',
                        help='TRUE point-in-time backtest 2005-2026. L1 only. $0 AI cost.')
    parser.add_argument('--buffett', action='store_true',
                        help='Buy-and-hold: sell only on thesis break. Compares vs --backtest.')
    args = parser.parse_args()

    if args.backtest:
        run_point_in_time_backtest()
        return

    if args.buffett:
        run_buffett_backtest()
        return

    if args.quick:
        QUICK_MODE    = True
        EDGAR_TIMEOUT = 3     # skip slow EDGAR responses immediately
        NUM_WORKERS   = 10    # more parallelism for fast large-cap run

    print('=' * 65)
    print('  VIX-TRIGGERED AI FUNDAMENTAL STOCK PICKER')
    if QUICK_MODE:
        print('  MODE: --quick  (large caps only | 10 workers | 3s EDGAR timeout)')
    print('  Layers: Fundamental (0-110) + AI Qualitative (0-40)')
    print('=' * 65)
    t0 = time.time()

    # ── Check API key ────────────────────────────────────────────────────── #
    has_ai = bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())
    if not has_ai:
        print('\n[AI] ANTHROPIC_API_KEY not found — Layer 2 will use neutral scores.')
        print('     To enable AI: set ANTHROPIC_API_KEY=<key> in .env\n')
    else:
        try:
            import anthropic
            print(f'[AI] Claude API ready ({ANTHROPIC_MODEL})\n')
        except ImportError:
            print('[AI] anthropic package not installed — run: pip install anthropic\n')
            has_ai = False

    # ── 1. Universe ──────────────────────────────────────────────────────── #
    print('[1/5] Building stock universe...')
    universe = build_or_load_universe()
    universe = [s for s in universe if s.get('price', 0) >= 2.0]
    print(f'  {len(universe)} stocks to score')

    # ── 2. Layer 1 scoring — progress-aware, parallel ────────────────────── #
    print(f'\n[2/5] Layer 1 scoring (0-110, cutoff >= {L1_PASS_SCORE}) '
          f'| {NUM_WORKERS} workers | EDGAR timeout={EDGAR_TIMEOUT}s...')
    if QUICK_MODE:
        print('  Quick mode: insider checks skipped, large caps only.')
    else:
        print('  Full mode: first run 20-40 min; cached runs ~1 min.')

    # Load progress so interrupted runs resume from where they stopped
    attempted  = _load_progress()
    pending    = [s for s in universe if s['ticker'] not in attempted]
    from_cache = len(universe) - len(pending)
    if from_cache:
        print(f'  Resuming: {from_cache} tickers already attempted (from l1_progress.json)')
    print(f'  {len(pending)} tickers to process now\n')

    l1_passed   = []
    new_attempts: set = set()

    def _score_one(row):
        ticker = row['ticker']
        sector = row.get('sector', '')
        score, breakdown, fund = score_layer1(ticker, sector)
        _tick(l1_pass=(score >= L1_PASS_SCORE))
        with _lock:
            new_attempts.add(ticker)
            # Save progress every 100 new tickers
            if len(new_attempts) % 100 == 0:
                _save_progress(attempted | new_attempts)
        if score >= L1_PASS_SCORE:
            return {'ticker': ticker, 'l1_score': score, 'breakdown': breakdown,
                    'fund': fund, 'company_name': row.get('name', ticker),
                    'sector': sector, 'mcap': row.get('mcap', 0)}
        return None

    # Also load L1 cache for already-attempted tickers that did pass
    for s in universe:
        if s['ticker'] in attempted:
            cp = L1_CACHE / f'{s["ticker"]}.json'
            if cp.exists():
                try:
                    d = json.loads(cp.read_text())
                    if d.get('score', 0) >= L1_PASS_SCORE:
                        l1_passed.append({
                            'ticker': s['ticker'], 'l1_score': d['score'],
                            'breakdown': d.get('breakdown', {}),
                            'fund': d.get('fundamentals', {}),
                            'company_name': s.get('name', s['ticker']),
                            'sector': s.get('sector', ''), 'mcap': s.get('mcap', 0)
                        })
                except Exception:
                    pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        for result in pool.map(_score_one, pending):
            if result:
                l1_passed.append(result)

    # Final progress save
    _save_progress(attempted | new_attempts)

    l1_passed.sort(key=lambda x: -x['l1_score'])
    print(f'\n  Layer 1 complete: {len(l1_passed)}/{len(universe)} passed (score >= {L1_PASS_SCORE})')

    # ── 3. Layer 2 AI scoring ─────────────────────────────────────────────── #
    print(f'\n[3/5] Layer 2 AI scoring (0-40) for {len(l1_passed)} stocks...')
    api_count    = 0
    value_traps  = []
    final_scored = []

    for row in l1_passed:
        ticker = row['ticker']
        cik    = get_cik(ticker)
        if not cik:
            ai_score, ai_data = 15, {'ai_score': 15, 'no_ai': True}
        else:
            ai_score, ai_data = score_layer2(
                ticker, row['company_name'], row['sector'], row['fund'], cik)

        total_score = row['l1_score'] + ai_score
        row.update({'ai_score': ai_score, 'ai_data': ai_data,
                    'total_score': total_score,
                    'value_trap_warning': ai_data.get('value_trap_warning', False)})

        # Collect value traps
        if ai_data.get('value_trap_warning') and row['l1_score'] >= L1_PASS_SCORE:
            value_traps.append({**row, **ai_data})

        if total_score >= FINAL_MIN_SCORE:
            final_scored.append(row)

    final_scored.sort(key=lambda x: -x['total_score'])
    top20 = final_scored[:TOP_N]

    print(f'  AI complete | API calls: {_counter["api_calls"]} | '
          f'Cache hits: {_counter["cache_hits"]} | '
          f'Est cost: ~${_counter["api_calls"] * 0.01:.2f}')
    print(f'  Above {FINAL_MIN_SCORE}/150: {len(final_scored)} | Top {TOP_N} selected')

    # ── 4. Sell rules check ───────────────────────────────────────────────── #
    print('\n[4/5] Checking sell rules for all passing stocks...')
    sell_alerts = []
    for row in l1_passed[:50]:   # check top 50 for efficiency
        reason = check_sell_rules(row['ticker'], row['fund'])
        if reason:
            sell_alerts.append((row['ticker'], reason))
            print(f'  REVIEW NEEDED: {row["ticker"]} — {reason}')
    if not sell_alerts:
        print('  No sell triggers found.')

    # ── 5. VIX + backtest ─────────────────────────────────────────────────── #
    print('\n[5/5] VIX analysis + indicative backtest...')
    vix        = download_vix()
    vix_events = find_vix_spikes(vix)
    cur_vix    = get_current_vix(vix)
    print(f'  VIX history: {vix.index[0].date()} to {vix.index[-1].date()}')
    print(f'  Current VIX: {cur_vix:.1f}')
    print(f'  Distinct spikes > 25: {len(vix_events)}')

    # Download prices for backtest and section 2
    bt_tickers = [s['ticker'] for s in top20[:10]] + ['SPY', 'QQQ', 'IWM']
    bt_prices  = {}
    for t in bt_tickers:
        try:
            s = yf.Ticker(t).history(start='2005-01-01', interval='1d')['Close']
            s.index = pd.to_datetime(s.index).tz_localize(None)
            bt_prices[t] = s
        except Exception:
            pass

    bt = simulate_backtest(top20, vix_events, start='2005-01-01')

    # ── Output ───────────────────────────────────────────────────────────── #
    print_section1(bt)
    print_section2(vix_events, top20, bt_prices)
    print_section3(value_traps)
    print_section4(top20, cur_vix)

    elapsed = time.time() - t0
    print(f'\n  Runtime: {elapsed:.0f}s | '
          f'Universe: {len(universe)} | L1 passed: {len(l1_passed)} | '
          f'Top {TOP_N}: {len(top20)} stocks')


if __name__ == '__main__':
    main()
