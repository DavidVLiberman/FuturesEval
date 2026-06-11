#!/usr/bin/env python3
"""Convert NinjaTrader 8 historical data exports into the CSV format the
backtester expects.

NinjaTrader (Tools > Historical Data > Export, type "Minute") writes
semicolon-separated lines in UTC:

    20260311 143000;5982.25;5983.50;5981.75;5983.00;1106

This script converts to America/New_York, optionally resamples 1-minute
bars to 5-minute, and can stitch several quarterly contract files into one
continuous series (at overlaps, the later-listed file wins — pass files in
contract order, oldest first, and export each contract only around the
period it was front month).

Usage:
    python scripts/convert_ninjatrader.py MES_12-25.txt MES_03-26.txt \
        MES_06-26.txt -o mes_bars.csv
    python scripts/run_backtest.py --data csv --csv mes_bars.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from lucid_algo.data import COLUMNS, ET


def read_nt8(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", header=None,
                     names=["ts", "open", "high", "low", "close", "volume"])
    idx = pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S", utc=True)
    df.index = pd.DatetimeIndex(idx).tz_convert(ET)
    return df[COLUMNS].sort_index()


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    out = df.resample(f"{minutes}min").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}
    ).dropna(subset=["open"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+",
                    help="NinjaTrader export .txt files, oldest contract first")
    ap.add_argument("-o", "--output", default="mes_bars.csv")
    ap.add_argument("--bar-minutes", type=int, default=5,
                    help="resample to this bar size (default 5)")
    args = ap.parse_args()

    parts = [read_nt8(f) for f in args.files]
    df = pd.concat(parts)
    # On overlapping timestamps keep the later file (the newer contract).
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if args.bar_minutes > 1:
        df = resample(df, args.bar_minutes)

    out = df.copy()
    out.insert(0, "timestamp", out.index.strftime("%Y-%m-%d %H:%M:%S"))
    out.to_csv(args.output, index=False)
    print(f"wrote {len(out):,} bars ({out.index[0]} .. {out.index[-1]}) "
          f"to {args.output}")


if __name__ == "__main__":
    main()
