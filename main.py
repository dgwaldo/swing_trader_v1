import argparse
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

from swingtrader.config import TradingConfig
from swingtrader.data import (
    current_price,
    download_batch,
    fetch_market_candidates,
)
from swingtrader.scanner import analyze


FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "AVGO", "AMD", "NFLX", "TSLA", "PLTR", "ORCL",
    "CRM", "MU", "QCOM", "COST", "JPM", "XOM",
    "SOFI", "F", "PFE", "INTC", "SNAP", "RIVN",
    "NU", "VALE", "T", "BAC", "GM", "CCL", "NCLH", "DKNG",
    "HOOD", "MARA", "RKLB", "SIRI", "TGT", "AES", "ABUS", "AGRO", "AESI"
]


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
        f"| Rank | Symbol | Score | P(+10%) | P(-5%) | Risk | Entry | Stop | {target_pct_label} | Target 1 (+8-10%) | Target 2 (+16-20%) | Shares | Max Risk | Catalyst | Why Attractive |"
    )
    print("|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|")

    for rank, c in enumerate(candidates, start=1):
        reasons = "; ".join(c.reasons)
        print(
            f"| {rank} | **{c.symbol}** | {c.score}/100 | **{c.prob_gain_10d:.1%}** | {c.prob_loss_5d:.1%} | {c.risk_rating} | "
            f"${c.entry:.2f} | ${c.stop:.2f} | **${c.target_1pct:.2f}** | ${c.target_1:.2f} | ${c.target_2:.2f} | {c.shares} | "
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
            live_price = current_price(symbol)
            candidate = analyze(symbol, data, cfg, current_price=live_price)
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            print(f"  Error analyzing {symbol}: {exc}")

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


if __name__ == "__main__":
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
