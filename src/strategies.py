import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate trading signals from OHLCV data.

        Args:
            df: DataFrame with columns ['open', 'high', 'low', 'close', 'volume']

        Returns:
            DataFrame with 'signal' column (1 = long, 0 = no position, -1 = short)
        """
        pass


class SMACrossoverStrategy(BaseStrategy):
    """Simple Moving Average crossover strategy."""

    def __init__(self, short_window: int = 10, long_window: int = 30):
        super().__init__("SMA_Crossover")
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fast MA > Slow MA = long signal."""
        result = df.copy()
        result['sma_fast'] = result['close'].rolling(window=self.short_window).mean()
        result['sma_slow'] = result['close'].rolling(window=self.long_window).mean()
        result['signal'] = np.where(result['sma_fast'] > result['sma_slow'], 1.0, 0.0)
        result = result.dropna(subset=['sma_slow'])
        return result


class RSIMeanReversionStrategy(BaseStrategy):
    """RSI-based mean reversion strategy."""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        super().__init__("RSI_MeanReversion")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """RSI < oversold = buy, RSI > overbought = sell."""
        result = df.copy()
        delta = result['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        rs = gain / loss
        result['rsi'] = 100 - (100 / (1 + rs))
        result['signal'] = np.where(result['rsi'] < self.oversold, 1.0,
                                    np.where(result['rsi'] > self.overbought, 0.0, np.nan))
        result['signal'] = result['signal'].fillna(method='ffill').fillna(0.0)
        return result


class RSITrendFilteredStrategy(BaseStrategy):
    """RSI mean reversion with trend filter using SMA_200."""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        super().__init__("RSI_TrendFiltered")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """RSI signal gated by 200-day SMA trend filter."""
        result = df.copy()

        delta = result['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        rs = gain / loss
        result['rsi'] = 100 - (100 / (1 + rs))

        result['sma_200'] = result['close'].rolling(window=200).mean()
        result['in_uptrend'] = result['close'] > result['sma_200']

        result['rsi_signal'] = np.where(result['rsi'] < self.oversold, 1.0,
                                        np.where(result['rsi'] > self.overbought, 0.0, np.nan))

        result['signal'] = np.where(
            result['in_uptrend'] & (result['rsi_signal'] == 1.0),
            1.0,
            0.0
        )
        result['signal'] = result['signal'].fillna(method='ffill').fillna(0.0)
        return result
