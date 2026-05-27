import os
import yfinance as yf
import pandas as pd

def fetch_historical_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Downloads historical market data from Yahoo Finance for Small-Cap/Crypto assets.
    """
    print(f"📥 Fetching data for {ticker} from {start_date} to {end_date}...")
    
    # Download data using yfinance
    df = yf.download(ticker, start=start_date, end=end_date)
    
    if df.empty:
        raise ValueError(f"No data found for ticker: {ticker}")
        
    # Standardize column structure and clear multi-index if present
    df.columns = df.columns.get_level_values(0) if isinstance(df.columns, pd.MultiIndex) else df.columns
    
    # Keep only required columns for quant research
    required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    df = df[required_cols]
    
    return df

def save_data_to_csv(df: pd.DataFrame, ticker: str):
    """
    Saves the fetched dataframe into the local data directory.
    """
    # Ensure the data directory exists
    os.makedirs("data", exist_ok=True)
    
    # Format the file name cleanly
    clean_ticker = ticker.replace("-", "_").lower()
    file_path = f"data/{clean_ticker}_history.csv"
    
    df.to_csv(file_path)
    print(f"💾 Data successfully saved to {file_path}")

if __name__ == "__main__":
    # Volatile Small-Cap stocks and Crypto assets where retail traders have an edge
    target_assets = ["SOL-USD", "HUT", "BBAI", "CLSK"]
    
    for asset in target_assets:
        try:
            print("\n--------------------------------------------")
            # Downloading 2.5 years of daily history for mathematical backtesting
            historical_df = fetch_historical_data(asset, "2024-01-01", "2026-05-25")
            
            print(f"--- {asset} Data Preview ---")
            print(historical_df.head(3))
            
            save_data_to_csv(historical_df, asset)
            
        except Exception as e:
            print(f"❌ Error occurred for {asset}: {e}")