from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from typing import Callable, Protocol

import numpy as np
import pandas as pd

from .config import TradingConfig
from .scanner import TradeCandidate, analyze, refresh_candidate_price


class MarketDataProvider(Protocol):
    def get_bars(self, symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]: ...


class AlpacaMarketDataProvider:
    def __init__(self, api_key: str, secret_key: str):
        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(api_key, secret_key)

    def get_bars(self, symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        start_dt = datetime.combine(date.fromisoformat(start), time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(date.fromisoformat(end), time.max, tzinfo=timezone.utc)
        response = self._client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols,
            start=start_dt,
            end=end_dt,
            timeframe=TimeFrame.Day,
            adjustment=Adjustment.SPLIT,
            feed=DataFeed.IEX,
        ))
        frame = response.df
        histories = {}
        for symbol in symbols:
            try:
                history = frame.xs(symbol, level="symbol").copy()
            except KeyError:
                continue
            history.columns = [str(column).title() for column in history.columns]
            histories[symbol] = history[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return histories


@dataclass
class BacktestTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    stop_price: float
    target_price: float
    exit_reason: str
    realized_pnl: float
    realized_r: float
    strategy_score: int
    setup: str


@dataclass
class BacktestResult:
    trades: list[BacktestTrade]
    equity_curve: pd.Series
    starting_equity: float
    ending_equity: float
    benchmark_return: float | None
    max_drawdown: float
    win_rate: float
    average_r: float
    profit_factor: float


@dataclass
class _Position:
    candidate: TradeCandidate
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    stop_price: float
    target_price: float
    initial_risk: float


def _neutral_sentiment(_symbol: str):
    from .sentiment import SentimentResult

    return SentimentResult(0.0, 0, None, "Technical Trend")


def _historical_strategy(symbol: str, bars: pd.DataFrame, cfg: TradingConfig):
    return analyze(symbol, bars, cfg, sentiment_fn=_neutral_sentiment)


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    data = bars.copy()
    index = pd.DatetimeIndex(pd.to_datetime(data.index))
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    data.index = index.normalize()
    return data.loc[~data.index.duplicated(keep="last")].sort_index()


def run_backtest(
    histories: dict[str, pd.DataFrame],
    cfg: TradingConfig,
    *,
    slippage_bps: float = 5.0,
    commission_per_share: float = 0.0,
    strategy: Callable[[str, pd.DataFrame, TradingConfig], TradeCandidate | None] = _historical_strategy,
    benchmark: pd.DataFrame | None = None,
) -> BacktestResult:
    """Run close-generated signals at the following session's open using daily bars."""
    if slippage_bps < 0 or commission_per_share < 0:
        raise ValueError("Execution costs cannot be negative")

    data = {symbol: _normalize_bars(bars) for symbol, bars in histories.items() if not bars.empty}
    sessions = sorted(set().union(*(set(bars.index) for bars in data.values()))) if data else []
    if not sessions:
        raise ValueError("No historical bars available for the requested backtest")

    slip = slippage_bps / 10_000
    cash = cfg.account_size
    positions: dict[str, _Position] = {}
    pending: list[TradeCandidate] = []
    trades: list[BacktestTrade] = []
    equity_values: list[float] = []
    equity_dates: list[pd.Timestamp] = []
    last_closes: dict[str, tuple[pd.Timestamp, float]] = {}

    def close_position(symbol: str, position: _Position, day: pd.Timestamp, price: float, reason: str):
        nonlocal cash
        exit_price = price
        if reason.startswith("stop") or reason == "end_of_test":
            exit_price *= 1 - slip
        pnl = (exit_price - position.entry_price) * position.shares
        fees = commission_per_share * position.shares * 2
        pnl -= fees
        cash += exit_price * position.shares - commission_per_share * position.shares
        realized_r = pnl / position.initial_risk if position.initial_risk else 0.0
        trades.append(BacktestTrade(
            symbol=symbol,
            entry_date=position.entry_date.date().isoformat(),
            exit_date=day.date().isoformat(),
            entry_price=position.entry_price,
            exit_price=exit_price,
            shares=position.shares,
            stop_price=position.stop_price,
            target_price=position.target_price,
            exit_reason=reason,
            realized_pnl=pnl,
            realized_r=realized_r,
            strategy_score=position.candidate.score,
            setup=position.candidate.setup,
        ))
        del positions[symbol]

    for session_index, day in enumerate(sessions):
        today = {symbol: bars.loc[day] for symbol, bars in data.items() if day in bars.index}

        # Stops and targets crossed by the open execute before new entries compete for capital.
        for symbol, position in list(positions.items()):
            bar = today.get(symbol)
            if bar is None:
                continue
            if float(bar["Open"]) <= position.stop_price:
                close_position(symbol, position, day, float(bar["Open"]), "stop_gap")
            elif float(bar["Open"]) >= position.target_price:
                close_position(symbol, position, day, float(bar["Open"]), "target_gap")

        for candidate in sorted(pending, key=lambda item: (item.prob_gain_10d, item.score, item.reward_risk), reverse=True):
            bar = today.get(candidate.symbol)
            if bar is None or candidate.symbol in positions:
                continue
            if len(positions) >= cfg.max_open_positions:
                break
            fill_price = float(bar["Open"]) * (1 + slip)
            live_cfg = replace(cfg, account_size=max(cash, 0.0))
            sized = refresh_candidate_price(candidate, fill_price, live_cfg)
            if sized is None or sized.shares * fill_price > cash:
                continue
            open_risk = sum(position.initial_risk for position in positions.values())
            if open_risk + sized.risk_dollars > cash * cfg.max_combined_risk_fraction:
                continue
            cash -= sized.shares * fill_price + commission_per_share * sized.shares
            positions[sized.symbol] = _Position(
                candidate=sized,
                entry_date=day,
                entry_price=fill_price,
                shares=sized.shares,
                stop_price=sized.stop,
                target_price=fill_price * (1 + cfg.target_percent / 100),
                initial_risk=sized.risk_dollars,
            )
        pending = []

        # When both barriers are inside a daily candle, assume the adverse stop happened first.
        for symbol, position in list(positions.items()):
            bar = today.get(symbol)
            if bar is None:
                continue
            if float(bar["Low"]) <= position.stop_price:
                close_position(symbol, position, day, position.stop_price, "stop")
            elif float(bar["High"]) >= position.target_price:
                close_position(symbol, position, day, position.target_price, "target")

        if session_index < len(sessions) - 1:
            for symbol, bars in data.items():
                if day not in bars.index:
                    continue
                prefix = bars.loc[:day]
                candidate = strategy(symbol, prefix, cfg)
                if candidate is not None and symbol not in positions:
                    pending.append(candidate)

        last_closes.update({symbol: (day, float(bar["Close"])) for symbol, bar in today.items()})
        marked_equity = cash + sum(
            position.shares * last_closes[symbol][1]
            for symbol, position in positions.items()
            if symbol in last_closes
        )
        equity_values.append(marked_equity)
        equity_dates.append(day)

    final_day = sessions[-1]
    for symbol, position in list(positions.items()):
        last_day, last_close = last_closes[symbol]
        close_position(symbol, position, last_day, last_close, "end_of_test")
    if equity_values:
        equity_values[-1] = cash

    equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates), name="equity")
    drawdown = equity_curve / equity_curve.cummax() - 1
    winners = [trade.realized_pnl for trade in trades if trade.realized_pnl > 0]
    losers = [trade.realized_pnl for trade in trades if trade.realized_pnl < 0]
    gross_loss = abs(sum(losers))
    benchmark_return = None
    if benchmark is not None and not benchmark.empty:
        benchmark_bars = _normalize_bars(benchmark)
        benchmark_return = float(benchmark_bars["Close"].iloc[-1] / benchmark_bars["Open"].iloc[0] - 1)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        starting_equity=cfg.account_size,
        ending_equity=float(cash),
        benchmark_return=benchmark_return,
        max_drawdown=float(drawdown.min()) if not drawdown.empty else 0.0,
        win_rate=len(winners) / len(trades) if trades else 0.0,
        average_r=float(np.mean([trade.realized_r for trade in trades])) if trades else 0.0,
        profit_factor=sum(winners) / gross_loss if gross_loss else (float("inf") if winners else 0.0),
    )


def format_backtest_report(result: BacktestResult, *, symbols: list[str], start: str, end: str,
                           slippage_bps: float) -> str:
    total_return = result.ending_equity / result.starting_equity - 1
    benchmark = "N/A" if result.benchmark_return is None else f"{result.benchmark_return:.2%}"
    profit_factor = "inf" if np.isinf(result.profit_factor) else f"{result.profit_factor:.2f}"
    lines = [
        "# Swing Trader Backtest",
        "",
        f"Period: {start} to {end}",
        f"Symbols: {', '.join(symbols)}",
        "Strategy: existing technical candidate rules; historical sentiment disabled",
        "Data: Alpaca IEX, split-adjusted daily bars",
        "Signal timing: completed daily close; entry: following session open",
        f"Execution: {slippage_bps:g} bps slippage per market-side fill; stop-first if daily barriers both touched",
        "Limit fills: profit targets fill at target when touched; commissions default to $0",
        "Coverage: explicit current-day symbols only; historical universe and delisted symbols are not reconstructed",
        "",
        "## Summary",
        "",
        f"- Starting equity: ${result.starting_equity:,.2f}",
        f"- Ending equity: ${result.ending_equity:,.2f}",
        f"- Total return: {total_return:.2%}",
        f"- SPY buy-and-hold (price return): {benchmark}",
        f"- Maximum drawdown: {result.max_drawdown:.2%}",
        f"- Trades: {len(result.trades)}",
        f"- Win rate: {result.win_rate:.2%}",
        f"- Average realized R: {result.average_r:.2f}",
        f"- Profit factor: {profit_factor}",
        "",
        "## Trade Ledger",
        "",
        "| Symbol | Entry date | Exit date | Shares | Entry | Exit | Reason | P&L | R | Setup |",
        "|---|---|---|---:|---:|---:|---|---:|---:|---|",
    ]
    lines.extend(
        f"| {trade.symbol} | {trade.entry_date} | {trade.exit_date} | {trade.shares} | "
        f"${trade.entry_price:.2f} | ${trade.exit_price:.2f} | {trade.exit_reason} | "
        f"${trade.realized_pnl:.2f} | {trade.realized_r:.2f} | {trade.setup} |"
        for trade in result.trades
    )
    return "\n".join(lines) + "\n"