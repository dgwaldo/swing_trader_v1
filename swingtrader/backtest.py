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


def run(symbol: str, raw: pd.DataFrame, cfg: TradingConfig) -> BacktestResult:
    df = add_indicators(raw).dropna().copy()

    cash = cfg.account_size
    equity_curve = []
    trades = 0
    wins = 0
    losses = 0

    for i in range(1, len(df) - 1):
        today = df.iloc[i]
        tomorrow = df.iloc[i + 1]

        # Conservative entry signal: trend + momentum + volume.
        signal = (
            today["Close"] > today["SMA20"]
            and today["SMA20"] > today["SMA50"]
            and today["RSI"] >= 50
            and today["RSI"] <= 70
            and today["MACD"] > today["MACD_SIGNAL"]
            and today["VOLUME_RATIO"] >= 1.0
        )

        if not signal:
            equity_curve.append(cash)
            continue

        entry = float(tomorrow["Open"])
        stop = entry - float(today["ATR"]) * cfg.stop_atr_multiple
        risk_per_share = entry - stop

        if risk_per_share <= 0:
            equity_curve.append(cash)
            continue

        shares = int(min(
            (cash * cfg.risk_fraction) / risk_per_share,
            (cash * cfg.max_position_fraction) / entry,
        ))

        if shares < 1:
            equity_curve.append(cash)
            continue

        target = entry + risk_per_share * cfg.target_rr

        # V1 holds for up to 10 trading sessions.
        exit_price = None
        end = min(i + 11, len(df))

        for j in range(i + 1, end):
            bar = df.iloc[j]

            # If both are hit in one candle, assume stop first.
            if bar["Low"] <= stop:
                exit_price = stop
                break

            if bar["High"] >= target:
                exit_price = target
                break

        if exit_price is None:
            exit_price = float(df.iloc[end - 1]["Close"])

        pnl = (exit_price - entry) * shares
        cash += pnl
        trades += 1

        if pnl > 0:
            wins += 1
        else:
            losses += 1

        equity_curve.append(cash)

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
