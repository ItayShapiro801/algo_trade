# 🤖 Algorithmic Trading System - Phase 6 & 7

## Production Risk Management & Interactive Streamlit Dashboard

**Version:** 1.0.0  
**Status:** Production-Ready | Fully Typed | Fully Documented

---

## 📋 Overview

This is a **complete quantitative research and algorithmic trading system** implementing:

### Phase 6: Production Risk Management Module
- **Dynamic Position Sizing**: Mathematically optimal position calculation based on account equity and risk tolerance
- **Stop Loss & Take Profit**: Configurable SL/TP levels with trade horizon validation
- **Capital Preservation**: Capped position weights to prevent over-leveraging
- **Risk Metrics**: Comprehensive trade risk analysis with reward/risk ratios

### Phase 7: Interactive Streamlit Dashboard
- **Executive UI**: Clean, professional web interface for system control
- **Real-time Visualization**: Interactive performance curves with Plotly charts
- **Dynamic Controls**: Sliders and inputs for all system parameters
- **Multi-asset Analysis**: Simultaneous screening and backtesting of multiple assets
- **Risk Analytics**: Position sizing calculator and portfolio status monitoring

---

## 🏗️ Project Structure

```
algo_trade/
├── src/
│   ├── __init__.py
│   ├── backtest_engine.py          # Enhanced with risk management
│   ├── strategies.py                # SMA & RSI trading strategies
│   ├── data_loader.py              # Data ingestion from yfinance
│   ├── db_pipeline.py              # TimescaleDB integration
│   ├── market_screener.py          # Quantitative asset filtering
│   ├── metrics.py                  # Performance metrics (Sharpe, Sortino, etc.)
│   ├── optimizer.py                # Walk-forward optimization
│   ├── risk_manager.py             # NEW: Production risk management (Phase 6)
│   ├── research_engine.py
│   └── simple_backtester.py
├── data/                           # Historical OHLCV CSVs
│   ├── sol_usd_history.csv
│   ├── hut_history.csv
│   └── ...
├── strategies/                     # Custom strategy implementations
├── dashboard.py                    # NEW: Streamlit UI (Phase 7)
├── main.py                         # Headless execution pipeline
├── docker-compose.yml             # PostgreSQL + TimescaleDB
├── requirements.txt               # Python dependencies
├── .env                           # Environment configuration
└── README.md                      # This file
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 2. Start PostgreSQL Database

```bash
# Using Docker Compose
docker-compose up -d

# Or connect to existing PostgreSQL:
# Update DB_HOST, DB_USER, DB_PASSWORD in main.py / dashboard.py
```

### 3. Launch Interactive Dashboard

```bash
# Start Streamlit app
streamlit run dashboard.py

# Opens at http://localhost:8501
```

### 4. Run Headless Pipeline (Optional)

```bash
# Execute full analysis pipeline from command line
python main.py
```

---

## 📊 Dashboard Features

### Sidebar Controls
- 💰 **Capital Settings**: Initial capital, max risk per trade
- 📈 **Strategy Parameters**: SMA windows, RSI periods, oversold/overbought thresholds
- 🛡️ **Risk Management**: Stop loss %, take profit %, position sizing limits
- 📅 **Date Range**: Historical lookback period selector

### Main Tabs

#### 1️⃣ Market Screener
- Run quantitative market screening
- View qualified vs. disqualified assets with reasons
- Color-coded badges (green = qualified, red = disqualified)
- Filter criteria:
  - SMA_200 trend validation
  - Minimum liquidity ($100k daily volume)
  - Penny protection (min price $0.50)

#### 2️⃣ Backtesting
- Select assets to analyze
- Run SMA Crossover & RSI Mean Reversion strategies
- View comparison metrics grid:
  - **Return**: Cumulative return %
  - **Sharpe**: Risk-adjusted return
  - **Sortino**: Downside risk metric
  - **Max Drawdown**: Peak-to-trough decline
  - **Win Rate**: % of profitable trades
  - **Profit Factor**: Gross profit / gross loss ratio
- Highlight best performers per metric

#### 3️⃣ Performance Charts
- Interactive line charts with Plotly
- Portfolio value curves for each strategy
- Head-to-head strategy comparison
- Zoom, pan, hover for detailed inspection
- Responsive layout for multiple assets

#### 4️⃣ Risk Analysis
- **Position Sizing Calculator**:
  - Input entry & stop loss prices
  - Auto-calculate optimal position size
  - Display risk/reward ratio
  - Show max loss & gain in dollars and %
- **Portfolio Status**:
  - Total capital, available capital tracking
  - Capital utilization gauge
  - Risk per trade constraints

---

## 🔧 Risk Manager API

### Initialization

```python
from src.risk_manager import RiskManager

risk_mgr = RiskManager(
    total_capital=10000.0,
    max_risk_per_trade_pct=1.0,      # Risk 1% of portfolio max
    max_position_size_pct=10.0,      # Max 10% per position
    min_position_size=100.0          # Min $100 position
)
```

### Core Methods

#### 1. Calculate Position Size
```python
position_size = risk_mgr.calculate_position_size(
    entry_price=50.00,
    stop_loss_price=47.50,           # 5% below entry
    current_available_capital=10000.0
)
# Returns: 100 units (or less if constrained by capital)
```

**Formula:**
```
Risk Amount = Total Capital × Max Risk Per Trade %
Stop Loss Distance = Entry Price - Stop Loss Price
Position Size = Risk Amount / Stop Loss Distance
```

#### 2. Calculate SL/TP Prices
```python
sl_price, tp_price = risk_mgr.calculate_stop_loss_and_tp(
    entry_price=50.00,
    stop_loss_pct=5.0,               # 5% below
    take_profit_pct=20.0             # 20% above
)
# Returns: (47.50, 60.00)
```

#### 3. Complete Trade Risk Metrics
```python
metrics = risk_mgr.calculate_trade_risk_metrics(
    entry_price=50.00,
    stop_loss_pct=5.0,
    take_profit_pct=20.0,
    current_available_capital=10000.0
)

print(f"Position Size: {metrics.position_size:.2f} units")
print(f"Risk Amount: ${metrics.risk_amount:.2f}")
print(f"Reward Potential: ${metrics.reward_potential:.2f}")
print(f"Risk/Reward Ratio: {metrics.risk_reward_ratio:.2f}:1")
print(f"Max Loss: {metrics.max_loss_pct:.2f}%")
print(f"Max Gain: {metrics.max_gain_pct:.2f}%")
```

#### 4. Trade Horizon Validation
```python
is_valid, reason = risk_mgr.validate_trade_horizon(
    entry_price=50.00,
    current_price=48.00,
    stop_loss_pct=5.0,
    take_profit_pct=20.0
)
# Returns: (True, "Trade horizon valid")
# Or: (False, "Stop Loss hit: 48.00 <= 47.50")
```

#### 5. Apply Position Sizing to Backtest
```python
df_with_sizing = risk_mgr.apply_position_sizing_to_backtest(
    signals_df=backtest_df,
    entry_price_col='close',
    stop_loss_pct=5.0,
    initial_capital=10000.0,
    max_risk_per_trade_pct=1.0
)
# Adds 'position_size' and 'position_weight' columns
```

---

## 📈 Enhanced BacktestEngine

### Risk-Managed Backtesting
```python
from src.backtest_engine import BacktestEngine
from src.strategies import SMACrossoverStrategy

engine = BacktestEngine(db_url, initial_capital=10000.0)

strategy = SMACrossoverStrategy(short_window=10, long_window=30)

metrics, results_df = engine.run_backtest_with_risk_management(
    strategy=strategy,
    ticker="SOL-USD",
    start_date="2024-01-01",
    end_date="2026-05-27",
    stop_loss_pct=5.0,
    take_profit_pct=20.0,
    max_risk_per_trade_pct=1.0
)

print(f"Return: {metrics.cumulative_return:.2%}")
print(f"Sharpe: {metrics.sharpe_ratio:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown:.2%}")
```

**Improvements over basic backtesting:**
- ✅ Position sizes scale with stop loss distance
- ✅ Capital allocation respects risk limits
- ✅ Portfolio never goes to 100% leverage
- ✅ Realistic position weights prevent distortion

---

## 🎯 System Pipeline (Headless Mode)

The `main.py` executes a complete 7-phase pipeline:

1. **Market Screening**: Filter universe by quantitative criteria
2. **Data Ingestion**: Download historical OHLCV from yfinance
3. **Data Ingestion**: Load CSV → PostgreSQL + TimescaleDB
4. **Database Initialization**: Create tables and indices
5. **Backtesting**: Test SMA vs RSI on qualified assets
6. **Risk Management**: Run backtest with dynamic position sizing
7. **Optimization**: Walk-forward grid search for best parameters

```bash
python main.py

# Output:
# ==================================================
# 🤖 CORE QUANT RESEARCH SYSTEM - ADVANCED PIPELINE 🤖
# ==================================================
# 
# [PHASE 1] Running Market Screener...
# [PHASE 3] Dynamic Data Ingestion...
# [PHASE 4] Database Initialization & CSV → TimescaleDB...
# [PHASE 5] Advanced Backtesting Sweep...
# [PHASE 6] Production Risk Management...
# [PHASE 7] Strategy Optimization...
```

---

## 🛡️ Risk Management Best Practices

### Position Sizing Example

```
Portfolio: $10,000
Max Risk Per Trade: 1.0%
Max Risk Amount: $100

Entry: $50.00
Stop Loss: $47.50 (5% below)
SL Distance: $2.50

Position Size = $100 / $2.50 = 40 units
Position Cost = 40 × $50 = $2,000
Position Weight = 2,000 / 10,000 = 20%

Risk/Reward (with 20% TP):
- Max Loss: $100 (1% of capital)
- Max Gain: $500 (5% of capital)
- Ratio: 5:1 (favorable)
```

### Capital Preservation Rules

1. **Never risk more than 1-2% per trade**
   - Recovers from 50% drawdown in ~70 trades
   - Allows for statistical edge to work

2. **Cap position sizes at 5-10% of capital**
   - Prevents concentration risk
   - Maintains diversification

3. **Validate SL/TP on every entry**
   - Ensure risk/reward ≥ 2:1
   - Exit if horizon violated

---

## 📊 Key Metrics Explained

| Metric | Formula | Interpretation |
|--------|---------|-----------------|
| **Cumulative Return** | (1 + sum of returns) - 1 | Total profit/loss % |
| **Sharpe Ratio** | (excess return) / volatility × √252 | Risk-adjusted performance |
| **Sortino Ratio** | (excess return) / downside std × √252 | Penalizes only downside risk |
| **Max Drawdown** | Peak-to-trough decline | Worst historical loss |
| **Win Rate** | Winning trades / Total trades | % of profitable trades |
| **Profit Factor** | Gross wins / Gross losses | Revenue quality (>1.5 good) |

---

## 🔗 Integration & Deployment

### Option 1: Streamlit Cloud
```bash
# Push to GitHub, then:
# 1. Go to https://streamlit.io/cloud
# 2. Connect repo
# 3. Select dashboard.py as main file
# 4. Deploy (free tier available)
```

### Option 2: Docker Containerization
```bash
# Create Dockerfile
cat > Dockerfile << 'EOF'
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "dashboard.py", "--server.port=8501"]
EOF

# Build and run
docker build -t algo-dashboard .
docker run -p 8501:8501 algo-dashboard
```

### Option 3: Production Server
```bash
# Using Gunicorn + Streamlit
pip install gunicorn[gevent]
gunicorn --workers 4 --worker-class gevent --bind 0.0.0.0:8501 \
  "streamlit.cli:main" dashboard.py
```

---

## 🐛 Troubleshooting

### Issue: "No data found for ticker"
- ✅ Check ticker spelling (e.g., "SOL-USD" not "SOLANA")
- ✅ Verify date range (not older than ~5 years)
- ✅ Try different ticker: `"GORO"`, `"HUT"`, `"BBAI"`

### Issue: "psycopg2 connection refused"
- ✅ Ensure PostgreSQL running: `docker-compose ps`
- ✅ Check credentials in .env
- ✅ Or use mock data mode: remove DB URL from code

### Issue: "Streamlit not found"
- ✅ Install: `pip install streamlit plotly`
- ✅ Check virtual environment is activated

### Issue: Dashboard slow/unresponsive
- ✅ Reduce date range (fewer bars = faster compute)
- ✅ Test on smaller asset subset
- ✅ Enable caching: `@st.cache_data`

---

## 📝 Configuration Files

### .env (Example)
```bash
DB_USER=quant_user
DB_PASSWORD=quant_password123
DB_HOST=localhost
DB_PORT=5432
DB_NAME=quant_research

INITIAL_CAPITAL=10000
MAX_RISK_PCT=1.0
COMMISSION=0.001
```

### docker-compose.yml
```yaml
version: '3.8'
services:
  postgres:
    image: timescale/timescaledb-ha:latest-pg14
    environment:
      POSTGRES_USER: quant_user
      POSTGRES_PASSWORD: quant_password123
      POSTGRES_DB: quant_research
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

---

## 📚 Code Documentation Standards

All code follows these standards:
- ✅ **Type Hints**: Every function parameter and return type annotated
- ✅ **Docstrings**: Google-style docstrings for all classes and methods
- ✅ **Logging**: Structured logging at DEBUG, INFO, WARNING, ERROR levels
- ✅ **English Only**: All comments, docs, and strings in English
- ✅ **PEP 8**: Code formatted with Black (88 char lines)
- ✅ **Error Handling**: Explicit exception handling with logging

---

## 🎓 Learning Resources

### Quantitative Finance Concepts
- **Position Sizing**: Kelly Criterion, Fixed Fractional
- **Risk Management**: VaR, Expected Shortfall, Drawdown Analysis
- **Strategy Evaluation**: Walk-Forward Testing, Monte Carlo Analysis

### Technical References
- [Streamlit Documentation](https://docs.streamlit.io)
- [Plotly Interactive Charts](https://plotly.com/python/)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org)
- [TimescaleDB Time-Series](https://docs.timescale.com)

---

## 📄 License & Disclaimer

**⚠️ IMPORTANT:** This system is for **educational and research purposes only**.

- Past performance ≠ future results
- Always conduct thorough due diligence
- Test strategies extensively before live trading
- Never trade with money you can't afford to lose
- This is NOT financial advice

---

## 🤝 Support & Contribution

For issues, questions, or contributions:
1. Check the troubleshooting section
2. Review the code comments and docstrings
3. Test with sample data first
4. Enable debug logging: `logging.basicConfig(level=logging.DEBUG)`

---

## ✅ Checklist - What's Implemented

### Phase 6: Risk Management ✅
- [x] `RiskManager` class with production-grade code
- [x] Dynamic position sizing formula with constraints
- [x] Stop loss & take profit calculations
- [x] Trade horizon validation
- [x] Risk metrics dataclass with 7 metrics
- [x] Position sizing integrated into BacktestEngine
- [x] Run method for backtest with risk management
- [x] Portfolio status tracking
- [x] Comprehensive docstrings & logging

### Phase 7: Dashboard ✅
- [x] Streamlit app with professional UI
- [x] Sidebar controls for all parameters
- [x] Market screener results display with badges
- [x] Strategy metrics grid with 6 metrics
- [x] Performance curves with Plotly charts
- [x] Risk management calculator
- [x] Portfolio status gauge
- [x] Tab-based navigation
- [x] Session state management
- [x] Error handling & status messages

### Integration ✅
- [x] Risk manager imported in BacktestEngine
- [x] Dashboard imports all core modules
- [x] main.py updated with Phase 6 & 7
- [x] Headless pipeline works standalone
- [x] All files fully typed & documented
- [x] requirements.txt with dependencies

---

**🎉 System is production-ready and fully operational!**
