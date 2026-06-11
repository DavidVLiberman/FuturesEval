#!/usr/bin/env python3
"""Convert a Databento OHLCV CSV export into the backtester's CSV format.

Works with CSV downloads from https://databento.com (dataset GLBX.MDP3,
schema ohlcv-1m or ohlcv-1s, symbology "continuous" e.g. MES.c.0 or raw
contract symbols). Handles both timestamp styles (ISO strings or integer
nanoseconds) and both price styles (plain decimals or fixed-precision
integers scaled by 1e9), converts UTC to America/New_York, and resamples
to 5-minute bars.

Usage:
    python scripts/convert_databento.py mes_download.csv -o mes_bars.csv
    python scripts/run_backtest.py --data csv --csv mes_bars.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from lucid_algo.data import COLUMNS, ET


def read_databento(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = next(c for c in df.columns if c.startswith("ts_"))

    ts = df[ts_col]
    if pd.api.types.is_numeric_dtype(ts):
        idx = pd.to_datetime(ts, unit="ns", utc=True)
    else:
        idx = pd.to_datetime(ts, utc=True, format="ISO8601")
    df.index = pd.DatetimeIndex(idx).tz_convert(ET)

    if "volume" not in df.columns and "size" in df.columns:
        df = df.rename(columns={"size": "volume"})
    df = df[COLUMNS].astype(float)
    # Fixed-precision exports encode 5982.25 as 5982250000000.
    if df["close"].median() > 1e6:
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] / 1e9
    return df.sort_index()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="Databento CSV file(s)")
    ap.add_argument("-o", "--output", default="mes_bars.csv")
    ap.add_argument("--bar-minutes", type=int, default=5)
    args = ap.parse_args()

    df = pd.concat([read_databento(f) for f in args.files])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if args.bar_minutes > 1:
        df = df.resample(f"{args.bar_minutes}min").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna(subset=["open"])

    out = df.copy()
    out.insert(0, "timestamp", out.index.strftime("%Y-%m-%d %H:%M:%S"))
    out.to_csv(args.output, index=False)
    print(f"wrote {len(out):,} bars ({out.index[0]} .. {out.index[-1]}) "
          f"to {args.output}")


if __name__ == "__main__":
    main()
