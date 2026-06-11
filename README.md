# Lucid 25K Evaluation Algorithm

A rules-aware trading system built specifically for the **Lucid Trading $25K
evaluation** (LucidFlex preset by default). It pairs a simple, well-studied
entry model (opening range breakout on MES micros) with a prop-firm risk
engine that encodes every Lucid rule, plus a backtester that simulates the
evaluation itself and a Monte Carlo tool that tells you how much to risk
per trade.

> **The honest premise:** passing a prop evaluation is mostly a *risk
> geometry* problem, not an entry-signal problem. The $1,500 end-of-day
> trailing drawdown is the constraint that kills most accounts. This repo
> treats the risk engine as the product and the entry model as a
> replaceable module.

---

## Lucid rules encoded (verified June 2026)

| Rule | LucidFlex 25K | Where enforced |
|---|---|---|
| Profit target | $1,250 | `risk.evaluation_passed()` |
| Max Loss Limit | $1,500 **end-of-day trailing**, locks at start + $100 | `risk.end_day()` |
| Daily loss limit | None (firm) → **$250 self-imposed** | `RiskConfig.daily_loss_stop` |
| Consistency | Best day ≤ 50% of total profit (eval only) | daily profit lock + pass check |
| Min trading days | 2 | `risk.evaluation_passed()` |
| Contracts | 2 minis / 20 micros (mixable) | `risk.position_size()` |
| Flat by | 4:45 PM ET (auto-flatten) | strategy exits 3:55 PM ET |
| Time limit | None | — |
| Automation | Allowed (trader responsible) | — |

⚠️ Prop firms change rules often — Lucid changed its consistency rule and
payout split within the last year. **Re-verify at
[support.lucidtrading.com](https://support.lucidtrading.com) before every
evaluation** and update `lucid_algo/config.py` presets if needed.

## How the system is tailored to these rules

1. **EOD trailing drawdown → defend the close, not the tick.** Lucid only
   checks the Max Loss Limit at the 4:45 PM close, and the MLL only
   ratchets up at the close. The risk engine tracks the live MLL, blocks
   any trade whose full stop-out would leave the close too near it, and
   throttles size: half risk when the buffer < $700, one micro below
   $350, no trades when a stop-out could threaten the buffer.
2. **50% consistency rule → daily profit lock at +$550.** With a $1,250
   target, a $550 best day is at most 44% of the final total, so you can
   never "pass except for consistency." The lock also banks green days.
3. **No daily loss limit → impose your own.** A $250 daily stop means
   six maximum-loss days are needed to bust the account — and the EOD
   trailing math means early losses are the most dangerous, so the first
   days matter most.
4. **2 minis / 20 micros cap → trade MES micros and size to dollars.**
   Position size = risk-per-trade ÷ (stop distance × $5), capped at 20.
   Micros give 10× finer sizing granularity than minis — the single
   biggest practical edge for staying inside a $1,500 drawdown.
5. **Flat by 4:45 PM ET → all exits by 3:55 PM ET**, an hour of margin
   against the auto-flatten (which fills at the firm's mercy, not yours).

## The entry model: Opening Range Breakout (ORB)

- Opening range = 9:30–9:45 AM ET high/low on 5-minute MES bars.
- Long on a 5m close above the OR high; short on a close below the OR
  low. One attempt per direction, max 2 trades/day, no entries after
  noon ET.
- Stop at the opposite side of the range, breakeven at +1R, target +1.5R,
  time exit 3:55 PM ET.
- Day filters: skip when the opening range is wider than 65% of the
  14-day average session range (stop too wide to size) or under 8 ticks.

Why ORB: defined risk *before* entry (so sizing is exact), few trades per
day (suits the consistency rule and daily stop), morning-session only
(best liquidity, flat hours before the deadline), and it's one of the
few intraday patterns with published supporting research (Zarattini &
Aziz, 2023-24). It is **not** guaranteed to be profitable — validate on
real data before paying for an evaluation.

## What the numbers say (Monte Carlo)

Assuming a modest realistic edge (42% win rate, +1.45R / −0.95R after
costs, ≤2 trades/day), 20,000 simulated evaluations:

```
risk $ 75:  pass 33%   fail 0.2%   median 54 days to pass
risk $125:  pass 53%   fail 0.6%   median 34 days
risk $150:  pass 54%   fail 0.5%   median 26 days   ← sweet spot
risk $250:  pass 41%   fail 1.1%   median 14 days
```

Two lessons: (1) the EOD trailing drawdown + throttling makes outright
failure rare at sane sizes — the realistic bad outcome is *taking a long
time*, which is fine since Lucid has no time limit; (2) oversizing
lowers the pass rate even though it shortens the fast paths. **$125–150
risk per trade (~0.5–0.6% of account, 8–10% of the drawdown) is the
optimum for this edge profile.** Re-run the sweep with your own measured
stats: `python scripts/run_montecarlo.py --win-rate 0.45 --avg-win-r 1.4`

## Quick start

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                  # 16 tests
python scripts/run_backtest.py              # synthetic-data pipeline demo
python scripts/run_backtest.py --data yfinance   # ~60 days of real MES 5m
python scripts/run_backtest.py --data csv --csv your_mes_bars.csv
python scripts/run_montecarlo.py            # risk-per-trade sweep
```

For real validation, export 1–5 minute MES bars (6+ months) from
NinjaTrader / Tradovate / TradingView to CSV with columns
`timestamp,open,high,low,close,volume` and use `--data csv`.

NinjaTrader users: export each quarterly contract via Tools → Historical
Data → Export (type "Minute"), then convert and stitch in one step:

```bash
python scripts/convert_ninjatrader.py MES_12-25.txt MES_03-26.txt MES_06-26.txt -o mes_bars.csv
python scripts/run_backtest.py --data csv --csv mes_bars.csv
```

## Repo layout

```
lucid_algo/
  config.py      # Lucid rule presets (Flex/Pro 25K), risk + strategy params
  risk.py        # PropRiskManager: MLL trailing, gating, sizing, pass/fail
  strategy.py    # ORB entry model (replaceable)
  backtest.py    # event-driven backtester that simulates the evaluation
  montecarlo.py  # pass-probability simulator + risk sweep
  data.py        # yfinance / CSV loaders + synthetic MES generator
scripts/         # run_backtest.py, run_montecarlo.py
tests/           # risk-rule unit tests + end-to-end tests
```

## Going live

Lucid allows automation. Two practical routes:

1. **Semi-automated (recommended to start):** trade the signals manually
   on Lucid's platform; run the risk engine beside you as the gatekeeper
   (it answers "may I take this trade and at what size?").
2. **Fully automated:** port the strategy to your execution platform
   (NinjaTrader strategy, or TradingView alerts → TradersPost →
   Tradovate). Keep the risk-engine logic in the execution layer — the
   gating rules in `risk.py` translate line-for-line.

### Execution checklist (the part that actually decides the outcome)

- [ ] Re-verify Lucid's current rules the day you activate
- [ ] Validate the edge on ≥6 months of real MES intraday data first
- [ ] Risk $125–150/trade; never increase size to "catch up"
- [ ] Hard daily stop −$250; hard daily lock +$550; then *close the platform*
- [ ] Trade mornings only; flat by 3:55 PM ET, every day, no exceptions
- [ ] Skip days: FOMC days, CPI mornings until 10:00 ET, half-days
- [ ] Track every trade; re-run the Monte Carlo monthly with your real stats
- [ ] Remember the min-2-trading-days rule — you can't pass in one day anyway,
      so there is no prize for rushing

## Disclaimers

Futures trading involves substantial risk of loss. Nothing here is
financial advice, and no backtest or simulation guarantees future
results. The synthetic data generator is for pipeline testing only — it
contains no real market edge. Evaluation fees are the cost of variance:
budget for more than one attempt.
