"""
paper_trading.py
Runs the sp500_backtest strategy live against an Alpaca paper-trading account:
same universe (Wikipedia S&P 500), same Layer-1 PIT scoring, same thesis-break
rules, same blind/zero-leakage AI sell-confirmation, same FCF override, hold-
count limit, and 25% position cap.

Run with: python paper_trading.py
"""
import os, sys, json, time
import concurrent.futures
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
import vix_ai_picker as _vix
import sp500_backtest as _bt

_vix.QUICK_MODE    = True
_vix.EDGAR_TIMEOUT = 5
_vix.NUM_WORKERS   = 10

_load_cik_map             = _vix._load_cik_map
get_cik                   = _vix.get_cik
get_xbrl_facts            = _vix.get_xbrl_facts
check_thesis_break        = _vix.check_thesis_break
_price_on_date            = _vix._price_on_date
_last_price_before        = _vix._last_price_before
_get_price_cache          = _vix._get_price_cache
_get_pit_metrics          = _vix._get_pit_metrics

get_sp500_for_year        = _bt.get_sp500_for_year
score_layer1_sp500        = _bt.score_layer1_sp500
_ai_sell_confirm          = _bt._ai_sell_confirm
_fcf_positive_and_growing = _bt._fcf_positive_and_growing
_san                      = _bt._san

# ── Constants ─────────────────────────────────────────────────────────────── #
MAX_POSITIONS   = _bt.SP500_MAX_POS         # 25
MAX_WEIGHT      = _bt.MAX_POSITION_WEIGHT   # 0.25
MIN_SCORE       = _bt.PIT_L1_PASS           # 65
STATE_FILE      = Path('data/paper_trading_state.json')
ALPACA_BASE_URL = 'https://paper-api.alpaca.markets'

AI_COST_TRACKER = [0.0]
AI_COST_CAP     = 2.0


def get_alpaca():
    return tradeapi.REST(
        key_id=os.environ.get('ALPACA_API_KEY'),
        secret_key=os.environ.get('ALPACA_SECRET_KEY'),
        base_url=ALPACA_BASE_URL,
    )


# ── State ─────────────────────────────────────────────────────────────────── #
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        'start_date':  str(date.today()),
        'start_value': 100_000.0,
        # ticker -> {buy_date, buy_year, buy_price, initial_metrics, ai_hold_count}
        'positions':   {},
        'history':     [],
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def market_is_open(api) -> bool:
    return bool(api.get_clock().is_open)


# ── Step 1: Thesis checks on existing Alpaca positions ───────────────────── #
def check_existing_positions(api, state: dict, year: int) -> tuple:
    """Mirrors the backtest's Step-1 thesis-check loop against live Alpaca
    positions. Returns (to_sell: list[ticker], report_lines: list[str])."""
    alpaca_positions = {p.symbol: p for p in api.list_positions()}
    to_sell      = []
    report_lines = []

    for ticker, ap in alpaca_positions.items():
        try:
            pos_state = state['positions'].get(ticker)
            if not pos_state:
                report_lines.append(f'  {ticker}: no buy-state on file - skipping thesis check')
                continue

            cik = get_cik(ticker)
            if not cik:
                continue
            gaap = get_xbrl_facts(cik)
            if not gaap:
                continue
            ph = _get_price_cache(ticker)

            broke, reason = check_thesis_break(year, pos_state['initial_metrics'], gaap, ph,
                                               ticker=ticker)
            if not broke:
                pos_state['ai_hold_count'] = 0
                pnl = float(ap.unrealized_plpc) * 100
                report_lines.append(f'  HOLD: {ticker} - thesis intact ({pnl:+.1f}%)')
                continue

            cutoff_fcf = f'{year}-01-01'
            if reason.startswith('ROE collapsed') and _fcf_positive_and_growing(gaap, cutoff_fcf):
                report_lines.append(f'  FCF OVERRIDE: keeping {ticker} - FCF positive+growing')
                continue

            cur_price  = float(ap.current_price)
            buy_price  = pos_state['buy_price']
            buy_year   = pos_state['buy_year']
            held_years = max(year - buy_year, 0)
            profitable = cur_price > buy_price
            hold_count = pos_state.get('ai_hold_count', 0)

            if hold_count >= 2:
                report_lines.append(f'  FORCED SELL: {ticker} - 2 prior HOLD overrides exhausted | {reason}')
                pos_state['ai_hold_count'] = 0
                to_sell.append(ticker)
                continue

            ai_decision = ai_conf = ai_rsn = None
            if profitable and AI_COST_TRACKER[0] < AI_COST_CAP:
                ai_decision, ai_conf, ai_rsn = _ai_sell_confirm(
                    ticker, year, reason, AI_COST_TRACKER,
                    buy_year, buy_price, cur_price, held_years, gaap, hold_count,
                )

            rsn_safe = _san(ai_rsn or '')
            if ai_decision == 'HOLD':
                pos_state['ai_hold_count'] = hold_count + 1
                report_lines.append(f'  HOLD: {ticker} - AI ({ai_conf}%) - {rsn_safe}')
            else:
                pos_state['ai_hold_count'] = 0
                if ai_decision == 'SELL':
                    tag = f'AI confirmed ({ai_conf}%) - {rsn_safe}'
                elif not profitable:
                    tag = 'losing position, no AI review'
                else:
                    tag = 'AI unavailable - rule-based sell'
                report_lines.append(f'  SELL: {ticker} - {reason} | {tag}')
                to_sell.append(ticker)

        except Exception as e:
            report_lines.append(f'  {ticker}: error during thesis check - {e}')
            continue

    return to_sell, report_lines


def execute_sells(api, tickers: list, state: dict):
    for ticker in tickers:
        try:
            position = api.get_position(ticker)
            shares   = abs(int(float(position.qty)))
            if shares < 1:
                state['positions'].pop(ticker, None)
                continue
            api.submit_order(symbol=ticker, qty=shares, side='sell', type='market',
                             time_in_force='day')
            print(f'  ORDER PLACED: SELL {shares} sh {ticker}')
            state['positions'].pop(ticker, None)
        except Exception as e:
            print(f'  ORDER FAILED: SELL {ticker} - {e}')


# ── Step 2: Score current S&P 500 universe (Layer 1, PIT) ────────────────── #
def score_universe(year: int, existing_tickers: set) -> list:
    print(f'  Universe: scoring S&P {year} constituents (slots may be limited)...')
    universe = get_sp500_for_year(year)
    print(f'  Universe size: {len(universe)} stocks')

    def _score_one(ticker):
        if ticker in existing_tickers:
            return None
        cik = get_cik(ticker)
        if not cik:
            return None
        gaap = get_xbrl_facts(cik)
        if not gaap:
            return None
        ph = _get_price_cache(ticker)
        score, _, _ = score_layer1_sp500(ticker, year, gaap, ph)
        if score >= MIN_SCORE:
            return {'ticker': ticker, 'score': score, 'gaap': gaap, 'ph': ph}
        return None

    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for result in ex.map(_score_one, universe):
            if result:
                candidates.append(result)

    candidates.sort(key=lambda c: c['score'], reverse=True)
    print(f'  Qualified (>={MIN_SCORE}): {len(candidates)} stocks')
    return candidates


# ── Step 3: Buy new positions to fill open slots ─────────────────────────── #
def execute_buys(api, candidates: list, n_held: int, state: dict, year: int):
    slots = MAX_POSITIONS - n_held

    account = api.get_account()
    cash    = float(account.cash)

    if slots <= 0 or cash <= 0:
        print('  No slots or cash available')
        return

    allocation = cash / slots
    cutoff     = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
    bought     = 0

    for cand in candidates:
        if bought >= slots:
            break
        ticker = cand['ticker']
        try:
            current_cash = float(api.get_account().cash)
            if current_cash < allocation * 0.9:
                print('  Insufficient cash - stopping')
                break

            quote = api.get_latest_trade(ticker)
            price = float(quote.price)
            if price <= 0:
                continue
            shares = int(allocation / price)
            if shares < 1:
                continue

            api.submit_order(symbol=ticker, qty=shares, side='buy',
                             type='market', time_in_force='day')
            cost = shares * price
            print(f'  BUY {ticker}: {shares} shares @ ${price:,.2f} = ${cost:,.0f}')

            init_m = _get_pit_metrics(cand['gaap'], cutoff, cand['ph'], ticker=ticker)
            state['positions'][ticker] = {
                'buy_date':        str(date.today()),
                'buy_year':        year,
                'buy_price':       price,
                'initial_metrics': init_m,
                'ai_hold_count':   0,
            }
            bought += 1
            time.sleep(0.3)   # rate limit
        except Exception as e:
            print(f'  ORDER FAILED: BUY {ticker} - {e}')
            continue

    if bought == 0:
        print('  No new positions opened this run.')


# ── Step 4: Enforce 25% position cap on the live account ─────────────────── #
def apply_live_position_cap(api):
    account       = api.get_account()
    portfolio_val = float(account.portfolio_value)
    if portfolio_val <= 0:
        return

    trimmed = False
    for p in api.list_positions():
        market_value = float(p.market_value)
        weight       = market_value / portfolio_val
        if weight <= MAX_WEIGHT:
            continue
        trimmed   = True
        px        = float(p.current_price)
        excess_val    = market_value - portfolio_val * MAX_WEIGHT
        excess_shares = int(excess_val / px)
        if excess_shares < 1:
            continue
        try:
            api.submit_order(symbol=p.symbol, qty=excess_shares, side='sell',
                             type='market', time_in_force='day')
            print(f'  [25% CAP] {p.symbol}: {weight:.0%} -> 25% | '
                  f'trimmed {excess_shares} sh @ ${px:,.2f}')
        except Exception as e:
            print(f'  CAP ORDER FAILED: {p.symbol} - {e}')

    if not trimmed:
        print('  No positions exceeded the 25% cap.')


# ── Output ────────────────────────────────────────────────────────────────── #
def print_portfolio_summary(api):
    account   = api.get_account()
    positions = api.list_positions()
    port_val  = float(account.portfolio_value)
    cash      = float(account.cash)

    print('\n=== PORTFOLIO SUMMARY ===')
    sep = '+--------+--------+----------+------------+--------+'
    print(sep)
    print('| Ticker | Shares |  Price   |   Value    | Weight |')
    print(sep)
    for p in sorted(positions, key=lambda x: float(x.market_value), reverse=True):
        shares = float(p.qty)
        price  = float(p.current_price)
        value  = float(p.market_value)
        weight = (value / port_val * 100) if port_val else 0.0
        print(f'| {p.symbol:<7}|{shares:>7.0f} | ${price:>7.2f} | ${value:>9,.0f} | {weight:>5.1f}% |')
    print(sep)
    print(f'| {"TOTAL":<7}|{"":>7} |{"":>9} | ${port_val:>9,.0f} |  100%  |')
    print(sep)
    print(f'\nCash available: ${cash:,.2f}')


def print_benchmarks(api, state):
    spy  = _get_price_cache('SPY')
    qqq  = _get_price_cache('QQQ')
    year = date.today().year
    jan1 = f'{year}-01-01'
    today_str = str(date.today())

    spy0 = _price_on_date(spy, jan1)
    spy1 = _price_on_date(spy, today_str) or _last_price_before(spy, today_str)
    qqq0 = _price_on_date(qqq, jan1)
    qqq1 = _price_on_date(qqq, today_str) or _last_price_before(qqq, today_str)

    spy_ret = (spy1 / spy0 - 1) * 100 if spy0 and spy1 else 0.0
    qqq_ret = (qqq1 / qqq0 - 1) * 100 if qqq0 and qqq1 else 0.0

    account   = api.get_account()
    port_val  = float(account.portfolio_value)
    start_val = state.get('start_value', 100_000.0)
    port_ret  = (port_val / start_val - 1) * 100 if start_val else 0.0

    print('\n=== BENCHMARKS ===')
    print(f'SPY today:  ${spy1:,.2f}  ({spy_ret:+.1f}% YTD)' if spy1 else 'SPY today:  N/A')
    print(f'QQQ today:  ${qqq1:,.2f}  ({qqq_ret:+.1f}% YTD)' if qqq1 else 'QQQ today:  N/A')
    print(f'Portfolio:  ${port_val:,.2f}  ({port_ret:+.1f}% since start {state.get("start_date","")})')
    print(f'\nNext run: January {year + 1} (annual rebalance)')
    print('Or run manually anytime: python paper_trading.py')


# ── Main ──────────────────────────────────────────────────────────────────── #
def run():
    api   = get_alpaca()
    state = load_state()
    year  = date.today().year

    print('=' * 60)
    print(f'  PAPER TRADING RUN -- {date.today()}')
    print('=' * 60)

    account   = api.get_account()
    positions = api.list_positions()
    print(f'Account Value:   ${float(account.portfolio_value):,.2f}')
    print(f'Cash Available:  ${float(account.cash):,.2f}')
    print(f'Positions:       {len(positions)}')

    if not market_is_open(api):
        print('\nMarket closed -- showing portfolio only')
        print_portfolio_summary(api)
        print_benchmarks(api, state)
        save_state(state)
        return

    _load_cik_map()

    print('\n=== THESIS CHECKS ===')
    to_sell, report_lines = check_existing_positions(api, state, year)
    if report_lines:
        for line in report_lines:
            print(line)
    else:
        print('  No existing positions to check.')

    if to_sell:
        print(f'\nExecuting {len(to_sell)} sell order(s)...')
        execute_sells(api, to_sell, state)
        time.sleep(2)

    existing_tickers = {p.symbol for p in api.list_positions()}
    candidates = score_universe(year, existing_tickers)

    print('\n=== NEW BUYS ===')
    execute_buys(api, candidates, len(existing_tickers), state, year)
    time.sleep(2)

    print('\n=== POSITION CAP ===')
    apply_live_position_cap(api)

    save_state(state)

    print_portfolio_summary(api)
    print_benchmarks(api, state)
    print(f'\nAI cost this run: ${AI_COST_TRACKER[0]:.4f} / ${AI_COST_CAP:.2f} cap')


if __name__ == '__main__':
    run()
