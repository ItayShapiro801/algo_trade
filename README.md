# Algorithmic S&P 500 Stock Picker

**A production-grade quantitative trading system with 17-year backtested edge, live paper trading, and fully automated daily monitoring**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)
![Alpaca](https://img.shields.io/badge/Alpaca-Markets-yellow?logo=alpaca&logoColor=white)
![Anthropic](https://img.shields.io/badge/Anthropic-Claude-orange?logo=anthropic&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Automated-2088FF?logo=github-actions&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Performance Results (Backtest 2010–2026)

| Metric | Strategy | SPY Buy & Hold | QQQ Buy & Hold |
|---|---|---|---|
| CAGR | **+20.8%** | +13.6% | +18.3% |
| Total Return | **+2,375%** | +770% | +1,650% |
| Final Value ($100K start) | **$2,475,330** | — | — |
| Max Drawdown | -33.2% | -18.5% | — |
| Years Beating SPY | **11 / 17** | — | — |
| Tax Saved vs Annual Rebalance | **$686,035** | — | — |
| AI Cost (entire 17yr backtest) | **$0.033** | — | — |

> Backtest runs from January 2010 through January 2026 using point-in-time SEC EDGAR data. No look-ahead bias. No survivorship bias.

---

## System Architecture

```
vix_ai_picker.py  (Core Engine)
├── sp500_backtest.py    → Historical simulation (2010–2026)
├── paper_trading.py     → Live Alpaca paper trading
└── daily_scan.py        → GitHub Actions automation
```

---

## How It Works

### Stock Selection — Layer 1 Scoring (0–110 pts)

Every candidate in the historical S&P 500 universe is scored across five fundamental dimensions. Only stocks scoring **≥ 65 / 110** are eligible for purchase.

| Dimension | What It Measures |
|---|---|
| **D1 Quality** | ROE consistency across 3 years, net profit margin |
| **D2 Fortress** | Debt-to-equity ratio, free cash flow positivity |
| **D3 Growth** | 3-year revenue CAGR, 3-year EPS CAGR |
| **D4 Valuation** | P/E ratio, PEG ratio, FCF yield (with stock-split EPS correction) |
| **D5 Momentum** | 6-month and 12-month price performance relative to SPY |

Scores are additive and dimension-weighted. A high-quality business trading at a reasonable valuation with positive momentum is the target. The threshold of 65 eliminates roughly 80–90% of the universe at any given rebalance date.

---

### Point-in-Time Data — Zero Look-Ahead Bias

Look-ahead bias is the most common — and most damaging — flaw in amateur backtests. This system is built from the ground up to prevent it:

- **Fundamentals source**: SEC EDGAR XBRL API (free, no vendor dependency). All financial data is pulled from actual 10-Q and 10-K filings.
- **45-day publication lag**: Every fundamental data point is date-stamped to its filing date, then offset by a 45-day buffer. The backtest engine never uses a number that wouldn't have been publicly available on the simulated trade date.
- **Historical S&P 500 constituents**: The universe for each simulated year is reconstructed from Wikipedia's historical constituent tables — not today's index. This prevents survivorship bias (i.e., the system doesn't magically know which companies survived to 2026).
- **Stock-split EPS correction**: EPS figures from EDGAR are adjusted for all subsequent stock splits before any scoring or valuation calculation, ensuring consistent comparisons across time.

---

### AI-Confirmed Sells — Zero Data Leakage

The AI layer is deliberately blind. It is invoked only after a thesis-break rule fires, and it is never told what company it is analyzing.

**What the AI receives (anonymized):**
- Sector label (e.g., "Technology")
- CapEx trend over 3 years
- 3-year revenue trend, ROE trend, FCF trend
- Macro context at the simulated date
- Anonymized 8-K summary (any company names scrubbed)

**What the AI does not receive:**
- Ticker symbol
- Company name
- Any identifying text

A leakage validator post-processes every AI response and scrubs any company names before the decision is recorded. The AI is testing pure financial reasoning — not pattern-matching on its knowledge of well-known companies.

Each position may receive a maximum of **2 HOLD overrides** before a forced sell is triggered regardless of the AI's recommendation. This prevents the system from deferring indefinitely on deteriorating positions.

**Total AI cost across all 39 decisions in the 17-year backtest: $0.033.**

---

### Sell Rules — Thesis-Break Logic

A sell review is triggered when **any one** of these conditions is met:

1. **ROE Collapse** — ROE drops below 10% after being ≥ 15% at purchase
2. **Leverage Surge** — Debt-to-equity ratio doubled AND now exceeds 1.0×
3. **Revenue Decline** — Revenue declined two consecutive years
4. **Earnings Collapse** — EPS was negative for two consecutive years
5. **Extreme Valuation** — P/E > 60 AND PEG > 5 simultaneously

**FCF Override:** If a position triggers the ROE-collapse rule but its free cash flow is both positive and growing year-over-year, the sell is automatically vetoed. This protects high-quality compounders (e.g., heavy-R&D tech companies) that temporarily suppress accounting earnings while generating strong cash.

---

## Live Paper Trading

The paper trading module runs the identical logic as the backtest — same universe construction, same scoring, same thesis-break checks, same AI confirmation, same FCF override — against a live Alpaca paper account. There is no reimplementation or simplified version.

- **25% position cap** enforced on every run (without it, NVDA reached 46% of the portfolio in 2024)
- **Portfolio state** persists to `data/paper_trading_state.json` across runs
- **Daily automated scan** via GitHub Actions checks for new 10-Q / 10-K filings on held positions and fires thesis-break checks
- **Telegram alerts** notify on thesis breaks, AI sell decisions, and send a daily portfolio summary

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.9+ |
| Fundamentals Data | SEC EDGAR XBRL API (free) |
| Price Data | yfinance |
| Universe | Wikipedia S&P 500 historical constituents |
| AI Layer | Anthropic Claude Haiku |
| Broker | Alpaca Markets (paper trading) |
| Automation | GitHub Actions (cron) |
| Alerts | Telegram Bot API |

---

## Project Structure

```
algo_trade/
├── vix_ai_picker.py      # Core engine: SEC EDGAR, scoring, AI sells
├── sp500_backtest.py     # 17-year historical backtest
├── paper_trading.py      # Live Alpaca paper trading
├── daily_scan.py         # Automated daily monitoring
├── check_portfolio.py    # Portfolio scoring utility
└── .github/
    └── workflows/
        └── daily_scan.yml  # GitHub Actions automation
```

---

## Setup

```bash
git clone https://github.com/ItayShapiro801/algo_trade
cd algo_trade
pip install -r requirements.txt
```

Add a `.env` file with the following:

```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ANTHROPIC_API_KEY=your_key
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

**Run the 17-year backtest:**

```bash
python sp500_backtest.py
```

**Run paper trading (market hours only):**

```bash
python paper_trading.py
```

**Run daily monitoring manually:**

```bash
python daily_scan.py
```

**GitHub Actions setup:** Push the repo to GitHub, add the five secrets above under Settings → Secrets → Actions, and the workflow in `.github/workflows/daily_scan.yml` will run automatically every weekday morning.

---

## Key Design Decisions

**Why point-in-time data matters.** Look-ahead bias is the #1 mistake in amateur backtests. Using today's financial data to simulate trades made in 2012 is not a backtest — it is hindsight. Every fundamental in this system is pulled from timestamped EDGAR filings and gated behind a 45-day lag before it can influence a simulated trade. The survivorship bias problem is handled separately by reconstructing the historical S&P 500 universe year-by-year rather than using the current index composition.

**Why the AI prompt is blind.** The goal of the AI layer is to evaluate financial reasoning, not to exploit the model's prior knowledge of famous companies. If the prompt said "evaluate Apple's sell thesis," the model's response would be colored by everything it knows about Apple from training data — earnings calls, analyst opinions, product launches. A blind prompt strips all of that away and forces the model to reason purely from the anonymized financial trends provided, which is exactly the signal the system is trying to capture.

**Why the 25% position cap exists.** In a simulation run without position limits, NVIDIA grew to represent 46% of the portfolio by mid-2024. That is not stock-picking skill — that is concentration risk masquerading as alpha. The 25% cap ensures that no single position can dominate returns or drawdowns, keeping the portfolio's risk profile consistent with the strategy's intent and defensible as a replicable system rather than a lucky bet.

---

## Disclaimer

This project is for educational and research purposes only. It is not financial advice. All trading shown here is paper trading — no real money is involved. Past backtest performance does not guarantee future results. Use at your own risk.
