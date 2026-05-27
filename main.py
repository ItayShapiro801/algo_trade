import os
import sys
import logging
from datetime import date, timedelta
from typing import List

from src.data_loader import fetch_historical_data, save_data_to_csv
from src.db_pipeline import init_database, load_csv_to_sql
from src.backtest_engine import BacktestEngine, StrategyComparator
from src.strategies import SMACrossoverStrategy, RSIMeanReversionStrategy
from src.metrics import MetricsCalculator
from src.market_screener import MarketScreener
from src.optimizer import StrategyOptimizer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_USER = "quant_user"
DB_PASSWORD = "quant_password123"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "quant_research"
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def main():
    print("==================================================")
    print("🤖 CORE QUANT RESEARCH SYSTEM - ADVANCED PIPELINE 🤖")
    print("==================================================")

    # PHASE 1: Dynamic Market Screening
    print("\n[PHASE 1] Running Market Screener...")
    screener = MarketScreener()
    qualified, disqualified = screener.screen()

    # Nicely formatted console block showing results
    print('\n' + '=' * 80)
    print('🎯 MARKET SCREENER RESULTS')
    print('=' * 80)
    print('\nQualified Dynamic Universe:')
    if qualified:
        for t in qualified:
            print(f" - {t}")
    else:
        print(' - (none)')

    print('\nDisqualified / Skipped:')
    for t, reason in disqualified.items():
        print(f" - {t}: {reason}")
    print('=' * 80 + '\n')

    # PHASE 2: Halt if no universe
    if not qualified:
        logger.warning("Qualified universe empty after screening. Exiting gracefully.")
        return

    # PHASE 3: Dynamic Data Ingestion (only for qualified assets)
    print("[PHASE 3] Dynamic Data Ingestion for qualified assets...")
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=365 * 2)  # fetch last ~2 years for robustness
    for asset in qualified:
        clean_name = asset.replace("-", "_").lower()
        file_path = f"data/{clean_name}_history.csv"

        try:
            if not os.path.exists(file_path):
                df = fetch_historical_data(asset, start_dt.isoformat(), end_dt.isoformat())
                save_data_to_csv(df, asset)
            else:
                print(f"💾 CSV cache exists for {asset} -> {file_path}")
        except Exception as e:
            logger.error(f"Failed ingestion for {asset}: {e}")

    # PHASE 4: Production DB Migration (TimescaleDB)
    print("\n[PHASE 4] Database Initialization & CSV -> TimescaleDB ingestion...")
    init_database()
    for asset in qualified:
        clean_name = asset.replace("-", "_").lower()
        csv_path = f"data/{clean_name}_history.csv"
        try:
            load_csv_to_sql(csv_path, clean_name)
        except Exception as e:
            logger.error(f"Migration failed for {clean_name}: {e}")

    # PHASE 5: Advanced Backtesting Sweep (SMA vs RSI) using DB
    print("\n[PHASE 5] Advanced Backtesting Sweep (SMA vs RSI) using SQL data...")
    engine = BacktestEngine(DATABASE_URL, initial_capital=10000.0, commission=0.001)

    strategies_to_test = [
        SMACrossoverStrategy(short_window=10, long_window=30),
        RSIMeanReversionStrategy(period=14, oversold=30, overbought=70),
    ]

    for asset in qualified:
        clean_name = asset.replace("-", "_").lower()
        print(f"\n{'='*60}")
        print(f"🔬 Backtesting {asset.upper()}...")
        print(f"{'='*60}")

        comparator = StrategyComparator(engine)
        try:
            comparator.compare_strategies(
                strategies_to_test,
                ticker=clean_name,
                start_date=start_dt.isoformat(),
                end_date=end_dt.isoformat(),
                use_csv=None
            )

            for strategy_name, metrics in comparator.results.items():
                MetricsCalculator.print_metrics(metrics, strategy_name)
        except Exception as e:
            logger.error(f"Backtest failed for {asset}: {e}")

    # PHASE 6: Strategy Optimization (Walk-forward grid search) for qualified assets
    print("\n[PHASE 6] Strategy Optimization - Walk-Forward Grid Search...")
    optimizer = StrategyOptimizer(DATABASE_URL, initial_capital=10000.0)

    # Parameter grids (kept small to limit runtime)
    rsi_windows = [10, 14, 21]
    oversold_levels = [25, 30]
    overbought_levels = [70, 75]

    for asset in qualified:
        clean_name = asset.replace("-", "_").lower()
        try:
            res = optimizer.optimize(
                ticker=clean_name,
                start_date=start_dt.isoformat(),
                end_date=end_dt.isoformat(),
                rsi_windows=rsi_windows,
                oversold_levels=oversold_levels,
                overbought_levels=overbought_levels,
            )
            # Print concise best parameters
            best = res.get("best_params")
            if best:
                print(f"✅ Optimization complete for {asset}: Best RSI={best[0]}, Oversold={best[1]}, Overbought={best[2]}")
        except Exception as e:
            logger.error(f"Optimization failed for {asset}: {e}")

    optimizer.print_optimization_summary(top_n=3)

    print("\n✅ Full pipeline execution complete!")


if __name__ == "__main__":
    main()