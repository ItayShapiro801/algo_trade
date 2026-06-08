"""
daily_scan.py
Runs once per day via GitHub Actions:
  1. Checks whether any held position published a new 10-Q/10-K in the last 48h
  2. If so, re-runs the thesis-break + blind AI sell-confirmation check immediately
  3. Sends a Telegram summary report regardless

Run with: python daily_scan.py
"""
import os, sys, json, requests
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
import vix_ai_picker as _vix
import sp500_backtest as _bt

_vix.QUICK_MODE    = True
_vix.EDGAR_TIMEOUT = 5

_load_cik_map             = _vix._load_cik_map
get_cik                   = _vix.get_cik
get_xbrl_facts            = _vix.get_xbrl_facts
check_thesis_break        = _vix.check_thesis_break
_get_price_cache          = _vix._get_price_cache
_price_on_date            = _vix._price_on_date
_last_price_before        = _vix._last_price_before
_fcf_positive_and_growing = _bt._fcf_positive_and_growing
_ai_sell_confirm          = _bt._ai_sell_confirm
_san                      = _bt._san

STATE_FILE = Path('paper_trading_state.json')
AI_TRACKER = [0.0]


# ── Telegram ──────────────────────────────────────────────────────────────── #
def send_telegram(message: str):
    token   = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        print('Telegram not configured -- printing report only:\n')
        print(message)
        return
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        requests.post(url, data={'chat_id': chat_id, 'text': message,
                                 'parse_mode': 'HTML'}, timeout=10)
        print('Telegram message sent.')
    except Exception as e:
        print(f'Telegram send failed: {e}')


# ── New-filing check (last 48h) ───────────────────────────────────────────── #
def check_new_filings(ticker: str, cik) -> bool:
    """Returns True if a 10-Q/10-K was filed for this CIK in the last 48 hours."""
    try:
        cik_padded = str(cik).zfill(10)
        url = f'https://data.sec.gov/submissions/CIK{cik_padded}.json'
        r = requests.get(url, headers={'User-Agent': 'algo-trade research@example.com'},
                         timeout=10)
        if r.status_code != 200:
            return False
        data  = r.json()
        forms = data['filings']['recent']['form']
        dates = data['filings']['recent']['filingDate']
        cutoff = str(date.today() - timedelta(days=2))
        for form, dt in zip(forms, dates):
            if dt >= cutoff and form in ('10-Q', '10-K'):
                print(f'  {ticker}: NEW {form} filed {dt}')
                return True
        return False
    except Exception:
        return False


# ── Alpaca portfolio snapshot ─────────────────────────────────────────────── #
def get_alpaca_portfolio() -> dict:
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            key_id=os.environ.get('ALPACA_API_KEY'),
            secret_key=os.environ.get('ALPACA_SECRET_KEY'),
            base_url='https://paper-api.alpaca.markets',
        )
        account   = api.get_account()
        positions = api.list_positions()
        port_val  = float(account.portfolio_value)
        holdings  = [{
            'ticker':  p.symbol,
            'shares':  float(p.qty),
            'price':   float(p.current_price),
            'value':   float(p.market_value),
            'weight':  (float(p.market_value) / port_val * 100) if port_val else 0.0,
            'pnl_pct': float(p.unrealized_plpc) * 100,
        } for p in sorted(positions, key=lambda x: float(x.market_value), reverse=True)]
        return {'portfolio_value': port_val, 'cash': float(account.cash), 'holdings': holdings}
    except Exception as e:
        return {'error': str(e)}


# ── Telegram message formatting ───────────────────────────────────────────── #
def format_daily_message(portfolio: dict, alerts: list, state: dict) -> str:
    today     = date.today().strftime('%d/%m/%Y')
    start_val = state.get('start_value', 100_000)
    start_dt  = state.get('start_date', str(date.today()))

    if 'error' in portfolio:
        return f'Daily Report - {today}\nError fetching Alpaca portfolio: {portfolio["error"]}'

    port_val  = portfolio['portfolio_value']
    cash      = portfolio['cash']
    total_ret = (port_val / start_val - 1) * 100 if start_val else 0.0
    holdings  = portfolio['holdings']

    msg  = f'<b>Daily Portfolio Report</b>\n{today}\n\n'
    msg += (f'<b>Account Value:</b> ${port_val:,.0f}\n'
            f'<b>Total Return:</b> {total_ret:+.1f}%\n'
            f'<b>Cash:</b> ${cash:,.0f}\n'
            f'<b>Since:</b> {start_dt}\n\n')

    if alerts:
        msg += '<b>ALERTS</b>\n'
        for a in alerts:
            msg += f'- {a}\n'
        msg += '\n'
    else:
        msg += 'No alerts today.\n\n'

    msg += f'<b>Holdings ({len(holdings)} positions)</b>\n'
    msg += '-' * 24 + '\n'
    for h in holdings[:15]:
        msg += f"{h['ticker']}  {h['weight']:.1f}%  |  ${h['value']:,.0f}  |  {h['pnl_pct']:+.1f}%\n"
    if len(holdings) > 15:
        msg += f'... +{len(holdings) - 15} more positions\n'
    msg += '-' * 24 + '\n'
    msg += f'AI cost today: ${AI_TRACKER[0]:.4f}'
    return msg


# ── Main ──────────────────────────────────────────────────────────────────── #
def run_daily_scan():
    print('=' * 50)
    print(f'DAILY SCAN -- {date.today()}')
    print('=' * 50)

    _load_cik_map()

    if not STATE_FILE.exists():
        print('No state file - run paper_trading.py first')
        send_telegram('No portfolio state found. Run paper_trading.py first.')
        return

    state    = json.loads(STATE_FILE.read_text())
    holdings = state.get('positions', {})
    alerts   = []
    year     = date.today().year
    today_str = str(date.today())

    print(f'\nChecking {len(holdings)} holdings for new filings...')
    for ticker, buy_info in holdings.items():
        try:
            cik = get_cik(ticker)
            if not cik or not check_new_filings(ticker, cik):
                continue

            gaap = get_xbrl_facts(cik)
            if not gaap:
                continue
            ph = _get_price_cache(ticker)
            initial_metrics = buy_info.get('initial_metrics', {})
            if not initial_metrics:
                continue

            broke, reason = check_thesis_break(year, initial_metrics, gaap, ph)
            if not broke:
                alerts.append(f'New filing {ticker} - thesis intact')
                continue

            cutoff_fcf = f'{year}-01-01'
            if reason.startswith('ROE collapsed') and _fcf_positive_and_growing(gaap, cutoff_fcf):
                alerts.append(f'New filing {ticker} - FCF override keeps position (FCF positive+growing)')
                continue

            buy_price  = buy_info.get('buy_price', 0.0)
            buy_year   = buy_info.get('buy_year', year)
            held_years = max(year - buy_year, 0)
            hold_count = buy_info.get('ai_hold_count', 0)
            cur_price  = _price_on_date(ph, today_str) or _last_price_before(ph, today_str)

            decision = conf = ai_reason = None
            if cur_price and cur_price > buy_price:
                decision, conf, ai_reason = _ai_sell_confirm(
                    ticker, year, reason, AI_TRACKER,
                    buy_year, buy_price, cur_price, held_years, gaap, hold_count,
                )

            rsn_safe = _san(ai_reason or '')
            if decision == 'SELL':
                alerts.append(f'SELL SIGNAL: {ticker} - {reason} | AI ({conf}%): {rsn_safe}')
            elif decision == 'HOLD':
                alerts.append(f'New filing {ticker} - thesis broke ({reason}) but AI ({conf}%) says HOLD: {rsn_safe}')
            else:
                alerts.append(f'SELL SIGNAL: {ticker} - {reason} (rule-based, no AI review)')
        except Exception as e:
            print(f'  {ticker}: error - {e}')
            continue

    print('\nFetching portfolio from Alpaca...')
    portfolio = get_alpaca_portfolio()

    message = format_daily_message(portfolio, alerts, state)
    print('\nSending Telegram report...')
    send_telegram(message)

    print('\nDaily scan complete.')
    print(f'Alerts: {len(alerts)}')
    print(f'AI cost: ${AI_TRACKER[0]:.4f}')


if __name__ == '__main__':
    run_daily_scan()
