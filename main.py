import argparse
import importlib.util
import json
import math
import sys
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from zoneinfo import ZoneInfo

from swingtrader.config import TradingConfig, load_trading_config
from swingtrader.data import (
    download_batch,
    download_latest_prices,
    fetch_market_candidates,
)
from swingtrader.scanner import analyze, refresh_candidate_price


FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "AVGO", "AMD", "NFLX", "TSLA", "PLTR", "ORCL",
    "CRM", "MU", "QCOM", "COST", "JPM", "XOM",
    "SOFI", "F", "PFE", "INTC", "SNAP", "RIVN",
    "NU", "VALE", "T", "BAC", "GM", "CCL", "NCLH", "DKNG",
    "HOOD", "MARA", "RKLB", "SIRI", "TGT", "AES", "ABUS", "AGRO", "AESI"
]

REPORT_DIR = Path("data") / "scans"
BOT_LOCK_PATH = Path("data") / "paper_bot.lock"


class ReportOutput:
    def __init__(self, console, report):
        self.console = console
        self.report = report

    def write(self, text: str) -> int:
        written = self.console.write(text)
        self.report.write(text)
        return written

    def flush(self):
        self.console.flush()
        self.report.flush()


def build_config(
    minimum_price=None,
    maximum_price=None,
):
    cfg = load_trading_config()
    minimum_price = cfg.minimum_price if minimum_price is None else minimum_price
    maximum_price = cfg.maximum_price if maximum_price is None else maximum_price

    if minimum_price > maximum_price:
        raise ValueError("Minimum price cannot be greater than maximum price.")

    return replace(
        cfg,
        minimum_price=minimum_price,
        maximum_price=maximum_price,
    )


def save_scan(candidates, symbols_scanned, output_path):
    payload = {
        "scan_date": date.today().isoformat(),
        "total_symbols_scanned": len(symbols_scanned),
        "total_candidates": len(candidates),
        "candidates": [asdict(c) for c in candidates],
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved scan snapshot to {path}")


def print_candidates_table(candidates, cfg=None):
    cfg = cfg or TradingConfig()
    target_pct_label = f"+{cfg.target_percent:g}% Target"
    print("\n## Top Swing-Trading Candidates (Ranked by Highest Setup Probability)\n")
    print(
        f"| Rank | Symbol | Score | P(+10%) | P(-5%) | Risk | Latest | Move vs Close | Stop | {target_pct_label} | Target 1 (+8-10%) | Target 2 (+16-20%) | Shares | Max Risk | Catalyst | Why Attractive |"
    )
    print("|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")

    for rank, c in enumerate(candidates, start=1):
        reasons = "; ".join(c.reasons)
        move = f"{c.price_change_pct:+.1%}" if c.price_change_pct is not None else "N/A"
        print(
            f"| {rank} | **{c.symbol}** | {c.score}/100 | **{c.prob_gain_10d:.1%}** | {c.prob_loss_5d:.1%} | {c.risk_rating} | "
            f"${c.entry:.2f} | {move} | ${c.stop:.2f} | **${c.target_1pct:.2f}** | ${c.target_1:.2f} | ${c.target_2:.2f} | {c.shares} | "
            f"${c.risk_dollars:.2f} | {c.main_catalyst} | {reasons} |"
        )


def run_scanner(symbols=None, output_path=None, top=25, cfg=None):
    """Run the consistent swing trading scanner across NYSE & NASDAQ candidates."""
    cfg = cfg or TradingConfig()

    if not symbols:
        symbols = fetch_market_candidates(
            minimum_price=cfg.minimum_price,
            maximum_price=cfg.maximum_price,
            minimum_volume=cfg.minimum_average_volume,
        ) or FALLBACK_SYMBOLS

    print(f"Ingesting 1-year OHLCV bars for {len(symbols)} liquid ${cfg.minimum_price:.2f}-${cfg.maximum_price:.2f} candidates...")
    historical_data = download_batch(symbols, period="1y", batch_size=80)

    candidates = []
    for symbol in symbols:
        data = historical_data.get(symbol)
        if data is None or data.empty:
            continue
        try:
            candidate = analyze(symbol, data, cfg)
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            print(f"  Error analyzing {symbol}: {exc}")

    if candidates:
        latest_prices = download_latest_prices([candidate.symbol for candidate in candidates])
        refreshed = []
        for candidate in candidates:
            latest_price = latest_prices.get(candidate.symbol)
            if latest_price is None:
                refreshed.append(candidate)
                continue
            repriced = refresh_candidate_price(candidate, latest_price, cfg)
            if repriced:
                refreshed.append(repriced)
        candidates = refreshed
        print(f"Updated {len(latest_prices)} candidates with latest extended-hours prices.")

    # Rank strictly from highest probability to lowest (with score tie-breaker)
    candidates.sort(key=lambda x: (x.prob_gain_10d, x.score, x.reward_risk), reverse=True)

    if top and top > 0:
        candidates = candidates[:top]

    if output_path is None:
        output_path = Path("data") / "scans" / f"scan_{date.today().isoformat()}.json"

    save_scan(candidates, symbols, output_path)

    if not candidates:
        print("\nNo candidates met the full swing criteria today.")
        return []

    print_candidates_table(candidates, cfg=cfg)
    return candidates


def run_paper_bot_cycle(cfg, symbols=None, top=25, *, scan=run_scanner, now=None):
    from swingtrader.paper_trading import (
        bot_attempted_today, bot_capacity, check_bot_daily_halt, paper_bot_snapshot, record_paper_fills,
        review_paper_orders,
        submit_paper_candidate,
    )

    now = now or datetime.now(timezone.utc)
    trading, orders = paper_bot_snapshot()
    new_fills = record_paper_fills(orders)
    if new_fills:
        print(f"Recorded {new_fills} paper fills in the local ledger")
    messages = review_paper_orders(trading, orders, now, cancel_stale=True)
    for message in messages:
        print(message)
    if any("manual review" in message or "cancellation requested" in message for message in messages):
        print("Paper bot paused: verify orders before any new entries")
        return False
    account = trading.get_account()
    positions = trading.get_all_positions()
    try:
        check_bot_daily_halt(account, cfg, now)
        capacity = bot_capacity(account, positions, orders, cfg, now)
    except ValueError as exc:
        print(f"Paper bot paused: {exc}")
        return False
    if scan is None:
        return False
    local_time = now.astimezone(ZoneInfo("America/New_York"))
    if not getattr(trading.get_clock(), "is_open", False) or not (10, 0) <= (local_time.hour, local_time.minute) < (15, 30):
        return False
    if capacity.slots <= 0 or capacity.remaining_risk <= 0:
        print("Paper bot at position or planned-risk limit")
        return False

    candidates = scan(symbols=symbols, top=top, cfg=cfg)
    for candidate in candidates:
        if candidate.symbol in capacity.symbols or bot_attempted_today(orders, candidate.symbol, now):
            continue
        try:
            plan = submit_paper_candidate(candidate, cfg, execute=True, bot_mode=True)
            print(f"Paper bot: {plan.symbol} buy {plan.shares} @ <= ${plan.limit_price:.2f}; "
                  f"stop ${plan.stop_price:.2f}; target ${plan.target_price:.2f}")
        except ValueError as exc:
            print(f"Paper bot skipped {candidate.symbol}: {exc}")
    return True


def run_paper_bot(cfg, symbols=None, top=25, *, once=False):
    if importlib.util.find_spec("alpaca") is None:
        raise RuntimeError(
            f"alpaca-py is not installed in {sys.executable}; run the bot with .\\.venv\\Scripts\\python.exe"
        )
    if cfg.bot_poll_seconds < 30:
        raise ValueError("Paper bot polling interval must be at least 30 seconds")
    if cfg.bot_scan_interval_seconds < cfg.bot_poll_seconds:
        raise ValueError("Paper bot scan interval must be at least the polling interval")
    last_scan_at = None
    while True:
        now = perf_counter()
        try:
            scan = run_scanner if last_scan_at is None or now - last_scan_at >= cfg.bot_scan_interval_seconds else None
            if run_paper_bot_cycle(cfg, symbols, top, scan=scan):
                last_scan_at = perf_counter()
        except Exception as exc:
            print(f"Paper bot paused this cycle: {exc}")
            if once:
                raise
        print(f"Paper bot cycle complete at {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
        if once:
            break
        sleep(cfg.bot_poll_seconds)


def run_backtest_cli(
    symbols,
    start,
    end,
    cfg,
    *,
    slippage_bps=5.0,
    spread_bps=5.0,
    spread_scenarios_bps=None,
    commission_per_share=0.0,
    target_fill_mode="trade_through",
    target_trade_through_bps=5.0,
):
    from swingtrader.backtest import (
        AlpacaMarketDataProvider,
        SECTOR_BY_SYMBOL,
        SECTOR_ETF_BY_SECTOR,
        format_spread_sensitivity_report,
        run_backtest,
    )
    from swingtrader.backtest_store import DEFAULT_BACKTEST_DB, persist_sensitivity_results
    from swingtrader.paper_trading import load_paper_credentials

    if not symbols:
        raise ValueError("Backtests require explicit --symbols to make the tested universe reproducible")
    key, secret = load_paper_credentials()
    provider = AlpacaMarketDataProvider(key, secret)
    sectors = {SECTOR_BY_SYMBOL[symbol] for symbol in symbols if symbol in SECTOR_BY_SYMBOL}
    sector_etfs = sorted({
        SECTOR_ETF_BY_SECTOR[sector]
        for sector in sectors
        if sector in SECTOR_ETF_BY_SECTOR
    })
    requested_symbols = list(dict.fromkeys([*symbols, "SPY", *sector_etfs]))
    histories = provider.get_bars(requested_symbols, start, end)
    benchmark = histories.pop("SPY", None)
    sector_benchmarks = {etf: histories.pop(etf, None) for etf in sector_etfs}
    sector_benchmarks = {etf: bars for etf, bars in sector_benchmarks.items() if bars is not None}
    missing = sorted(set(symbols) - histories.keys())
    if missing:
        print(f"No Alpaca daily bars returned for: {', '.join(missing)}")
    if not histories:
        raise ValueError("Alpaca returned no history for the requested symbols")
    spread_scenarios = [spread_bps] if spread_scenarios_bps is None else spread_scenarios_bps
    if not spread_scenarios or any(not math.isfinite(value) or value < 0 for value in spread_scenarios):
        raise ValueError("Spread scenarios must contain non-negative basis-point values")
    strategy_cache = {}
    results = [
        (scenario_spread, run_backtest(
            histories,
            cfg,
            slippage_bps=slippage_bps,
            spread_bps=scenario_spread,
            commission_per_share=commission_per_share,
            target_fill_mode=target_fill_mode,
            target_trade_through_bps=target_trade_through_bps,
            strategy_cache=strategy_cache,
            benchmark=benchmark,
            sector_benchmarks=sector_benchmarks,
        ))
        for scenario_spread in spread_scenarios
    ]
    run_ids = persist_sensitivity_results(
        results,
        symbols=list(histories),
        start=start,
        end=end,
        slippage_bps=slippage_bps,
        commission_per_share=commission_per_share,
        target_fill_mode=target_fill_mode,
        target_trade_through_bps=target_trade_through_bps,
    )
    report = format_spread_sensitivity_report(
        results,
        run_ids=run_ids,
        database_path=str(DEFAULT_BACKTEST_DB),
        symbols=list(histories),
        start=start,
        end=end,
        slippage_bps=slippage_bps,
        commission_per_share=commission_per_share,
        target_fill_mode=target_fill_mode,
        target_trade_through_bps=target_trade_through_bps,
    )
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    report_path = REPORT_DIR / f"backtest_{timestamp}.md"
    report_path.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"Persisted {len(run_ids)} run(s) to {DEFAULT_BACKTEST_DB}")
    print(f"Saved backtest report to {report_path}")
    return results


@contextmanager
def bot_lock(path=BOT_LOCK_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r+b") as lock_file:
        lock_file.seek(0)
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("Another paper bot process is running") from exc
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Another paper bot process is running") from exc
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def main():
    started_at = perf_counter()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    suffix = 0
    while True:
        report_path = REPORT_DIR / f"scan_{timestamp}{f'_{suffix}' if suffix else ''}.md"
        try:
            report = report_path.open("x", encoding="utf-8")
            break
        except FileExistsError:
            suffix += 1

    with report, redirect_stdout(ReportOutput(sys.stdout, report)):
        print(f"# Swing Trader Scan - {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        try:
            parser = argparse.ArgumentParser(description="Consistent Swing Trading Scanner for NYSE & NASDAQ")
            parser.add_argument("--symbols", nargs="+", help="Optional specific ticker symbols to scan")
            parser.add_argument("--output", help="Path for JSON scan snapshot output")
            parser.add_argument("--top", type=int, default=25, help="Number of top candidates to return (default 25)")
            parser.add_argument("--min-price", type=float, help="Minimum stock price (default $5.00)")
            parser.add_argument("--max-price", type=float, help="Maximum stock price (default $30.00)")
            parser.add_argument("--backtest", action="store_true", help="Run a historical daily-bar backtest")
            parser.add_argument("--start", help="Backtest start date (YYYY-MM-DD)")
            parser.add_argument("--end", help="Backtest end date (YYYY-MM-DD)")
            parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage per fill in basis points")
            parser.add_argument("--spread-bps", type=float, default=5.0, help="Full bid-ask spread in basis points")
            parser.add_argument(
                "--spread-scenarios-bps",
                nargs="+",
                type=float,
                help="Run multiple full-spread scenarios against the same downloaded bars",
            )
            parser.add_argument(
                "--commission-per-share",
                type=float,
                default=0.0,
                help="Commission charged per share on each side of a trade",
            )
            parser.add_argument(
                "--target-fill-mode",
                choices=("touch", "trade_through"),
                default="trade_through",
                help="Target limit fill policy (default requires a trade-through)",
            )
            parser.add_argument(
                "--target-trade-through-bps",
                type=float,
                default=5.0,
                help="Required price trade-through for target fills (default 5 bps)",
            )
            paper_modes = parser.add_mutually_exclusive_group()
            paper_modes.add_argument("--paper-preview", action="store_true", help="Check top candidate against Alpaca paper quotes without placing an order")
            paper_modes.add_argument("--paper-trade", action="store_true", help="Submit one Alpaca paper bracket order for the top candidate")
            paper_modes.add_argument("--paper-status", action="store_true", help="Inspect Alpaca paper positions and orders without changing them")
            paper_modes.add_argument("--paper-reconcile", action="store_true", help="Inspect paper orders and request cancellation of stale unfilled bot entries")
            paper_modes.add_argument("--paper-bot", action="store_true", help="Reconcile continuously and rescan for open paper slots")
            paper_modes.add_argument("--paper-bot-once", action="store_true", help="Run one guarded paper bot cycle and exit")

            args = parser.parse_args()
            if args.backtest:
                if not args.start or not args.end:
                    parser.error("--backtest requires both --start and --end")
                if date.fromisoformat(args.start) > date.fromisoformat(args.end):
                    parser.error("--start must not be later than --end")
                config = build_config(minimum_price=args.min_price, maximum_price=args.max_price)
                run_backtest_cli(
                    args.symbols,
                    args.start,
                    args.end,
                    config,
                    slippage_bps=args.slippage_bps,
                    spread_bps=args.spread_bps,
                    spread_scenarios_bps=args.spread_scenarios_bps,
                    commission_per_share=args.commission_per_share,
                    target_fill_mode=args.target_fill_mode,
                    target_trade_through_bps=args.target_trade_through_bps,
                )
            elif args.paper_status or args.paper_reconcile:
                from swingtrader.paper_trading import report_paper_status

                report_paper_status(cancel_stale=args.paper_reconcile)
            elif args.paper_bot or args.paper_bot_once:
                config = build_config(minimum_price=args.min_price, maximum_price=args.max_price)
                with bot_lock():
                    run_paper_bot(config, symbols=args.symbols, top=args.top, once=args.paper_bot_once)
            else:
                config = build_config(
                    minimum_price=args.min_price,
                    maximum_price=args.max_price,
                )
                candidates = run_scanner(
                    symbols=args.symbols,
                    output_path=args.output,
                    top=args.top,
                    cfg=config,
                )
                if args.paper_preview or args.paper_trade:
                    if not candidates:
                        print("No candidate available for a paper order.")
                    else:
                        from swingtrader.paper_trading import submit_paper_candidate

                        try:
                            plan = submit_paper_candidate(candidates[0], config, execute=args.paper_trade)
                            print(f"\n## Alpaca Paper {'Order' if args.paper_trade else 'Preview'}\n")
                            print(f"{plan.symbol}: buy {plan.shares} @ <= ${plan.limit_price:.2f}; "
                                  f"stop ${plan.stop_price:.2f}; take profit ${plan.target_price:.2f}; "
                                  f"planned risk ${plan.risk_dollars:.2f}")
                        except ValueError as exc:
                            print(f"\nPaper order skipped: {exc}")
        finally:
            elapsed = perf_counter() - started_at
            print(f"\nTotal application runtime: {elapsed:.2f} seconds")
    print(f"Saved Markdown report to {report_path}")


if __name__ == "__main__":
    main()
