import argparse
import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path
from time import perf_counter

from swingtrader.config import TradingConfig
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
    cfg = TradingConfig()
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

            args = parser.parse_args()
            config = build_config(
                minimum_price=args.min_price,
                maximum_price=args.max_price,
            )
            run_scanner(
                symbols=args.symbols,
                output_path=args.output,
                top=args.top,
                cfg=config,
            )
        finally:
            elapsed = perf_counter() - started_at
            print(f"\nTotal application runtime: {elapsed:.2f} seconds")
    print(f"Saved Markdown report to {report_path}")


if __name__ == "__main__":
    main()
