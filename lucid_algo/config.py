"""Configuration: Lucid Trading account rules, instrument specs, strategy
and risk parameters.

Rule numbers below were verified against Lucid Trading's published rules in
June 2026. Prop firms change rules frequently — re-verify against
https://support.lucidtrading.com before trading and update these presets.
"""

from dataclasses import dataclass, field
from datetime import time


# ---------------------------------------------------------------------------
# Instrument specifications
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    point_value: float        # USD per full point, per contract
    tick_size: float
    commission_per_side: float  # USD per contract per side (incl. fees)
    slippage_ticks: float       # assumed slippage per side, in ticks
    is_micro: bool

    @property
    def tick_value(self) -> float:
        return self.point_value * self.tick_size

    def slippage_usd(self) -> float:
        """Round-trip slippage cost per contract in USD."""
        return 2.0 * self.slippage_ticks * self.tick_value

    def round_trip_cost(self) -> float:
        """Commission + slippage per contract, round trip, USD."""
        return 2.0 * self.commission_per_side + self.slippage_usd()


CONTRACTS = {
    "MES": ContractSpec("MES", 5.0, 0.25, 0.74, 1.0, True),
    "ES": ContractSpec("ES", 50.0, 0.25, 2.10, 1.0, False),
    "MNQ": ContractSpec("MNQ", 2.0, 0.25, 0.74, 1.0, True),
    "NQ": ContractSpec("NQ", 20.0, 0.25, 2.10, 1.0, False),
}


# ---------------------------------------------------------------------------
# Lucid account rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LucidRules:
    """Rule set for one Lucid evaluation account."""
    name: str
    account_size: float
    profit_target: float
    max_loss_limit: float          # trailing drawdown amount
    eod_trailing: bool             # True: MLL recomputed only at session close
    intraday_breach_fails: bool    # False: breach only checked at EOD close
    daily_loss_limit: float | None  # None = no firm-imposed daily loss limit
    consistency_pct: float | None  # best day must be <= pct * total profit
    min_trading_days: int
    max_minis: int                 # contract cap in mini-equivalents
    micros_per_mini: int
    flat_by_et: time               # firm auto-flattens at this time (ET)
    mll_lock_buffer: float         # MLL stops trailing at start balance + buffer

    @property
    def max_micros(self) -> int:
        return self.max_minis * self.micros_per_mini


# LucidFlex $25K evaluation (verified June 2026):
#   $1,250 profit target, $1,500 end-of-day trailing Max Loss Limit,
#   no daily loss limit, 50% consistency rule (evaluation only),
#   2 minimum trading days, no time limit, 2 minis / 20 micros,
#   flat by 4:45 PM ET, automation allowed.
LUCID_FLEX_25K = LucidRules(
    name="LucidFlex 25K",
    account_size=25_000.0,
    profit_target=1_250.0,
    max_loss_limit=1_500.0,
    eod_trailing=True,
    intraday_breach_fails=False,
    daily_loss_limit=None,
    consistency_pct=0.50,
    min_trading_days=2,
    max_minis=2,
    micros_per_mini=10,
    flat_by_et=time(16, 45),
    mll_lock_buffer=100.0,
)

# LucidPro $25K evaluation: same EOD trailing structure; consistency applies
# at the funded stage (40%) rather than in the evaluation. Update from the
# help center before relying on this preset.
LUCID_PRO_25K = LucidRules(
    name="LucidPro 25K",
    account_size=25_000.0,
    profit_target=1_500.0,
    max_loss_limit=1_500.0,
    eod_trailing=True,
    intraday_breach_fails=False,
    daily_loss_limit=None,
    consistency_pct=None,
    min_trading_days=2,
    max_minis=2,
    micros_per_mini=10,
    flat_by_et=time(16, 45),
    mll_lock_buffer=100.0,
)

RULE_PRESETS = {
    "flex25k": LUCID_FLEX_25K,
    "pro25k": LUCID_PRO_25K,
}


# ---------------------------------------------------------------------------
# Risk parameters (self-imposed — tighter than the firm's rules on purpose)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 125.0     # USD risked per trade at the stop
    max_trades_per_day: int = 2
    daily_loss_stop: float = 250.0    # self-imposed daily stop (USD, positive)
    daily_profit_lock: float = 550.0  # stop trading for the day at this gain
    # When (balance - MLL) falls below these buffers, throttle size:
    half_size_buffer: float = 700.0   # halve risk per trade
    min_size_buffer: float = 350.0    # trade 1 micro only
    # Best-day cap as a fraction of the consistency limit, applied once the
    # account is in profit. Keeps the largest day comfortably inside 50%.
    consistency_headroom: float = 0.90


@dataclass(frozen=True)
class StrategyConfig:
    symbol: str = "MES"
    # Opening range is measured from session open over this many minutes.
    opening_range_minutes: int = 15
    bar_minutes: int = 5
    # No new entries after this ET time (avoid lunch chop / late-day noise).
    entry_cutoff_et: time = time(12, 0)
    # Exit all positions at this ET time (well before Lucid's 16:45 flatten).
    session_exit_et: time = time(15, 55)
    reward_risk: float = 1.5          # target = entry + RR * stop distance
    move_stop_to_breakeven_at_r: float | None = 1.0
    # Skip the day if the opening range is wider than this multiple of the
    # 14-day average session range (stop too far to size sensibly).
    max_or_to_avg_range: float = 0.65
    # Skip if the opening range is narrower than this many ticks (noise).
    min_or_ticks: int = 8
    one_trade_per_direction: bool = True


@dataclass(frozen=True)
class Config:
    rules: LucidRules = LUCID_FLEX_25K
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    @property
    def contract(self) -> ContractSpec:
        return CONTRACTS[self.strategy.symbol]
