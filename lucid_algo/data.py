"""Data loading: real intraday futures bars (yfinance or CSV) and a
synthetic MES generator for offline development and testing.

All loaders return a DataFrame with a tz-aware America/New_York
DatetimeIndex and columns: open, high, low, close, volume.

Real-data notes
---------------
- yfinance only serves ~60 days of 5-minute history for futures
  ("MES=F"). Fine for smoke tests; for serious validation export 1-5 min
  bars from your trading platform (NinjaTrader, Tradovate, TradingView)
  to CSV and use load_csv().
- The synthetic generator reproduces the *texture* of MES (intraday
  U-shaped volatility, regime switching, fat tails) but contains no real
  edge or microstructure. Use it to test the pipeline, never to estimate
  live performance.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
COLUMNS = ["open", "high", "low", "close", "volume"]


def load_yfinance(symbol: str = "MES=F", interval: str = "5m",
                  period: str = "60d") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(symbol, interval=interval, period=period,
                     progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"yfinance returned no data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[COLUMNS]
    df.index = df.index.tz_convert(ET)
    return df


def load_csv(path: str, tz: str = "America/New_York") -> pd.DataFrame:
    """Load bars from CSV with columns: timestamp, open, high, low, close,
    volume. Timestamps may be naive (assumed `tz`) or tz-aware."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = next(c for c in df.columns if c in ("timestamp", "datetime", "time", "date"))
    idx = pd.to_datetime(df[ts_col])
    idx = idx.dt.tz_localize(tz) if idx.dt.tz is None else idx.dt.tz_convert(ET)
    df.index = pd.DatetimeIndex(idx)
    return df[COLUMNS].sort_index()


def rth_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep regular-trading-hours bars (09:30-16:00 ET), weekdays only."""
    t = df.index
    mask = (
        (t.weekday < 5)
        & ((t.hour > 9) | ((t.hour == 9) & (t.minute >= 30)))
        & (t.hour < 16)
    )
    return df[mask]


def make_synthetic_mes(n_days: int = 120, seed: int = 7,
                       start_price: float = 6000.0,
                       bar_minutes: int = 5) -> pd.DataFrame:
    """Generate synthetic MES 5-minute RTH bars.

    Regime-switching volatility (calm/normal/volatile days), U-shaped
    intraday vol, occasional trend days, 0.25-tick rounding.
    """
    rng = np.random.default_rng(seed)
    bars_per_day = (6 * 60 + 30) // bar_minutes  # 09:30-16:00
    sessions = pd.bdate_range("2026-01-05", periods=n_days, tz=ET)

    # Per-day regime: daily vol in points
    regimes = rng.choice([18.0, 30.0, 55.0], size=n_days, p=[0.35, 0.45, 0.20])
    trend_day = rng.random(n_days) < 0.22  # ~1 in 4.5 days trends

    # U-shaped intraday volatility profile
    x = np.linspace(0, 1, bars_per_day)
    u_shape = 0.6 + 1.4 * (4 * (x - 0.5) ** 2)
    u_shape /= u_shape.mean()

    rows, idx = [], []
    price = start_price
    for d in range(n_days):
        day_vol = regimes[d] / np.sqrt(bars_per_day)
        drift = 0.0
        if trend_day[d]:
            drift = rng.choice([-1, 1]) * regimes[d] * 0.6 / bars_per_day
        # Overnight gap
        price += rng.standard_t(4) * regimes[d] * 0.25
        open_px = price
        for b in range(bars_per_day):
            sigma = day_vol * u_shape[b]
            ret = drift + sigma * rng.standard_t(5) / np.sqrt(5 / 3)
            close_px = open_px + ret
            wick = abs(rng.normal(0, sigma * 0.6))
            high = max(open_px, close_px) + wick
            low = min(open_px, close_px) - abs(rng.normal(0, sigma * 0.6))
            ts = sessions[d] + pd.Timedelta(hours=9, minutes=30 + b * bar_minutes)
            q = 0.25
            rows.append([
                round(open_px / q) * q, round(high / q) * q,
                round(low / q) * q, round(close_px / q) * q,
                float(rng.integers(500, 5000)),
            ])
            idx.append(ts)
            open_px = close_px
        price = open_px

    return pd.DataFrame(rows, columns=COLUMNS, index=pd.DatetimeIndex(idx))
