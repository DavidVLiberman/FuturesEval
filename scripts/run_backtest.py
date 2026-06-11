#!/usr/bin/env python3
"""Run the Lucid evaluation backtest.

Examples:
    python scripts/run_backtest.py --data synthetic --days 120
    python scripts/run_backtest.py --data yfinance            # ~60d of MES 5m
    python scripts/run_backtest.py --data csv --csv bars.csv  # your own bars
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lucid_algo.backtest import EvaluationBacktester
from lucid_algo.config import Config, RULE_PRESETS
from lucid_algo.data import load_csv, load_yfinance, make_synthetic_mes, rth_only


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "yfinance", "csv"],
                    default="synthetic")
    ap.add_argument("--csv", help="CSV path when --data csv")
    ap.add_argument("--days", type=int, default=120,
                    help="synthetic sessions to generate")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--rules", choices=list(RULE_PRESETS), default="flex25k")
    args = ap.parse_args()

    if args.data == "synthetic":
        bars = make_synthetic_mes(n_days=args.days, seed=args.seed)
        print(f"[synthetic data — pipeline demo only, not a performance estimate]")
    elif args.data == "yfinance":
        bars = rth_only(load_yfinance())
    else:
        if not args.csv:
            ap.error("--csv is required with --data csv")
        bars = rth_only(load_csv(args.csv))

    config = Config(rules=RULE_PRESETS[args.rules])
    result = EvaluationBacktester(config).run(bars)

    print(f"\n=== {config.rules.name} evaluation backtest ===")
    for k, v in result.summary().items():
        print(f"  {k:>15}: {v}")
    print("\nLast 10 trades:")
    for t in result.trades[-10:]:
        print(f"  {t.entry_time:%Y-%m-%d %H:%M} {'L' if t.side==1 else 'S'} "
              f"x{t.contracts} {t.entry:.2f} -> {t.exit:.2f} "
              f"[{t.exit_reason}] {t.pnl:+8.2f} ({t.r_multiple:+.2f}R)")


if __name__ == "__main__":
    main()
