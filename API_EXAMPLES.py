"""
API Reference & Usage Examples

Complete guide to using the Algorithmic Trading System programmatically.
Includes RiskManager, BacktestEngine, and Dashboard integration.

Version: 1.0.0
Author: Quantitative Development Team
"""

# ============================================================================
# EXAMPLE 1: Basic Risk Manager Usage
# ============================================================================

from src.risk_manager import RiskManager

# Initialize risk manager with portfolio parameters
risk_mgr = RiskManager(
    total_capital=10000.0,              # Starting portfolio value
    max_risk_per_trade_pct=1.0,         # Risk max 1% per trade
    max_position_size_pct=10.0,         # Cap position at 10%
    min_position_size=100.0             # Minimum $100 per trade
)

# Example trade: Buy SOL-USD
entry_price = 150.00
stop_loss_price = 142.50  # 5% below entry

# Calculate position size
position_size = risk_mgr.calculate_position_size(
    entry_price=entry_price,
    stop_loss_price=stop_loss_price,
    current_available_capital=10000.0
)
print(f"Optimal position size: {position_size:.2f} units")
# Output: 66.67 units (= $10,000 / ($150 - $142.50))

# Calculate SL/TP levels
sl_price, tp_price = risk_mgr.calculate_stop_loss_and_tp(
    entry_price=entry_price,
    stop_loss_pct=5.0,      # 5% below entry
    take_profit_pct=15.0    # 15% above entry
)
print(f"Stop Loss: ${sl_price:.2f}, Take Profit: ${tp_price:.2f}")
# Output: Stop Loss: $142.50, Take Profit: $172.50

# Get complete risk metrics
metrics = risk_mgr.calculate_trade_risk_metrics(
    entry_price=entry_price,
    stop_loss_pct=5.0,
    take_profit_pct=15.0,
    current_available_capital=10000.0
)

print(f"Position Size: {metrics.position_size:.2f} units")
print(f"Risk Amount: ${metrics.risk_amount:.2f}")
print(f"Reward Potential: ${metrics.reward_potential:.2f}")
print(f"Risk/Reward Ratio: {metrics.risk_reward_ratio:.2f}:1")
print(f"Max Loss %: {metrics.max_loss_pct:.2f}%")
print(f"Max Gain %: {metrics.max_gain_pct:.2f}%")

# Output:
# Position Size: 66.67 units
# Risk Amount: $500.00
# Reward Potential: $1,500.00
# Risk/Reward Ratio: 3.00:1
# Max Loss %: 5.00%
# Max Gain %: 15.00%


# ============================================================================
# EXAMPLE 2: Trade Horizon Validation
# ============================================================================

# Check if current price triggers SL or TP
is_valid, reason = risk_mgr.validate_trade_horizon(
    entry_price=150.00,
    current_price=148.00,    # Current market price
    stop_loss_pct=5.0,
    take_profit_pct=15.0
)

print(f"Trade Valid: {is_valid}, Reason: {reason}")
# Output: Trade Valid: True, Reason: Trade horizon valid

# When price hits stop loss
is_valid, reason = risk_mgr.validate_trade_horizon(
    entry_price=150.00,
    current_price=142.00,    # Below stop loss!
    stop_loss_pct=5.0,
    take_profit_pct=15.0
)

print(f"Trade Valid: {is_valid}, Reason: {reason}")
# Output: Trade Valid: False, Reason: Stop Loss hit: 142.00 <= 142.50


# ============================================================================
# EXAMPLE 3: Backtesting with Risk Management
# ============================================================================

from src.backtest_engine import BacktestEngine
from src.strategies import SMACrossoverStrategy
import pandas as pd

# Initialize backtest engine with risk-managed mode
engine = BacktestEngine(
    db_url="postgresql://quant_user:quant_password123@localhost:5432/quant_research",
    initial_capital=10000.0,
    commission=0.001,
    slippage=0.0005
)

# Create strategy
strategy = SMACrossoverStrategy(
    short_window=10,
    long_window=30
)

# Run risk-managed backtest
metrics, results_df = engine.run_backtest_with_risk_management(
    strategy=strategy,
    ticker="SOL-USD",
    start_date="2024-01-01",
    end_date="2026-05-27",
    stop_loss_pct=5.0,           # 5% stop loss
    take_profit_pct=20.0,        # 20% take profit
    max_risk_per_trade_pct=1.0,  # Risk 1% per trade
    use_csv=None
)

# Access metrics
print(f"Cumulative Return: {metrics.cumulative_return:.2%}")
print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
print(f"Sortino Ratio: {metrics.sortino_ratio:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown:.2%}")
print(f"Win Rate: {metrics.win_rate:.1%}")
print(f"Profit Factor: {metrics.profit_factor:.2f}")

# results_df columns:
# - close: Price
# - signal: Strategy signal (1 = long, 0 = no position)
# - position: Position size (with risk management)
# - position_weight: Position weight % of portfolio
# - market_return: Market return
# - strategy_return: Strategy return
# - adjusted_return: Return after costs
# - portfolio_value: Cumulative portfolio value


# ============================================================================
# EXAMPLE 4: Multiple Strategy Comparison
# ============================================================================

from src.strategies import RSIMeanReversionStrategy

# Test multiple strategies on same asset
strategies = [
    SMACrossoverStrategy(short_window=10, long_window=30),
    RSIMeanReversionStrategy(period=14, oversold=30, overbought=70)
]

results = {}

for strategy in strategies:
    metrics, df = engine.run_backtest_with_risk_management(
        strategy=strategy,
        ticker="HUT",
        start_date="2024-01-01",
        end_date="2026-05-27",
        stop_loss_pct=5.0,
        take_profit_pct=20.0,
        max_risk_per_trade_pct=1.0
    )
    
    results[strategy.name] = {
        'metrics': metrics,
        'dataframe': df
    }

# Compare
print("\nStrategy Comparison:")
for name, data in results.items():
    m = data['metrics']
    print(f"\n{name}:")
    print(f"  Return: {m.cumulative_return:.2%}")
    print(f"  Sharpe: {m.sharpe_ratio:.2f}")
    print(f"  Max DD: {m.max_drawdown:.2%}")
    print(f"  Win Rate: {m.win_rate:.1%}")


# ============================================================================
# EXAMPLE 5: Position Sizing in Backtests
# ============================================================================

# Access position sizing details from backtest results
df = results_df

print(f"Total bars: {len(df)}")
print(f"\nPosition Sizing Summary:")
print(f"  Max Position: {df['position_size'].max():.2f} units")
print(f"  Max Weight: {df['position_weight'].max():.1%}")
print(f"  Avg Weight: {df[df['position_weight'] > 0]['position_weight'].mean():.1%}")

# Plot position sizes over time
import matplotlib.pyplot as plt

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

# Portfolio value
ax1.plot(df.index, df['portfolio_value'], linewidth=2, color='blue')
ax1.set_title('Portfolio Value Over Time')
ax1.set_ylabel('Portfolio Value ($)')
ax1.grid(True, alpha=0.3)

# Position sizes
ax2.bar(df.index, df['position_size'], alpha=0.6, color='green')
ax2.set_title('Position Sizes Over Time')
ax2.set_ylabel('Position Size (units)')
ax2.set_xlabel('Date')
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.show()


# ============================================================================
# EXAMPLE 6: Market Screener Integration
# ============================================================================

from src.market_screener import MarketScreener

# Run market screening
screener = MarketScreener(tracking_list=[
    "SOL-USD", "HUT", "BBAI", "CLSK", "GORO"
])

qualified, disqualified = screener.screen()

print(f"Qualified Assets ({len(qualified)}):")
for ticker in qualified:
    print(f"  ✅ {ticker}")

print(f"\nDisqualified Assets ({len(disqualified)}):")
for ticker, reason in disqualified.items():
    print(f"  ❌ {ticker}: {reason}")

# Backtest only qualified assets
for asset in qualified:
    metrics, df = engine.run_backtest_with_risk_management(
        strategy=SMACrossoverStrategy(),
        ticker=asset,
        start_date="2024-01-01",
        end_date="2026-05-27",
        stop_loss_pct=5.0,
        take_profit_pct=20.0
    )
    print(f"{asset}: {metrics.cumulative_return:.2%}")


# ============================================================================
# EXAMPLE 7: Portfolio Status Tracking
# ============================================================================

# Track portfolio capital allocation
status = risk_mgr.get_portfolio_status()

print("Portfolio Status:")
print(f"  Total Capital: ${status['total_capital']:.2f}")
print(f"  Available Capital: ${status['available_capital']:.2f}")
print(f"  Utilized Capital: ${status['total_capital'] - status['available_capital']:.2f}")
print(f"  Utilization %: {status['utilization_pct']:.1f}%")
print(f"  Max Risk Per Trade: {status['max_risk_per_trade_pct']}%")
print(f"  Max Position Size: {status['max_position_size_pct']}%")

# Simulate position entry
capital_after_entry = 10000.0 - (66.67 * 150.00)  # Reduce by position cost
risk_mgr.reset_capital(capital_after_entry)

status = risk_mgr.get_portfolio_status()
print(f"\nAfter entry:")
print(f"  Available Capital: ${status['available_capital']:.2f}")
print(f"  Utilization %: {status['utilization_pct']:.1f}%")


# ============================================================================
# EXAMPLE 8: Custom Risk Calculations
# ============================================================================

def analyze_trade_setup(
    entry: float,
    stop_loss: float,
    take_profit: float,
    portfolio_size: float,
    max_risk_pct: float = 1.0
) -> dict:
    """
    Comprehensive trade setup analysis.
    
    Args:
        entry: Entry price
        stop_loss: Stop loss price
        take_profit: Take profit price
        portfolio_size: Total portfolio capital
        max_risk_pct: Maximum risk as % of portfolio
    
    Returns:
        Dictionary with complete trade analysis
    """
    risk_mgr = RiskManager(
        total_capital=portfolio_size,
        max_risk_per_trade_pct=max_risk_pct
    )
    
    stop_loss_pct = ((entry - stop_loss) / entry) * 100
    take_profit_pct = ((take_profit - entry) / entry) * 100
    
    metrics = risk_mgr.calculate_trade_risk_metrics(
        entry_price=entry,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        current_available_capital=portfolio_size
    )
    
    return {
        'entry_price': entry,
        'stop_loss_price': stop_loss,
        'take_profit_price': take_profit,
        'stop_loss_pct': stop_loss_pct,
        'take_profit_pct': take_profit_pct,
        'position_size': metrics.position_size,
        'risk_amount': metrics.risk_amount,
        'reward_amount': metrics.reward_potential,
        'risk_reward_ratio': metrics.risk_reward_ratio,
        'max_loss_pct': metrics.max_loss_pct,
        'max_gain_pct': metrics.max_gain_pct,
        'kelly_fraction': (metrics.risk_reward_ratio / (1 + metrics.risk_reward_ratio))
    }

# Use the analyzer
analysis = analyze_trade_setup(
    entry=100.00,
    stop_loss=95.00,
    take_profit=120.00,
    portfolio_size=10000.0,
    max_risk_pct=1.0
)

for key, value in analysis.items():
    if isinstance(value, float):
        if 'pct' in key:
            print(f"{key}: {value:.2f}%")
        elif 'ratio' in key:
            print(f"{key}: {value:.2f}:1")
        else:
            print(f"{key}: {value:.2f}")
    else:
        print(f"{key}: {value}")


# ============================================================================
# EXAMPLE 9: Dashboard Integration (Streamlit App)
# ============================================================================

"""
The dashboard.py file provides a complete UI for the system.
It uses Streamlit to create an interactive web application.

Key features:
- Sidebar controls for all parameters
- Market screener results with badges
- Strategy metrics grid with 6 performance metrics
- Interactive performance curves with Plotly
- Risk management calculator
- Portfolio status gauge

To run the dashboard:
    streamlit run dashboard.py

The dashboard will open at http://localhost:8501

Main tabs:
1. Market Screener - View qualified vs disqualified assets
2. Backtesting - Run and compare strategies
3. Performance - Interactive charts and curves
4. Risk Analysis - Position sizing and portfolio status
"""


# ============================================================================
# EXAMPLE 10: Headless Pipeline (Full System)
# ============================================================================

"""
The main.py file implements a complete 7-phase pipeline:

1. Market Screening - Filter universe by quantitative criteria
2. Data Ingestion - Download historical OHLCV from yfinance
3. Database Prep - Load CSV files to PostgreSQL
4. DB Initialization - Create tables and schema
5. Backtesting - Test strategies on qualified assets
6. Risk Management - Run backtests with position sizing
7. Optimization - Walk-forward grid search

To run the full pipeline:
    python main.py

This executes the entire analysis without the UI.
Useful for batch processing and scheduled runs.
"""

# ============================================================================
# END OF EXAMPLES
# ============================================================================
