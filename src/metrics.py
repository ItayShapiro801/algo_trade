import numpy as np
import pandas as pd
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class BacktestMetrics:
    """Container for all backtest performance metrics."""
    cumulative_return: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    win_rate: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_trade_return: float


class MetricsCalculator:
    """Calculates comprehensive performance and risk metrics for trading strategies."""

    RISK_FREE_RATE = 0.02
    ANNUAL_TRADING_DAYS = 252

    @staticmethod
    def calculate_cumulative_return(returns: pd.Series) -> float:
        """Cumulative return as a decimal (0.15 = +15%)."""
        return (1 + returns).prod() - 1

    @staticmethod
    def calculate_max_drawdown(prices: pd.Series) -> float:
        """Peak-to-trough decline from rolling cumulative maximum."""
        cumulative = (1 + prices).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return drawdown.min()

    @staticmethod
    def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = RISK_FREE_RATE) -> float:
        """Annualized Sharpe ratio: (mean_excess_return) / std_dev * sqrt(252)."""
        excess_returns = returns - (risk_free_rate / MetricsCalculator.ANNUAL_TRADING_DAYS)
        if returns.std() == 0:
            return 0.0
        return (excess_returns.mean() / returns.std()) * np.sqrt(MetricsCalculator.ANNUAL_TRADING_DAYS)

    @staticmethod
    def calculate_sortino_ratio(returns: pd.Series, risk_free_rate: float = RISK_FREE_RATE) -> float:
        """Downside-risk adjusted return ratio using only negative returns."""
        excess_returns = returns - (risk_free_rate / MetricsCalculator.ANNUAL_TRADING_DAYS)
        downside_returns = returns[returns < 0]
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return 0.0
        downside_deviation = downside_returns.std()
        return (excess_returns.mean() / downside_deviation) * np.sqrt(MetricsCalculator.ANNUAL_TRADING_DAYS)

    @staticmethod
    def calculate_win_rate(returns: pd.Series) -> float:
        """Percentage of positive trading returns."""
        if len(returns) == 0:
            return 0.0
        winning = (returns > 0).sum()
        return winning / len(returns) if len(returns) > 0 else 0.0

    @staticmethod
    def calculate_profit_factor(returns: pd.Series) -> float:
        """Ratio of gross profits to gross losses."""
        positive_sum = returns[returns > 0].sum()
        negative_sum = abs(returns[returns < 0].sum())
        if negative_sum == 0:
            return float('inf') if positive_sum > 0 else 0.0
        return positive_sum / negative_sum if negative_sum != 0 else 0.0

    @classmethod
    def compute_all_metrics(cls, returns: pd.Series, prices: pd.Series) -> BacktestMetrics:
        """Compute all metrics and return a BacktestMetrics dataclass."""
        cum_return = cls.calculate_cumulative_return(returns)
        max_dd = cls.calculate_max_drawdown(returns)
        sharpe = cls.calculate_sharpe_ratio(returns)
        sortino = cls.calculate_sortino_ratio(returns)
        win_rate = cls.calculate_win_rate(returns)
        profit_factor = cls.calculate_profit_factor(returns)

        total_trades = (returns != 0).sum()
        winning_trades = (returns > 0).sum()
        losing_trades = (returns < 0).sum()
        avg_return = returns.mean()

        metrics = BacktestMetrics(
            cumulative_return=cum_return,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=int(total_trades),
            winning_trades=int(winning_trades),
            losing_trades=int(losing_trades),
            avg_trade_return=avg_return
        )
        return metrics

    @staticmethod
    def print_metrics(metrics: BacktestMetrics, strategy_name: str = "Strategy"):
        """Pretty-print metrics summary."""
        print(f"\n{'='*60}")
        print(f"📊 BACKTEST RESULTS: {strategy_name}")
        print(f"{'='*60}")
        print(f"📈 Cumulative Return:        {metrics.cumulative_return:>10.2%}")
        print(f"📉 Max Drawdown:             {metrics.max_drawdown:>10.2%}")
        print(f"📊 Sharpe Ratio:             {metrics.sharpe_ratio:>10.2f}")
        print(f"🛡️  Sortino Ratio:            {metrics.sortino_ratio:>10.2f}")
        print(f"✅ Win Rate:                 {metrics.win_rate:>10.2%}")
        print(f"💰 Profit Factor:            {metrics.profit_factor:>10.2f}")
        print(f"🎯 Total Trades:             {metrics.total_trades:>10}")
        print(f"   - Winning:               {metrics.winning_trades:>10}")
        print(f"   - Losing:                {metrics.losing_trades:>10}")
        print(f"📊 Avg Trade Return:         {metrics.avg_trade_return:>10.4f}")
        print(f"{'='*60}\n")
