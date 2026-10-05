import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .backtest import BacktestResult


DEFAULT_BACKTEST_DB = Path("data") / "backtests.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    symbols_json TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    slippage_bps REAL NOT NULL,
    spread_bps REAL NOT NULL,
    commission_per_share REAL NOT NULL,
    target_fill_mode TEXT NOT NULL,
    target_trade_through_bps REAL NOT NULL,
    starting_equity REAL NOT NULL,
    ending_equity REAL NOT NULL,
    total_return REAL NOT NULL,
    cagr REAL,
    benchmark_return REAL,
    benchmark_alpha REAL,
    max_drawdown REAL NOT NULL,
    annualized_volatility REAL NOT NULL,
    sharpe_ratio REAL,
    sortino_ratio REAL,
    calmar_ratio REAL,
    trade_count INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    average_win REAL,
    average_loss REAL,
    median_win REAL,
    median_loss REAL,
    expectancy_dollars REAL NOT NULL,
    average_r REAL NOT NULL,
    profit_factor REAL NOT NULL,
    max_consecutive_losses INTEGER NOT NULL,
    average_holding_sessions REAL NOT NULL,
    average_gross_exposure REAL NOT NULL DEFAULT 0,
    turnover REAL NOT NULL DEFAULT 0,
    market_regime_json TEXT NOT NULL DEFAULT '{}',
    sector_mapping_json TEXT NOT NULL DEFAULT '{}',
    sector_exposure_json TEXT NOT NULL DEFAULT '{}',
    average_pairwise_correlation REAL,
    max_pairwise_correlation REAL,
    sector_etf_mapping_json TEXT NOT NULL DEFAULT '{}',
    average_relative_strength_vs_market REAL,
    average_relative_strength_vs_sector REAL
);
CREATE TABLE IF NOT EXISTS backtest_trades (
    run_id TEXT NOT NULL,
    trade_number INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    symbol TEXT NOT NULL,
    sector TEXT NOT NULL DEFAULT 'Unclassified',
    sector_etf TEXT,
    market_regime TEXT NOT NULL DEFAULT 'UNKNOWN',
    stock_return_20d REAL,
    market_return_20d REAL,
    sector_return_20d REAL,
    relative_strength_vs_market REAL,
    relative_strength_vs_sector REAL,
    entry_date TEXT NOT NULL,
    exit_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    holding_sessions INTEGER NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    realized_pnl REAL NOT NULL,
    realized_r REAL NOT NULL,
    strategy_score INTEGER NOT NULL,
    setup TEXT NOT NULL,
    PRIMARY KEY (run_id, trade_number),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_symbol_entry
    ON backtest_trades(symbol, entry_date);
CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    equity REAL NOT NULL,
    PRIMARY KEY (run_id, session_date),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
);
"""


def persist_sensitivity_results(
    results: list[tuple[float, BacktestResult]],
    *,
    symbols: list[str],
    start: str,
    end: str,
    slippage_bps: float,
    commission_per_share: float,
    target_fill_mode: str,
    target_trade_through_bps: float,
    path: str | Path = DEFAULT_BACKTEST_DB,
) -> list[str]:
    if not results:
        raise ValueError("At least one backtest result is required")

    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    run_ids = []

    with closing(sqlite3.connect(database_path)) as database:
        database.execute("PRAGMA foreign_keys = ON")
        database.executescript(_SCHEMA)
        _ensure_column(database, "backtest_runs", "average_gross_exposure", "REAL NOT NULL DEFAULT 0")
        _ensure_column(database, "backtest_runs", "turnover", "REAL NOT NULL DEFAULT 0")
        _ensure_column(database, "backtest_runs", "market_regime_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(database, "backtest_runs", "sector_mapping_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(database, "backtest_runs", "sector_exposure_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(database, "backtest_runs", "average_pairwise_correlation", "REAL")
        _ensure_column(database, "backtest_runs", "max_pairwise_correlation", "REAL")
        _ensure_column(database, "backtest_runs", "sector_etf_mapping_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(database, "backtest_runs", "average_relative_strength_vs_market", "REAL")
        _ensure_column(database, "backtest_runs", "average_relative_strength_vs_sector", "REAL")
        _ensure_column(database, "backtest_trades", "sector", "TEXT NOT NULL DEFAULT 'Unclassified'")
        _ensure_column(database, "backtest_trades", "sector_etf", "TEXT")
        _ensure_column(database, "backtest_trades", "market_regime", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
        _ensure_column(database, "backtest_trades", "stock_return_20d", "REAL")
        _ensure_column(database, "backtest_trades", "market_return_20d", "REAL")
        _ensure_column(database, "backtest_trades", "sector_return_20d", "REAL")
        _ensure_column(database, "backtest_trades", "relative_strength_vs_market", "REAL")
        _ensure_column(database, "backtest_trades", "relative_strength_vs_sector", "REAL")
        database.execute("PRAGMA user_version = 3")
        with database:
            for spread_bps, result in results:
                run_id = str(uuid4())
                run_ids.append(run_id)
                database.execute(
                    """INSERT INTO backtest_runs (
                        run_id, created_at, strategy_id, strategy_version, configuration_hash,
                        configuration_json, symbols_json, start_date, end_date, slippage_bps,
                        spread_bps, commission_per_share, target_fill_mode, target_trade_through_bps,
                        starting_equity, ending_equity, total_return, cagr, benchmark_return,
                        benchmark_alpha, max_drawdown, annualized_volatility, sharpe_ratio,
                        sortino_ratio, calmar_ratio, trade_count, win_rate, average_win,
                        average_loss, median_win, median_loss, expectancy_dollars, average_r,
                        profit_factor, max_consecutive_losses, average_holding_sessions,
                        average_gross_exposure, turnover, market_regime_json, sector_mapping_json,
                        sector_exposure_json, average_pairwise_correlation, max_pairwise_correlation,
                        sector_etf_mapping_json, average_relative_strength_vs_market,
                        average_relative_strength_vs_sector
                    ) VALUES (
                        :run_id, :created_at, :strategy_id, :strategy_version, :configuration_hash,
                        :configuration_json, :symbols_json, :start_date, :end_date, :slippage_bps,
                        :spread_bps, :commission_per_share, :target_fill_mode, :target_trade_through_bps,
                        :starting_equity, :ending_equity, :total_return, :cagr, :benchmark_return,
                        :benchmark_alpha, :max_drawdown, :annualized_volatility, :sharpe_ratio,
                        :sortino_ratio, :calmar_ratio, :trade_count, :win_rate, :average_win,
                        :average_loss, :median_win, :median_loss, :expectancy_dollars, :average_r,
                        :profit_factor, :max_consecutive_losses, :average_holding_sessions,
                        :average_gross_exposure, :turnover, :market_regime_json, :sector_mapping_json,
                        :sector_exposure_json, :average_pairwise_correlation, :max_pairwise_correlation,
                        :sector_etf_mapping_json, :average_relative_strength_vs_market,
                        :average_relative_strength_vs_sector
                    )""",
                    {
                        "run_id": run_id,
                        "created_at": created_at,
                        "strategy_id": result.strategy_id,
                        "strategy_version": result.strategy_version,
                        "configuration_hash": result.configuration_hash,
                        "configuration_json": json.dumps(result.configuration, sort_keys=True, separators=(",", ":"), allow_nan=False),
                        "symbols_json": json.dumps(symbols, separators=(",", ":")),
                        "start_date": start,
                        "end_date": end,
                        "slippage_bps": slippage_bps,
                        "spread_bps": spread_bps,
                        "commission_per_share": commission_per_share,
                        "target_fill_mode": target_fill_mode,
                        "target_trade_through_bps": target_trade_through_bps,
                        "starting_equity": result.starting_equity,
                        "ending_equity": result.ending_equity,
                        "total_return": result.ending_equity / result.starting_equity - 1,
                        "cagr": result.cagr,
                        "benchmark_return": result.benchmark_return,
                        "benchmark_alpha": result.benchmark_alpha,
                        "max_drawdown": result.max_drawdown,
                        "annualized_volatility": result.annualized_volatility,
                        "sharpe_ratio": result.sharpe_ratio,
                        "sortino_ratio": result.sortino_ratio,
                        "calmar_ratio": result.calmar_ratio,
                        "trade_count": len(result.trades),
                        "win_rate": result.win_rate,
                        "average_win": result.average_win,
                        "average_loss": result.average_loss,
                        "median_win": result.median_win,
                        "median_loss": result.median_loss,
                        "expectancy_dollars": result.expectancy_dollars,
                        "average_r": result.average_r,
                        "profit_factor": result.profit_factor,
                        "max_consecutive_losses": result.max_consecutive_losses,
                        "average_holding_sessions": result.average_holding_sessions,
                        "average_gross_exposure": result.average_gross_exposure,
                        "turnover": result.turnover,
                        "market_regime_json": json.dumps(
                            {
                                regime: {
                                    **metrics,
                                    "profit_factor": "inf" if math.isinf(metrics["profit_factor"]) else metrics["profit_factor"],
                                }
                                for regime, metrics in result.market_regime_metrics.items()
                            },
                            sort_keys=True,
                            allow_nan=False,
                        ),
                        "sector_mapping_json": json.dumps(result.sector_mapping, sort_keys=True, allow_nan=False),
                        "sector_exposure_json": json.dumps(result.sector_exposure_metrics, sort_keys=True, allow_nan=False),
                        "average_pairwise_correlation": result.average_pairwise_correlation,
                        "max_pairwise_correlation": result.max_pairwise_correlation,
                        "sector_etf_mapping_json": json.dumps(result.sector_etf_mapping, sort_keys=True, allow_nan=False),
                        "average_relative_strength_vs_market": result.average_relative_strength_vs_market,
                        "average_relative_strength_vs_sector": result.average_relative_strength_vs_sector,
                    },
                )
                database.executemany(
                    """INSERT INTO backtest_trades (
                        run_id, trade_number, strategy_id, strategy_version, configuration_hash,
                        symbol, sector, sector_etf, market_regime, stock_return_20d, market_return_20d,
                        sector_return_20d, relative_strength_vs_market, relative_strength_vs_sector,
                        entry_date, exit_date, entry_price, exit_price,
                        shares, holding_sessions, stop_price, target_price, exit_reason,
                        realized_pnl, realized_r, strategy_score, setup
                    ) VALUES (
                        :run_id, :trade_number, :strategy_id, :strategy_version, :configuration_hash,
                        :symbol, :sector, :sector_etf, :market_regime, :stock_return_20d, :market_return_20d,
                        :sector_return_20d, :relative_strength_vs_market, :relative_strength_vs_sector,
                        :entry_date, :exit_date, :entry_price, :exit_price, :shares,
                        :holding_sessions, :stop_price, :target_price, :exit_reason, :realized_pnl,
                        :realized_r, :strategy_score, :setup
                    )""",
                    [
                        {
                            "run_id": run_id,
                            "trade_number": trade_number,
                            **trade.__dict__,
                        }
                        for trade_number, trade in enumerate(result.trades, start=1)
                    ],
                )
                database.executemany(
                    "INSERT INTO backtest_equity (run_id, session_date, equity) VALUES (?, ?, ?)",
                    [
                        (run_id, session_date.date().isoformat(), float(equity))
                        for session_date, equity in result.equity_curve.items()
                    ],
                )

    return run_ids


def _ensure_column(database: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in database.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        database.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
