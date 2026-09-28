import math
import runpy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from .config import TradingConfig
from .scanner import TradeCandidate


CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "config.py"
ENTRY_TIMEOUT = timedelta(minutes=10)


@dataclass(frozen=True)
class PaperOrderPlan:
    symbol: str
    shares: int
    limit_price: float
    stop_price: float
    target_price: float
    risk_dollars: float


def _cent(price: float, rounding: str) -> float:
    return float(Decimal(str(price)).quantize(Decimal("0.01"), rounding=rounding))


def load_paper_credentials(path: Path = CREDENTIALS_PATH) -> tuple[str, str]:
    if not path.is_file():
        raise ValueError("Create config.py from config.example.py and add Alpaca paper credentials")
    settings = runpy.run_path(str(path))
    base_url = settings.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    if not isinstance(base_url, str) or base_url.rstrip("/") != "https://paper-api.alpaca.markets":
        raise ValueError("Only the Alpaca paper endpoint is supported")
    key = settings.get("APCA_API_KEY_ID")
    secret = settings.get("APCA_API_SECRET_KEY")
    if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret.strip():
        raise ValueError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in config.py to Alpaca paper credentials")
    return key, secret


def review_paper_orders(trading, orders, now: datetime, *, cancel_stale: bool = False) -> list[str]:
    messages = []
    for order in orders:
        if not order.client_order_id.startswith("swing-paper-"):
            continue
        status = getattr(order.status, "value", order.status)
        side = getattr(order.side, "value", order.side)
        order_type = getattr(order.type, "value", order.type)
        order_class = getattr(order.order_class, "value", order.order_class)
        if side != "buy" or order_type != "limit" or order_class != "bracket":
            messages.append(f"{order.symbol} {order.id}: unexpected bot order; manual review required")
            continue

        filled_qty = Decimal(str(order.filled_qty or 0))
        if status == "filled":
            legs = order.legs or []
            leg_types = {getattr(leg.type, "value", leg.type) for leg in legs}
            if len(legs) < 2 or not {"limit", "stop"}.issubset(leg_types):
                messages.append(f"{order.symbol} {order.id}: filled but exits unverified; manual review required")
            else:
                messages.append(f"{order.symbol} {order.id}: filled; check bracket exit statuses in Alpaca")
        elif filled_qty > 0 or status == "partially_filled":
            messages.append(f"{order.symbol} {order.id}: partial fill; manual review required")
        elif status in {"new", "accepted", "pending_new"}:
            if order.created_at is None or order.created_at.tzinfo is None:
                messages.append(f"{order.symbol} {order.id}: missing order timestamp; manual review required")
            elif now - order.created_at >= ENTRY_TIMEOUT:
                if cancel_stale:
                    trading.cancel_order_by_id(order.id)
                    messages.append(f"{order.symbol} {order.id}: entry cancellation requested; verify in Alpaca")
                else:
                    messages.append(f"{order.symbol} {order.id}: stale unfilled entry; run --paper-reconcile to request cancellation")
            else:
                messages.append(f"{order.symbol} {order.id}: waiting for entry fill")
        else:
            messages.append(f"{order.symbol} {order.id}: {status}; review in Alpaca")
    return messages


def report_paper_status(*, cancel_stale: bool = False) -> None:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    key, secret = load_paper_credentials()
    trading = TradingClient(key, secret, paper=True)
    orders = trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, nested=True, limit=500))
    if len(orders) >= 500:
        raise ValueError("Order history limit reached; review orders manually in Alpaca")
    positions = trading.get_all_positions()
    print("\n## Alpaca Paper Status\n")
    for position in positions:
        print(f"Open position: {position.symbol} ({position.qty} shares)")
    for message in review_paper_orders(trading, orders, datetime.now(timezone.utc), cancel_stale=cancel_stale):
        print(message)
    if not positions and not any(order.client_order_id.startswith("swing-paper-") for order in orders):
        print("No open positions or bot orders found in the recent Alpaca order history.")


def plan_paper_order(candidate: TradeCandidate, bid: float, ask: float, cfg: TradingConfig) -> PaperOrderPlan:
    if not all(math.isfinite(value) for value in (bid, ask, candidate.entry, candidate.stop)):
        raise ValueError("Invalid price or quote")
    if bid <= 0 or ask < bid:
        raise ValueError("Invalid bid/ask quote")
    if (ask - bid) / ((ask + bid) / 2) > cfg.max_spread_fraction:
        raise ValueError("Quote spread exceeds configured maximum")
    if candidate.entry <= 0 or abs(ask / candidate.entry - 1) > cfg.max_price_drift_fraction:
        raise ValueError("Ask drifted beyond configured maximum from scan price; rescan")

    limit_price = _cent(ask, ROUND_CEILING)
    stop_price = _cent(ask - (candidate.entry - candidate.stop), ROUND_FLOOR)
    if not cfg.minimum_price <= limit_price <= cfg.maximum_price or stop_price <= 0 or stop_price >= bid:
        raise ValueError("Price or stop outside safe range")
    risk_per_share = limit_price - stop_price
    shares = min(
        math.floor(cfg.account_size * cfg.risk_fraction / risk_per_share),
        math.floor(cfg.account_size * cfg.max_position_fraction / limit_price),
    )
    if shares < 1:
        raise ValueError("No whole shares fit the configured risk and position limits")
    target_price = _cent(limit_price + max(risk_per_share * 1.5, limit_price * 0.08), ROUND_CEILING)
    return PaperOrderPlan(candidate.symbol, shares, limit_price, stop_price, target_price, shares * risk_per_share)


def submit_paper_candidate(candidate: TradeCandidate, cfg: TradingConfig, *, execute: bool = False) -> PaperOrderPlan:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, StopLossRequest, TakeProfitRequest

    key, secret = load_paper_credentials()

    trading = TradingClient(key, secret, paper=True)
    if not trading.get_clock().is_open:
        raise ValueError("Paper orders are limited to regular market hours")
    account = trading.get_account()
    if account.trading_blocked:
        raise ValueError("Paper account is blocked from trading")
    if any(position.symbol == candidate.symbol for position in trading.get_all_positions()):
        raise ValueError(f"Already holding {candidate.symbol} in the paper account")
    if trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[candidate.symbol])):
        raise ValueError(f"Open paper order already exists for {candidate.symbol}")

    quote = StockHistoricalDataClient(key, secret).get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=candidate.symbol)
    ).get(candidate.symbol)
    if quote is None or quote.timestamp is None or quote.timestamp.tzinfo is None:
        raise ValueError("No timestamped Alpaca quote available")
    age = datetime.now(timezone.utc) - quote.timestamp.astimezone(timezone.utc)
    if age < timedelta(0) or age > timedelta(seconds=cfg.max_quote_age_seconds):
        raise ValueError("Alpaca quote is stale; no order placed")
    plan = plan_paper_order(candidate, float(quote.bid_price), float(quote.ask_price), cfg)

    available_cash = min(float(account.cash), float(account.buying_power))
    if plan.shares * plan.limit_price > available_cash:
        raise ValueError("Insufficient paper account cash for planned order")

    if execute:
        order = LimitOrderRequest(
            symbol=plan.symbol,
            qty=plan.shares,
            limit_price=plan.limit_price,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=plan.target_price),
            stop_loss=StopLossRequest(stop_price=plan.stop_price),
            client_order_id=f"swing-paper-{candidate.symbol}-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}",
        )
        submitted = trading.submit_order(order_data=order)
        print(f"Submitted Alpaca paper bracket order {submitted.id} ({submitted.status})")
    return plan