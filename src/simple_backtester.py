import pandas as pd
import numpy as np

def run_sma_backtest(file_path: str, ticker_name: str, short_window=10, long_window=30):
    """
    Executes a basic Simple Moving Average (SMA) crossover backtest on historical data
    and calculates structural trading metrics.
    """
    # Load data chronologically
    df = pd.read_csv(file_path, parse_dates=['Date'], index_col='Date')
    
    # 1. Calculate technical indicators (Moving Averages)
    df['SMA_Fast'] = df['Close'].rolling(window=short_window).mean()
    df['SMA_Slow'] = df['Close'].rolling(window=long_window).mean()
    
    # Drop rows without enough data points for the long moving average
    df.dropna(subset=['SMA_Slow'], inplace=True)
    
    # 2. Generate Trading Signals (1 = Buy/Hold, 0 = Cash/Out)
    df['Signal'] = np.where(df['SMA_Fast'] > df['SMA_Slow'], 1.0, 0.0)
    
    # Shift signals by 1 day to prevent "look-ahead bias" (trading on today's close instead of tomorrow's open)
    df['Position'] = df['Signal'].shift(1).fillna(0.0)
    
    # 3. Calculate Strategy Performance
    df['Market_Return'] = df['Close'].pct_change()
    df['Strategy_Return'] = df['Market_Return'] * df['Position']
    
    # Cumulative performance metrics
    df['Cum_Market_Return'] = (1 + df['Market_Return'].fillna(0)).cumprod() - 1
    df['Cum_Strategy_Return'] = (1 + df['Strategy_Return'].fillna(0)).cumprod() - 1
    
    # Extract final metrics
    final_market_perf = df['Cum_Market_Return'].iloc[-1] * 100
    final_strat_perf = df['Cum_Strategy_Return'].iloc[-1] * 100
    
    print(f"\n================📊 Backtest Result: {ticker_name.upper()} ================")
    print(f"📈 Buy & Hold (Benchmark) Return: {final_market_perf:.2f}%")
    print(f"🤖 SMA Crossover Strategy Return: {final_strat_perf:.2f}%")
    
    # Check if the strategy generated an "Alpha" (beat the asset's benchmark)
    alpha = final_strat_perf - final_market_perf
    if alpha > 0:
        print(f"✅ Success! Strategy beat the market by +{alpha:.2f}%")
    else:
        print(f"❌ Underperformed: Strategy lagged the market by {alpha:.2f}%")

if __name__ == "__main__":
    # Let's run our newly built backtesting framework on our high-volatility assets
    print("🚀 Running Backtesting Engine across historical targets...")
    run_sma_backtest("data/hut_history.csv", "hut")
    run_sma_backtest("data/bbai_history.csv", "bbai")
    run_sma_backtest("data/sol_usd_history.csv", "sol_usd")