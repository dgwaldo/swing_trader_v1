from dataclasses import dataclass
import pandas as pd

from .config import TradingConfig
from .indicators import add_indicators


@dataclass
class BacktestResult:
    symbol: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_return: float
    max_drawdown: float


def _entry_signal(row, cfg: TradingConfig) -> bool:
    """Mirrors the live scanner: full trend stack, momentum, volume, liquidity."""
    return (
        row["Close"] >= cfg.minimum_price
        and row["Close"] <= cfg.maximum_price
        and row["AVG_VOLUME20"] >= cfg.minimum_average_volume
        and row["Close"] > row["SMA20"]
        and row["SMA20"] > row["SMA50"]
        and row["SMA50"] > row["SMA200"]
        and 50 <= row["RSI"] <= 70
        and row["MACD"] > row["MACD_SIGNAL"]
        and row["VOLUME_RATIO"] >= 1.0
    )


def _position_size(cash: float, entry: float, risk_per_share: float, cfg: TradingConfig) -> int:
    return int(min(
        (cash * cfg.risk_fraction) / risk_per_share,
        (cash * cfg.max_position_fraction) / entry,
    ))


def _simulate_exit(df: pd.DataFrame, i: int, stop: float, target: float) -> tuple[float, int]:
    """Walks forward up to 10 sessions; a stop/low touch is assumed to fill before a target/high touch."""
    end = min(i + 11, len(df))

    for j in range(i + 1, end):
        bar = df.iloc[j]

        if bar["Low"] <= stop:
            return stop, j

        if bar["High"] >= target:
            return target, j

    exit_index = end - 1
    return float(df.iloc[exit_index]["Close"]), exit_index


def run(symbol: str, raw: pd.DataFrame, cfg: TradingConfig) -> BacktestResult:
    df = add_indicators(raw).dropna().copy()

    cash = cfg.account_size
    equity_curve = []
    trades = 0
    wins = 0
    losses = 0

    i = 1
    while i < len(df) - 1:
        today = df.iloc[i]
        tomorrow = df.iloc[i + 1]

        if not _entry_signal(today, cfg):
            equity_curve.append(cash)
            i += 1
            continue

        entry = float(tomorrow["Open"])
        stop = entry - float(today["ATR"]) * cfg.stop_atr_multiple
        risk_per_share = entry - stop

        if risk_per_share <= 0:
            equity_curve.append(cash)
            i += 1
            continue

        shares = _position_size(cash, entry, risk_per_share, cfg)

        if shares < 1:
            equity_curve.append(cash)
            i += 1
            continue

        target = entry * (1 + cfg.target_percent / 100.0)
        exit_price, exit_index = _simulate_exit(df, i, stop, target)

        pnl = (exit_price - entry) * shares
        cash += pnl
        trades += 1

        if pnl > 0:
            wins += 1
        else:
            losses += 1

        # Hold cash flat until the position closes so trades never overlap.
        equity_curve.extend([cash] * (exit_index - i + 1))
        i = exit_index + 1

    if not equity_curve:
        equity_curve = [cash]

    curve = pd.Series(equity_curve, dtype=float)
    running_max = curve.cummax()
    drawdown = (curve - running_max) / running_max

    return BacktestResult(
        symbol=symbol,
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=(wins / trades * 100) if trades else 0,
        total_return=((cash / cfg.account_size) - 1) * 100,
        max_drawdown=abs(float(drawdown.min())) * 100,
    )
