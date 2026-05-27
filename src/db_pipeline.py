import os
import pandas as pd
from sqlalchemy import create_engine, text

# Database connection credentials (matching your docker-compose.yml)
DB_USER = "quant_user"
DB_PASSWORD = "quant_password123"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "quant_research"

# Initialize SQLAlchemy database engine
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URL)

def init_database():
    """
    Creates the necessary tables and optimizes them for time-series data using TimescaleDB.
    """
    create_table_query = """
    CREATE TABLE IF NOT EXISTS historical_prices (
        timestamp TIMESTAMPTZ NOT NULL,
        ticker VARCHAR(10) NOT NULL,
        open DOUBLE PRECISION NOT NULL,
        high DOUBLE PRECISION NOT NULL,
        low DOUBLE PRECISION NOT NULL,
        close DOUBLE PRECISION NOT NULL,
        volume DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (timestamp, ticker)
    );
    """
    
    # TimescaleDB magic: Converts a standard SQL table into a high-performance Hypertable
    convert_to_hypertable_query = """
    SELECT create_hypertable('historical_prices', 'timestamp', if_not_exists => TRUE);
    """
    
    with engine.connect() as conn:
        conn.execute(text(create_table_query))
        conn.commit()
        # Timescale hypertable conversion requires a separate transaction block
        try:
            conn.execute(text(convert_to_hypertable_query))
            conn.commit()
            print("🗄️ Database and Timescale hypertable initialized successfully.")
        except Exception as e:
            # Table might already be a hypertable, which is perfectly fine
            pass

def load_csv_to_sql(file_path: str, ticker_name: str):
    """
    Reads a local historical CSV file, cleans it, and inserts it into the database.
    """
    if not os.path.exists(file_path):
        print(f"❌ Cannot find file: {file_path}")
        return

    print(f"🚀 Loading {ticker_name.upper()} data from CSV into SQL database...")
    
    # Read the data
    df = pd.read_csv(file_path)
    
    # Rename and normalize columns to match our SQL schema exactly
    df.rename(columns={
        'Date': 'timestamp',
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    }, inplace=True)
    
    # Add the ticker identifier column
    df['ticker'] = ticker_name.upper()
    
    # Ensure columns match schema structure
    df = df[['timestamp', 'ticker', 'open', 'high', 'low', 'close', 'volume']]
    
    # Clean timestamps and ensure UTC localization
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    
    # Write dataframe rows into the SQL table (Bulk Insert)
    df.to_sql("historical_prices", con=engine, if_exists="append", index=False)
    print(f"✅ Successfully migrated {len(df)} rows for {ticker_name.upper()} into TimescaleDB.")