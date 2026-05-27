import pandas as pd
import numpy as np
from itertools import product
import logging
from typing import Dict, List, Tuple
from src.backtest_engine import BacktestEngine
from src.strategies import RSITrendFilteredStrategy
from src.metrics import MetricsCalculator

logger = logging.getLogger(__name__)


class StrategyOptimizer:
    """Grid search optimizer for strategy parameters with walk-forward validation."""

    def __init__(self, db_url: str, initial_capital: float = 10000.0):
        """
        Initialize optimizer with database connection and capital.

        Args:
            db_url: SQLAlchemy database URL
            initial_capital: Starting portfolio value for backtests
        """
        self.engine = BacktestEngine(db_url, initial_capital=initial_capital)
        self.optimization_results = []

    def optimize(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        rsi_windows: List[int],
        oversold_levels: List[float],
        overbought_levels: List[float]
    ) -> Dict:
        """
        Execute grid search optimization with walk-forward validation (70% train, 30% test).

        Args:
            ticker: Asset ticker symbol
            start_date: Start date for full data fetch
            end_date: End date for full data fetch
            rsi_windows: List of RSI period values to test
            oversold_levels: List of oversold threshold values to test
            overbought_levels: List of overbought threshold values to test

        Returns:
            Dictionary with best parameters and ranked results
        """
        logger.info(
            f"Starting optimization for {ticker}: {len(rsi_windows)*len(oversold_levels)*len(overbought_levels)} parameter combinations"
        )

        df = self.engine.fetch_data_from_db(ticker, start_date, end_date)
        logger.info(f"Fetched {len(df)} data points for {ticker}")

        split_idx = int(len(df) * 0.7)
        train_df = df.iloc[:split_idx].copy()
        test_df = df.iloc[split_idx:].copy()

        logger.info(
            f"Data split: Train={len(train_df)} bars, Test={len(test_df)} bars"
        )

        best_train_sharpe = -np.inf
        best_params = None
        results = []

        for rsi_w, oversold, overbought in product(
            rsi_windows, oversold_levels, overbought_levels
        ):
            strategy = RSITrendFilteredStrategy(
                period=rsi_w, oversold=oversold, overbought=overbought
            )

            train_signals = strategy.generate_signals(train_df.copy())
            train_signals["position"] = (
                train_signals["signal"].shift(1).fillna(0.0)
            )
            train_signals["market_return"] = train_signals["close"].pct_change()
            train_signals["strategy_return"] = (
                train_signals["market_return"] * train_signals["position"]
            )
            train_adjusted = self.engine._apply_costs(
                train_signals["strategy_return"], train_signals["position"]
            )
            train_metrics = MetricsCalculator.compute_all_metrics(
                train_adjusted.dropna(), train_signals["close"]
            )

            test_signals = strategy.generate_signals(test_df.copy())
            test_signals["position"] = test_signals["signal"].shift(1).fillna(0.0)
            test_signals["market_return"] = test_signals["close"].pct_change()
            test_signals["strategy_return"] = (
                test_signals["market_return"] * test_signals["position"]
            )
            test_adjusted = self.engine._apply_costs(
                test_signals["strategy_return"], test_signals["position"]
            )
            test_metrics = MetricsCalculator.compute_all_metrics(
                test_adjusted.dropna(), test_signals["close"]
            )

            results.append(
                {
                    "rsi_window": rsi_w,
                    "oversold": oversold,
                    "overbought": overbought,
                    "train_sharpe": train_metrics.sharpe_ratio,
                    "train_sortino": train_metrics.sortino_ratio,
                    "train_return": train_metrics.cumulative_return,
                    "train_max_dd": train_metrics.max_drawdown,
                    "test_sharpe": test_metrics.sharpe_ratio,
                    "test_sortino": test_metrics.sortino_ratio,
                    "test_return": test_metrics.cumulative_return,
                    "test_max_dd": test_metrics.max_drawdown,
                    "train_metrics": train_metrics,
                    "test_metrics": test_metrics,
                }
            )

            if train_metrics.sharpe_ratio > best_train_sharpe:
                best_train_sharpe = train_metrics.sharpe_ratio
                best_params = (rsi_w, oversold, overbought)

        results_df = pd.DataFrame(results).sort_values("train_sharpe", ascending=False)

        self.optimization_results.append(
            {
                "ticker": ticker,
                "best_params": best_params,
                "results_df": results_df,
            }
        )

        logger.info(
            f"Optimization complete for {ticker}. Best params: RSI={best_params[0]}, Oversold={best_params[1]}, Overbought={best_params[2]}, Train Sharpe={best_train_sharpe:.2f}"
        )

        return {
            "ticker": ticker,
            "best_params": best_params,
            "best_train_sharpe": best_train_sharpe,
            "results_df": results_df,
        }

    def print_optimization_summary(self, top_n: int = 5):
        """
        Print clean console table of top optimized parameters for each asset.

        Args:
            top_n: Number of top parameter sets to display per asset
        """
        print(f"\n{'='*120}")
        print(f"🎯 STRATEGY OPTIMIZATION RESULTS - WALK-FORWARD VALIDATION")
        print(f"{'='*120}\n")

        for result in self.optimization_results:
            ticker = result["ticker"]
            results_df = result["results_df"]
            best_params = result["best_params"]

            print(f"📊 ASSET: {ticker.upper()}")
            print(f"   Best Parameters: RSI={best_params[0]}, Oversold={best_params[1]:.1f}, Overbought={best_params[2]:.1f}")
            print(f"\n   Top {top_n} Parameter Sets (ranked by In-Sample Sharpe Ratio):\n")

            display_cols = [
                "rsi_window",
                "oversold",
                "overbought",
                "train_sharpe",
                "test_sharpe",
                "train_return",
                "test_return",
                "train_max_dd",
            ]

            top_results = results_df[display_cols].head(top_n).copy()
            top_results.columns = [
                "RSI_W",
                "Oversold",
                "Overbought",
                "Train_Sharpe",
                "Test_Sharpe",
                "Train_Ret%",
                "Test_Ret%",
                "Max_DD%",
            ]

            for col in ["Train_Ret%", "Test_Ret%", "Max_DD%"]:
                if col in top_results.columns:
                    top_results[col] = top_results[col].apply(lambda x: f"{x:.2%}")

            for col in ["Train_Sharpe", "Test_Sharpe"]:
                if col in top_results.columns:
                    top_results[col] = top_results[col].apply(lambda x: f"{x:.2f}")

            print(top_results.to_string(index=False))
            print(f"\n{'─'*120}\n")

        print(f"{'='*120}\n")
