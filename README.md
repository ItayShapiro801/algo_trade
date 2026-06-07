# Algorithmic Stock Picker — S&P 500 Fundamental Strategy

## Strategy Overview

Buy-and-hold fundamental stock picker based on:

- Warren Buffett-inspired quality criteria
- Point-in-time SEC EDGAR data (no look-ahead bias)
- AI-confirmed sell decisions (blind context, zero leakage)
- 25% position cap to prevent concentration risk
- FCF override to protect high-growth compounders

## Backtest Results (2010-2026, 17 years)

| Metric | Strategy | SPY B&H | QQQ B&H |
|---|---|---|---|
| CAGR | +20.8% | +13.6% | +18.3% |
| Total Return | +2,375% | +770% | +1,650% |
| Final Value | $2,475,330 | — | — |
| Years Beat SPY | 11/17 | — | — |
| Max Drawdown | -33.2% | -18.5% | — |
| Tax Saved | $686,035 | — | — |

## Architecture

- **Universe**: Wikipedia S&P 500 historical constituents
- **Fundamentals**: SEC EDGAR XBRL API (free, point-in-time)
- **AI Layer**: Claude Haiku (blind prompt, zero data leakage)
- **Broker**: Alpaca Markets API

## Files

- `sp500_backtest.py` — Historical backtest engine
- `vix_ai_picker.py` — Core scoring + AI confirmation
- `paper_trading.py` — Live paper-trading runner (Alpaca, identical strategy logic)
- `daily_scan.py` — Daily filing check + Telegram alert report
- `.github/workflows/daily_scan.yml` — Scheduled automation (GitHub Actions)

## Run Backtest

```
python sp500_backtest.py
```

## Live Paper Trading & Automation

`paper_trading.py` runs the exact same universe, scoring, thesis-break, FCF-override,
hold-count, blind-AI sell-confirmation, and 25% cap logic as the backtest — but against
a live Alpaca **paper** account. Portfolio state persists to `data/paper_trading_state.json`.

Required `.env` values:

```
ANTHROPIC_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
TELEGRAM_BOT_TOKEN=   # optional — get from @BotFather on Telegram
TELEGRAM_CHAT_ID=     # optional — get from @userinfobot on Telegram
```

Run it manually anytime (intended cadence: annual rebalance each January):

```
python paper_trading.py
```

`daily_scan.py` runs once a day (via the GitHub Actions workflow, or manually):
checks held positions for new 10-Q/10-K filings in the last 48 hours, re-runs the
thesis-break + AI sell-confirmation check on any that filed, and sends a portfolio
summary + alerts to Telegram (or prints to console if Telegram isn't configured).

```
python daily_scan.py
```

### GitHub Actions setup

1. Push this repo to a **private** GitHub repository (it references API keys via secrets).
2. In the repo: Settings → Secrets and variables → Actions → New repository secret.
   Add: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. The workflow at `.github/workflows/daily_scan.yml` runs `daily_scan.py`
   automatically every weekday at 08:00 Israel time, or on-demand via
   "Run workflow" in the Actions tab.
