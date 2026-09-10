import argparse
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

from swingtrader.config import TradingConfig
from swingtrader.data import current_price, discover_symbols, download
from swingtrader.scanner import analyze


FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "AVGO", "AMD", "NFLX", "TSLA", "PLTR", "ORCL",
    "CRM", "MU", "QCOM", "COST", "JPM", "XOM",
    "SOFI", "PLTR", "F", "PFE", "INTC", "SNAP", "RIVN",
    "NU", "VALE", "T", "BAC", "GM", "CCL", "NCLH", "DKNG",
    "HOOD", "MARA", "RKLB", "SIRI", "TGT"
]


def build_config(
    minimum_price=None,
    maximum_price=None,
    target_percent=None,
):
    cfg = TradingConfig()
    minimum_price = cfg.minimum_price if minimum_price is None else minimum_price
    maximum_price = cfg.maximum_price if maximum_price is None else maximum_price
    target_percent = cfg.target_percent if target_percent is None else target_percent

    if minimum_price > maximum_price:
        raise ValueError("Minimum price cannot be greater than maximum price.")

    if target_percent <= 0:
        raise ValueError("Target percent must be greater than zero.")

    return replace(
        cfg,
        minimum_price=minimum_price,
        maximum_price=maximum_price,
        target_percent=target_percent,
    )


def save_scan(candidates, symbols, output_path):
    payload = {
        "scan_date": date.today().isoformat(),
        "symbols_scanned": symbols,
        "candidates": [asdict(candidate) for candidate in candidates],
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved scan snapshot to {path}")


def print_candidates_table(candidates):
    print("\n## Swing Trade Candidates\n")
    print(
        "| Symbol | Score | Setup | Entry | Stop | Target | Shares | Risk | R:R | "
        "Sentiment | Reasons |"
    )
    print("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")

    for candidate in candidates:
        reasons = "; ".join(candidate.reasons)
        print(
            f"| {candidate.symbol} | {candidate.score} | {candidate.setup} | "
            f"${candidate.entry:.2f} | ${candidate.stop:.2f} | "
            f"${candidate.target:.2f} | {candidate.shares} | "
            f"${candidate.risk_dollars:.2f} | {candidate.reward_risk:.1f} | "
            f"{candidate.sentiment_score:+.2f} | {reasons} |"
        )


def scan(symbols=None, output_path=None, top=15, cfg=None):
    cfg = cfg or TradingConfig()
    if not symbols:
        symbols = discover_symbols(
            minimum_price=cfg.minimum_price,
            maximum_price=cfg.maximum_price,
            minimum_volume=cfg.minimum_average_volume,
        ) or FALLBACK_SYMBOLS
        print(f"Scanning {len(symbols)} Yahoo-discovered symbols...")

    candidates = []

    for symbol in symbols:
        print(f"Scanning {symbol}...")
        try:
            data = download(symbol)
            live_price = current_price(symbol)
            candidate = analyze(symbol, data, cfg, current_price=live_price)
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            print(f"  ERROR: {exc}")

    candidates.sort(key=lambda x: (x.score, x.reward_risk), reverse=True)
    if top:
        candidates = candidates[:top]
    if output_path is None:
        output_path = Path("data") / "scans" / f"scan_{date.today().isoformat()}.json"
    save_scan(candidates, symbols, output_path)

    if not candidates:
        print("No candidates met the V1 criteria.")
        return []

    print_candidates_table(candidates)
    return candidates


def print_evening_table(candidates, cfg=None):
    cfg = cfg or TradingConfig()
    target_percent = cfg.target_percent
    target_label = f"{target_percent:g}%"

    print("\n## Evening Swing Trade Grid\n")
    print(
        f"| Rank | Symbol | Score | Setup | Entry | {target_label} Profit | Target | "
        "Stop | Shares | Risk | R:R | Sentiment | Reasons |"
    )
    print("|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    rows = []
    for rank, candidate in enumerate(candidates, start=1):
        target_profit = candidate.target - candidate.entry
        reasons = "; ".join(candidate.reasons)
        print(
            f"| {rank} | {candidate.symbol} | {candidate.score} | {candidate.setup} | "
            f"${candidate.entry:.2f} | ${target_profit:.2f} | ${candidate.target:.2f} | "
            f"${candidate.stop:.2f} | {candidate.shares} | "
            f"${candidate.risk_dollars:.2f} | {candidate.reward_risk:.1f} | "
            f"{candidate.sentiment_score:+.2f} | {reasons} |"
        )
        rows.append({
            "rank": rank,
            "candidate": asdict(candidate),
            "target_percent": target_percent,
            "target_profit": target_profit,
        })

    return rows


def build_focus_analysis(rows):
    """Create an explainable English focus brief from the evening grid."""
    if not rows:
        return {
            "headline": "No actionable picks were found tonight.",
            "focus": [],
            "text": "No candidates had a current scan signal.",
        }

    ranked_symbols = [row["candidate"]["symbol"] for row in rows]
    best = ranked_symbols[0]
    second = ranked_symbols[1] if len(ranked_symbols) > 1 else None
    headline = f"Best overall bet: {best}."
    sentences = [
        f"Rank 1 is {best}, followed by {second}. The ranking is driven by live setup "
        "score, then reward/risk." if second else
        f"Rank 1 is {best}. The ranking is driven by live setup score, then reward/risk."
    ]

    secondary = [
        row["candidate"]["symbol"]
        for row in rows
        if row["candidate"]["symbol"] not in {best, second}
    ][:3]
    if secondary:
        sentences.append(
            f"Keep {', '.join(secondary)} as the next watchlist names, but review their "
            "entry price and setup quality before placing orders."
        )

    sentences.append(
        "Before buying, confirm the next-day bid/ask, use a limit entry at or below the "
        "planned entry, and recalculate shares from the actual stop distance."
    )

    return {
        "headline": headline,
        "focus": ranked_symbols[:3],
        "text": " ".join(sentences),
    }


def evening(symbols=None, output_path=None, cfg=None):
    """Create an after-hours top-10 scan and print the ranked grid."""
    cfg = cfg or TradingConfig()
    candidates = scan(symbols=symbols, top=10, cfg=cfg)
    if not candidates:
        return

    rows = print_evening_table(candidates, cfg=cfg)
    analysis = build_focus_analysis(rows)

    print("\n## Buying Focus\n")
    print(f"**{analysis['headline']}**")
    print(analysis["text"])

    if output_path is None:
        output_path = Path("data") / "scans" / f"evening_{date.today().isoformat()}.json"

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "report_date": date.today().isoformat(),
                "picks": rows,
                "buying_focus": analysis,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved evening report to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--symbols", nargs="+")
    scan_parser.add_argument("--output", help="Path for the JSON scan snapshot")
    scan_parser.add_argument("--top", type=int, default=15, help="Max candidates to keep (0 = no limit)")
    scan_parser.add_argument("--min-price", type=float, help="Minimum stock price to include")
    scan_parser.add_argument("--max-price", type=float, help="Maximum stock price to include")
    scan_parser.add_argument(
        "--target-percent",
        type=float,
        help="Profit target percent above entry (default 1.0)",
    )

    evening_parser = sub.add_parser(
        "evening",
        help="Scan and print a ranked next-day grid",
    )
    evening_parser.add_argument("--symbols", nargs="+", help="Optional symbols to scan")
    evening_parser.add_argument("--output", help="Path for the combined evening report")
    evening_parser.add_argument("--min-price", type=float, help="Minimum stock price to include")
    evening_parser.add_argument("--max-price", type=float, help="Maximum stock price to include")
    evening_parser.add_argument(
        "--target-percent",
        type=float,
        help="Profit target percent above entry (default 1.0)",
    )

    args = parser.parse_args()

    if args.command == "scan":
        scan(
            args.symbols,
            args.output,
            args.top,
            cfg=build_config(
                args.min_price,
                args.max_price,
                args.target_percent,
            ),
        )
    elif args.command == "evening":
        evening(
            args.symbols,
            args.output,
            cfg=build_config(
                args.min_price,
                args.max_price,
                args.target_percent,
            ),
        )
