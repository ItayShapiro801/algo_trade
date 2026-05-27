import pandas as pd
import numpy as np
import os

def analyze_asset(file_path: str, ticker_name: str):
    """
    Loads historical CSV data, calculates baseline quantitative metrics (Returns & Volatility),
    and prints a clear analytical summary.
    """
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return

    # Load data and ensure Date is treated as the chronological index
    df = pd.read_csv(file_path, parse_dates=['Date'], index_col='Date')
    
    # 1. Calculate Daily Returns (Percentage change from previous close)
    df['Daily_Return'] = df['Close'].pct_change()
    
    # 2. Calculate Cumulative Returns (The growth factor of $1 invested)
    df['Cumulative_Return'] = (1 + df['Daily_Return']).cumprod() - 1
    
    # 3. Calculate Volatility (Standard deviation of daily returns annualized)
    # 252 is the standard number of trading days in a year
    daily_vol = df['Daily_Return'].std()
    annualized_vol = daily_vol * np.sqrt(252)

    # Gather final metrics
    total_days = len(df)
    total_return_pct = df['Cumulative_Return'].iloc[-1] * 100
    
    print(f"\n================📊 Quant Report: {ticker_name.upper()} ================")
    print(f"📅 Total Trading Days Sampled: {total_days}")
    print(f"📈 Total Performance Return: {total_return_pct:.2f}%")
    print(f"📉 Annualized Volatility (Risk): {annualized_vol * 100:.2f}%")
    print(f"ℹ️ First Recorded Close: ${df['Close'].iloc[0]:.2f} | Last Close: ${df['Close'].iloc[-1]:.2f}")

if __name__ == "__main__":
    # Let's run a comparative risk/return sweep across our fresh Small-Cap & Crypto data
    assets_to_analyze = {
        "sol_usd": "data/sol_usd_history.csv",
        "hut": "data/hut_history.csv",
        "bbai": "data/bbai_history.csv",
        "clsk": "data/clsk_history.csv"
    }
    
    print("🚀 Initializing Mathematical Research Engine...")
    for ticker, path in assets_to_analyze.items():
        analyze_asset(path, ticker)