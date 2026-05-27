import logging
from typing import List, Dict, Tuple

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketScreener:
    """
    MarketScreener downloads a trailing one-year window for a pre-seeded
    list of small-cap equities and altcoins and applies strict quantitative
    filters to produce a Qualified Dynamic Universe.

    Filters:
    - SMA_200 Trend: latest close > 200-day SMA
    - Liquidity: 10-day average dollar volume > $100,000
    - Penny Protection: latest close > $0.50
    """

    def __init__(self, tracking_list: List[str] = None):
        # Expand the default tracking list with 15-20 volatile small-caps/altcoins
        default = [
            "OPHC", "GORO", "DIMO-USD", "ALEPH-USD", "MIGI", "WULF",
            "AULT", "ANY", "BTCM", "SDIG", "LMFA", "VPTC", "MRAI",
            "CETY", "SOBR", "SOL-USD", "HUT", "BBAI", "CLSK", "PHUN"
        ]
        self.tracking_list = tracking_list or default

    def _download_1y(self, ticker: str) -> pd.DataFrame:
        """Download safe 1-year daily OHLCV history via yfinance."""
        try:
            df = yf.download(ticker, period="1y", interval="1d", auto_adjust=False, progress=False)
            if df is None or df.empty:
                raise ValueError("no data")

            # Normalize columns if multi-index
            df.columns = df.columns.get_level_values(0) if isinstance(df.columns, pd.MultiIndex) else df.columns

            # Ensure required columns exist
            required = ["Open", "High", "Low", "Close", "Volume"]
            if not all(col in df.columns for col in required):
                raise ValueError("missing required OHLCV columns")

            # Keep only required columns with standard names
            df = df[required].copy()
            df.dropna(inplace=True)
            return df
        except Exception as e:
            logger.debug(f"Download failed for {ticker}: {e}")
            raise

    def screen(self) -> Tuple[List[str], Dict[str, str]]:
        """
        Execute the screening process over the tracking list.

        Returns:
            qualified: list of tickers that passed all filters
            disqualified_reasons: mapping ticker -> human-readable reason
        """
        qualified: List[str] = []
        disqualified: Dict[str, str] = {}

        for ticker in self.tracking_list:
            try:
                df = self._download_1y(ticker)

                # Need at least 200 days for SMA_200
                if len(df) < 200:
                    disqualified[ticker] = "insufficient history (<200 days)"
                    logger.info(f"{ticker}: disqualified - insufficient history ({len(df)} bars)")
                    continue

                close = df["Close"]

                sma_200 = close.rolling(window=200).mean()
                latest_close = float(close.iloc[-1])
                latest_sma200 = float(sma_200.iloc[-1])

                if not (latest_close > latest_sma200):
                    disqualified[ticker] = "price below 200-day SMA"
                    logger.info(f"{ticker}: disqualified - price {latest_close:.4f} <= SMA200 {latest_sma200:.4f}")
                    continue

                # Dollar volume filter: 10-day average of (Close * Volume)
                dollar_vol = (df["Close"] * df["Volume"]).rolling(window=10).mean()
                latest_dv = float(dollar_vol.iloc[-1]) if not dollar_vol.isna().all() else 0.0

                if latest_dv <= 100_000:
                    disqualified[ticker] = f"low liquidity (10d avg dollar vol ${latest_dv:,.0f})"
                    logger.info(f"{ticker}: disqualified - low liquidity ${latest_dv:,.0f}")
                    continue

                if latest_close <= 0.5:
                    disqualified[ticker] = f"penny protection failed (price ${latest_close:.2f})"
                    logger.info(f"{ticker}: disqualified - penny protection ${latest_close:.2f}")
                    continue

                # Passed all checks
                qualified.append(ticker)
                logger.info(f"{ticker}: qualified (close ${latest_close:.2f}, 10dDV=${latest_dv:,.0f})")

            except Exception as e:
                disqualified[ticker] = f"error during processing: {e}"
                logger.warning(f"{ticker}: screening error - {e}")

        return qualified, disqualified


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    screener = MarketScreener()
    q, dq = screener.screen()
    print("Qualified:", q)
    print("Disqualified:", dq)
