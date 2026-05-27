"""
Production-grade Risk Management Module for Algorithmic Trading.

This module implements comprehensive risk mitigation strategies including:
- Dynamic Position Sizing based on account equity and max risk tolerance
- Stop Loss (SL) and Take Profit (TP) calculation
- Trade horizon validation
- Capital preservation logic

Author: Quantitative Development Team
Version: 1.0.0
"""

import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskMetrics:
    """Container for trade-level risk metrics."""
    position_size: float
    stop_loss_price: float
    take_profit_price: float
    risk_amount: float
    reward_potential: float
    risk_reward_ratio: float
    max_loss_pct: float
    max_gain_pct: float


class RiskManager:
    """
    Production-grade risk management engine for algorithmic trading systems.
    
    Manages position sizing, stop losses, take profits, and capital preservation
    through sophisticated quantitative models.
    """

    def __init__(
        self,
        total_capital: float,
        max_risk_per_trade_pct: float = 1.0,
        max_position_size_pct: float = 10.0,
        min_position_size: float = 100.0
    ):
        """
        Initialize the Risk Manager.

        Args:
            total_capital: Total portfolio capital in USD
            max_risk_per_trade_pct: Maximum risk as % of total capital per trade (e.g., 1.0 = 1%)
            max_position_size_pct: Maximum position size as % of total capital (e.g., 10.0 = 10%)
            min_position_size: Minimum position size in USD

        Raises:
            ValueError: If parameters are invalid
        """
        if total_capital <= 0:
            raise ValueError("Total capital must be positive")
        if not (0 < max_risk_per_trade_pct <= 100):
            raise ValueError("max_risk_per_trade_pct must be between 0 and 100")
        if not (0 < max_position_size_pct <= 100):
            raise ValueError("max_position_size_pct must be between 0 and 100")
        if min_position_size < 0:
            raise ValueError("min_position_size must be non-negative")

        self.total_capital = total_capital
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_position_size_pct = max_position_size_pct
        self.min_position_size = min_position_size
        self.available_capital = total_capital

        logger.info(
            f"RiskManager initialized: Capital=${total_capital:.2f}, "
            f"MaxRiskPerTrade={max_risk_per_trade_pct}%, "
            f"MaxPositionSize={max_position_size_pct}%"
        )

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        current_available_capital: Optional[float] = None
    ) -> float:
        """
        Calculate optimal position size using the Kelly Criterion-inspired formula.

        Formula:
        Risk Amount = Total Capital × Max Risk Per Trade %
        Stop Loss Distance = Entry Price - Stop Loss Price
        Position Size = Risk Amount / Stop Loss Distance

        Capped by:
        1. Maximum position size as % of total capital
        2. Available cash in account
        3. Minimum position size threshold

        Args:
            entry_price: Entry price per unit in USD
            stop_loss_price: Stop loss price per unit in USD
            current_available_capital: Current available cash (uses self.available_capital if None)

        Returns:
            Optimal position size in units (quantity)

        Raises:
            ValueError: If entry_price <= stop_loss_price or prices are invalid
        """
        if entry_price <= 0 or stop_loss_price < 0:
            raise ValueError("Prices must be positive")
        if entry_price <= stop_loss_price:
            raise ValueError(
                f"Entry price (${entry_price:.2f}) must be above "
                f"stop loss (${stop_loss_price:.2f})"
            )

        if current_available_capital is None:
            current_available_capital = self.available_capital

        # Calculate risk amount: what we're willing to lose on this trade
        risk_amount = self.total_capital * (self.max_risk_per_trade_pct / 100.0)

        # Calculate stop loss distance (how much we lose per unit if hit)
        sl_distance = entry_price - stop_loss_price

        # Calculate position size based on risk
        position_size_by_risk = risk_amount / sl_distance

        # Apply maximum position size constraint (% of total capital)
        max_position_by_capital = (
            self.total_capital * (self.max_position_size_pct / 100.0)
        ) / entry_price

        # Apply available capital constraint
        max_position_by_liquidity = current_available_capital / entry_price

        # Take the minimum of all constraints
        position_size = min(
            position_size_by_risk,
            max_position_by_capital,
            max_position_by_liquidity
        )

        # Apply minimum position size floor
        if position_size < self.min_position_size / entry_price:
            logger.warning(
                f"Calculated position size {position_size:.2f} units "
                f"(${position_size * entry_price:.2f}) below minimum. "
                f"Rounding to minimum."
            )
            position_size = max(position_size, self.min_position_size / entry_price)

        logger.debug(
            f"Position size calculated: {position_size:.2f} units @ ${entry_price:.2f} "
            f"= ${position_size * entry_price:.2f} | SL=${stop_loss_price:.2f}"
        )

        return position_size

    def calculate_stop_loss_and_tp(
        self,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float
    ) -> Tuple[float, float]:
        """
        Calculate stop loss and take profit price levels from percentage parameters.

        Args:
            entry_price: Entry price per unit in USD
            stop_loss_pct: Stop loss as % below entry (e.g., 5.0 = 5%)
            take_profit_pct: Take profit as % above entry (e.g., 20.0 = 20%)

        Returns:
            Tuple of (stop_loss_price, take_profit_price)

        Raises:
            ValueError: If parameters are invalid
        """
        if entry_price <= 0:
            raise ValueError("Entry price must be positive")
        if not (0 < stop_loss_pct < 100):
            raise ValueError("stop_loss_pct must be between 0 and 100")
        if not (0 < take_profit_pct < 1000):
            raise ValueError("take_profit_pct must be positive and less than 1000")

        stop_loss_price = entry_price * (1 - stop_loss_pct / 100.0)
        take_profit_price = entry_price * (1 + take_profit_pct / 100.0)

        logger.debug(
            f"SL/TP calculated: Entry=${entry_price:.2f} | "
            f"SL=${stop_loss_price:.2f} ({stop_loss_pct}%) | "
            f"TP=${take_profit_price:.2f} ({take_profit_pct}%)"
        )

        return stop_loss_price, take_profit_price

    def calculate_trade_risk_metrics(
        self,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        current_available_capital: Optional[float] = None
    ) -> RiskMetrics:
        """
        Comprehensive trade risk analysis combining position sizing, SL/TP, and risk metrics.

        Args:
            entry_price: Entry price per unit
            stop_loss_pct: Stop loss % below entry
            take_profit_pct: Take profit % above entry
            current_available_capital: Current available cash in account

        Returns:
            RiskMetrics dataclass with complete trade risk profile
        """
        stop_loss_price, take_profit_price = self.calculate_stop_loss_and_tp(
            entry_price, stop_loss_pct, take_profit_pct
        )

        position_size = self.calculate_position_size(
            entry_price, stop_loss_price, current_available_capital
        )

        # Calculate monetary risk and reward
        risk_amount = position_size * (entry_price - stop_loss_price)
        reward_potential = position_size * (take_profit_price - entry_price)

        # Avoid division by zero
        risk_reward_ratio = (
            reward_potential / risk_amount
            if risk_amount > 0 else 0.0
        )

        max_loss_pct = (risk_amount / self.total_capital) * 100.0
        max_gain_pct = (reward_potential / self.total_capital) * 100.0

        metrics = RiskMetrics(
            position_size=position_size,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_amount=risk_amount,
            reward_potential=reward_potential,
            risk_reward_ratio=risk_reward_ratio,
            max_loss_pct=max_loss_pct,
            max_gain_pct=max_gain_pct
        )

        logger.info(
            f"Trade Risk Metrics: Position={position_size:.2f} units | "
            f"Risk=${risk_amount:.2f} | Reward=${reward_potential:.2f} | "
            f"RR={risk_reward_ratio:.2f}:1"
        )

        return metrics

    def validate_trade_horizon(
        self,
        entry_price: float,
        current_price: float,
        stop_loss_pct: float,
        take_profit_pct: float
    ) -> Tuple[bool, str]:
        """
        Validate that a trade is still within acceptable risk parameters.

        Used to check if a position should be closed based on SL/TP criteria.

        Args:
            entry_price: Original entry price
            current_price: Current market price
            stop_loss_pct: Stop loss threshold %
            take_profit_pct: Take profit threshold %

        Returns:
            Tuple of (is_valid, reason)
            - is_valid: True if trade should remain open
            - reason: Explanation of validation result
        """
        stop_loss_price, take_profit_price = self.calculate_stop_loss_and_tp(
            entry_price, stop_loss_pct, take_profit_pct
        )

        if current_price <= stop_loss_price:
            return False, f"Stop Loss hit: {current_price:.2f} <= {stop_loss_price:.2f}"

        if current_price >= take_profit_price:
            return False, f"Take Profit hit: {current_price:.2f} >= {take_profit_price:.2f}"

        return True, "Trade horizon valid"

    def apply_position_sizing_to_backtest(
        self,
        signals_df: pd.DataFrame,
        entry_price_col: str = 'close',
        stop_loss_pct: float = 5.0,
        initial_capital: float = 10000.0,
        max_risk_per_trade_pct: float = 1.0
    ) -> pd.DataFrame:
        """
        Apply dynamic position sizing to backtest signals with risk management.

        This method integrates position sizing logic into a backtest DataFrame,
        adjusting position weights based on calculated stop losses and available capital.

        Args:
            signals_df: DataFrame with 'signal' column (trading signals)
            entry_price_col: Column name for entry prices (default: 'close')
            stop_loss_pct: Stop loss percentage
            initial_capital: Starting capital
            max_risk_per_trade_pct: Max risk % per trade

        Returns:
            Enhanced DataFrame with 'position_size' and 'position_weight' columns
        """
        result = signals_df.copy()
        result['position_size'] = 0.0
        result['position_weight'] = 0.0

        available_cash = initial_capital
        entry_prices = {}

        for i in range(len(result)):
            if result['signal'].iloc[i] == 1.0:  # Long signal
                entry_price = result[entry_price_col].iloc[i]
                stop_loss_price = entry_price * (1 - stop_loss_pct / 100.0)

                position_size = self.calculate_position_size(
                    entry_price, stop_loss_price, available_cash
                )

                position_cost = position_size * entry_price
                position_weight = position_cost / self.total_capital

                result.loc[result.index[i], 'position_size'] = position_size
                result.loc[result.index[i], 'position_weight'] = position_weight

                available_cash -= position_cost
                entry_prices[i] = entry_price

            elif result['signal'].iloc[i] == 0.0:  # Close signal
                result.loc[result.index[i], 'position_size'] = 0.0
                result.loc[result.index[i], 'position_weight'] = 0.0
                # Recover cash on exit
                if i - 1 in entry_prices:
                    prev_entry = entry_prices.pop(i - 1, None)
                    if prev_entry:
                        prev_size = result['position_size'].iloc[i - 1]
                        available_cash += prev_size * result[entry_price_col].iloc[i]

        logger.info(f"Position sizing applied to {len(result)} bars")
        return result

    def get_portfolio_status(self) -> dict:
        """
        Get current portfolio risk status summary.

        Returns:
            Dictionary with portfolio risk metrics
        """
        utilized_capital = self.total_capital - self.available_capital
        utilization_pct = (utilized_capital / self.total_capital) * 100.0

        return {
            'total_capital': self.total_capital,
            'available_capital': self.available_capital,
            'utilized_capital': utilized_capital,
            'utilization_pct': utilization_pct,
            'max_risk_per_trade_pct': self.max_risk_per_trade_pct,
            'max_position_size_pct': self.max_position_size_pct
        }

    def reset_capital(self, new_capital: float):
        """Reset available capital (useful after trade exits)."""
        if new_capital < 0:
            raise ValueError("Capital cannot be negative")
        self.available_capital = new_capital
        logger.info(f"Available capital reset to ${new_capital:.2f}")
