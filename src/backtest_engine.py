import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import logging
from datetime import datetime
from typing import Optional, Tuple
from src.strategies import BaseStrategy
from src.metrics import MetricsCalculator, BacktestMetrics

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Production-grade backtesting engine for strategy evaluation."""

    def __init__(
        self,
        db_url: str,
        initial_capital: float = 10000.0,
        commission: float = 0.001,
        slippage: float = 0.0005
    ):
        """
        Initialize the backtesting engine.

        Args:
            db_url: SQLAlchemy database connection URL
            initial_capital: Starting portfolio value in USD
            commission: Per-trade commission as a decimal (0.001 = 0.1%)
            slippage: Per-trade slippage as a decimal
        """
        self.engine = create_engine(db_url)
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        logger.info(f"BacktestEngine initialized: capital=${initial_capital:.2f}")

    def fetch_data_from_db(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch OHLCV data from TimescaleDB for a specific ticker and date range.

        Args:
            ticker: Asset ticker symbol (e.g., 'SOL', 'HUT')
            start_date: ISO format start date (e.g., '2024-01-01')
            end_date: ISO format end date (e.g., '2026-05-25')

        Returns:
            DataFrame with columns [timestamp, open, high, low, close, volume]
        """
        query = """
        SELECT timestamp, open, high, low, close, volume
        FROM historical_prices
        WHERE ticker = :ticker
          AND timestamp >= :start_date
          AND timestamp <= :end_date
        ORDER BY timestamp ASC;
        """

        with self.engine.connect() as conn:
            result = conn.execute(text(query), {
                "ticker": ticker.upper(),
                "start_date": start_date,
                "end_date": end_date
            })
            rows = result.fetchall()

        if not rows:
            raise ValueError(f"No data found for {ticker} between {start_date} and {end_date}")

        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        logger.info(f"Fetched {len(df)} bars for {ticker}")
        return df

    def run_backtest(
        self,
        strategy: BaseStrategy,
        ticker: str,
        start_date: str,
        end_date: str,
        use_csv: Optional[str] = None
    ) -> Tuple[BacktestMetrics, pd.DataFrame]:
        """
        Execute a complete backtest simulation for a strategy.

        Args:
            strategy: Strategy object implementing BaseStrategy interface
            ticker: Asset ticker
            start_date: Backtest start date
            end_date: Backtest end date
            use_csv: Optional CSV file path instead of database

        Returns:
            Tuple of (BacktestMetrics, results_dataframe)
        """
        logger.info(f"Starting backtest: {strategy.name} on {ticker} ({start_date} to {end_date})")

        if use_csv:
            df = pd.read_csv(use_csv, parse_dates=['Date'], index_col='Date')
            df.columns = [col.lower() for col in df.columns]
        else:
            df = self.fetch_data_from_db(ticker, start_date, end_date)

        df_with_signals = strategy.generate_signals(df)

        df_with_signals['position'] = df_with_signals['signal'].shift(1).fillna(0.0)

        df_with_signals['market_return'] = df_with_signals['close'].pct_change()
        df_with_signals['strategy_return'] = (
            df_with_signals['market_return'] * df_with_signals['position']
        )

        df_with_signals['adjusted_return'] = self._apply_costs(
            df_with_signals['strategy_return'],
            df_with_signals['position']
        )

        cumulative_returns = (1 + df_with_signals['adjusted_return']).cumprod() - 1
        df_with_signals['portfolio_value'] = self.initial_capital * (1 + cumulative_returns)

        metrics = MetricsCalculator.compute_all_metrics(
            df_with_signals['adjusted_return'].dropna(),
            df_with_signals['portfolio_value']
        )

        logger.info(
            f"Backtest completed: {strategy.name} | "
            f"Return: {metrics.cumulative_return:.2%} | "
            f"Sharpe: {metrics.sharpe_ratio:.2f} | "
            f"Max DD: {metrics.max_drawdown:.2%}"
        )

        return metrics, df_with_signals

    def _apply_costs(self, returns: pd.Series, positions: pd.Series) -> pd.Series:
        """Deduct transaction costs (commission + slippage) from returns."""
        costs = pd.Series(0.0, index=returns.index)
        position_changes = positions.diff().fillna(0).abs()
        trade_mask = position_changes > 0
        costs[trade_mask] = self.commission + self.slippage
        return returns - costs


class StrategyComparator:
    """Compare multiple strategies against the same asset."""

    def __init__(self, engine: BacktestEngine):
        self.engine = engine
        self.results = {}

    def compare_strategies(
        self,
        strategies: list,
        ticker: str,
        start_date: str,
        end_date: str,
        use_csv: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Run multiple strategies and return comparison DataFrame.

        Args:
            strategies: List of BaseStrategy objects
            ticker: Asset ticker
            start_date: Backtest start date
            end_date: Backtest end date
            use_csv: Optional CSV file path

        Returns:
            DataFrame comparing all strategies
        """
        logger.info(f"Comparing {len(strategies)} strategies on {ticker}")

        comparison_data = []
        for strategy in strategies:
            try:
                metrics, _ = self.engine.run_backtest(strategy, ticker, start_date, end_date, use_csv)
                self.results[strategy.name] = metrics
                comparison_data.append({
                    'strategy': strategy.name,
                    'return': metrics.cumulative_return,
                    'sharpe': metrics.sharpe_ratio,
                    'max_drawdown': metrics.max_drawdown,
                    'win_rate': metrics.win_rate,
                    'profit_factor': metrics.profit_factor
                })
            except Exception as e:
                logger.error(f"Error running {strategy.name}: {e}")

        comparison_df = pd.DataFrame(comparison_data)
        self._print_comparison(comparison_df)
        return comparison_df

    def _print_comparison(self, df: pd.DataFrame):
        """Pretty-print strategy comparison."""
        print(f"\n{'='*80}")
        print(f"🏆 STRATEGY COMPARISON")
        print(f"{'='*80}")
        print(df.to_string(index=False))
        print(f"{'='*80}\n")
