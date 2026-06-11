#!/usr/bin/env python3
"""Sweep risk-per-trade and report evaluation pass probabilities.

    python scripts/run_montecarlo.py
    python scripts/run_montecarlo.py --win-rate 0.45 --avg-win-r 1.4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lucid_algo.config import Config, RULE_PRESETS
from lucid_algo.montecarlo import MCParams, sweep_risk


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", choices=list(RULE_PRESETS), default="flex25k")
    ap.add_argument("--win-rate", type=float, default=0.42)
    ap.add_argument("--avg-win-r", type=float, default=1.45)
    ap.add_argument("--avg-loss-r", type=float, default=0.95)
    ap.add_argument("--sims", type=int, default=20_000)
    args = ap.parse_args()

    config = Config(rules=RULE_PRESETS[args.rules])
    params = MCParams(win_rate=args.win_rate, avg_win_r=args.avg_win_r,
                      avg_loss_r=args.avg_loss_r, n_sims=args.sims)

    print(f"=== {config.rules.name} — pass probability vs risk per trade ===")
    print(f"assumed edge: {params.win_rate:.0%} win rate, "
          f"+{params.avg_win_r}R avg win, -{params.avg_loss_r}R avg loss\n")
    for res in sweep_risk(config, params):
        print(res)
    print("\nNotes:")
    print("- 'timeout' = not passed within the 90-day simulation horizon. "
          "Lucid has no evaluation time limit, so those runs simply continue.")
    print("- Expectancy must be positive for any risk size to work. "
          "Validate the edge on real data before trusting these numbers.")


if __name__ == "__main__":
    main()
