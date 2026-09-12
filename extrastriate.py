"""
extrastriate.py
---------------
EXTRASTRIATE = the Monitor (higher visual area)

This is the part that watches every open trade after V1 has placed it.

Responsibilities:
  1. Watch every active trade
  2. Close the trade early if the original Order Block is invalidated
  3. Detect hard Stop Loss or Take Profit hits
  4. Force-close forex/gold trades before weekend
  5. Record PnL and reason
  6. Persist state so monitoring survives GitHub Actions restarts

Daleko Market language:
  V1 has already bought or sold the rice.
  Extrastriate is the trusted assistant who sits and watches the price.
  If the big boys’ footprint (Order Block) is broken, or if SL/TP is hit,
  he closes the position and reports the result by email.

Only Order Blocks and Trendlines are monitored.
All old FVG / Breaker / Double Top logic has been removed.
"""

from datetime import datetime, timedelta

try:
    from utils.metrics import record_closed
except ImportError:
    def record_closed(t): pass

try:
    from config import MONITOR_INTERVAL_SEC
except ImportError:
    MONITOR_INTERVAL_SEC = 60


# =========================================================
# HELPERS
# =========================================================
def _pip_value(symbol: str) -> float:
    if "JPY" in symbol:
        return 0.01
    if symbol == "XAUUSD":
        return 0.10
    return 0.0001


def _buffer(symbol: str, pips: int = 3) -> float:
    return _pip_value(symbol) * pips


# =========================================================
# PRICE FEED (will use live Deriv later)
# =========================================================
def get_current_price(symbol: str):
    """Returns (bid, ask, mid) or (None, None, None)."""
    try:
        from utils.deriv_client import get_tick
        return get_tick(symbol)
    except Exception:
        return None, None, None


def get_latest_15m_candle(symbol: str):
    """Returns the most recent closed 15M candle or None."""
    try:
        from utils.deriv_client import get_latest_candle
        return get_latest_candle(symbol, timeframe="M15")
    except Exception:
        return None


# =========================================================
# CONDITION CHECKERS (only OB + Trendline)
# =========================================================
def _check_order_block(trade: dict, candle: dict) -> tuple:
    """
    If 15M closes beyond the Order Block, the idea is dead.
    Long  → close if close < zone_bottom
    Short → close if close > zone_top
    """
    direction = trade.get("direction")
    close = candle.get("C")
    bottom = trade.get("zone_bottom")
    top = trade.get("zone_top")

    if close is None or bottom is None or top is None:
        return False, None

    if direction == "long" and close < bottom:
        return True, "15M closed below Bullish OB — footprint invalidated"
    if direction == "short" and close > top:
        return True, "15M closed above Bearish OB — footprint invalidated"

    return False, None


def _check_trendline(trade: dict, candle: dict) -> tuple:
    """
    If price closes beyond the confirmed trendline zone, exit.
    """
    direction = trade.get("direction")
    close = candle.get("C")
    bottom = trade.get("zone_bottom")
    top = trade.get("zone_top")
    symbol = trade.get("symbol", "EURUSD")
    buf = _buffer(symbol, 3)

    if close is None:
        return False, None

    if direction == "long" and bottom is not None and close < (bottom - buf):
        return True, "15M closed below Uptrend Trendline — line broken"
    if direction == "short" and top is not None and close > (top + buf):
        return True, "15M closed above Downtrend Trendline — line broken"

    return False, None


def _check_sl_tp(trade: dict, candle: dict) -> tuple:
    """
    Hard SL / TP using candle high/low (wicks count).
    Returns (hit, exit_type, reason)
    """
    direction = trade.get("direction")
    sl = trade.get("sl")
    tp = trade.get("tp")

    if sl is None or tp is None or direction is None:
        return False, None, None

    high = candle.get("H")
    low  = candle.get("L")

    if direction == "long":
        if low is not None and low <= sl:
            return True, "sl", "Stop Loss hit"
        if high is not None and high >= tp:
            return True, "tp", "Take Profit hit"

    if direction == "short":
        if high is not None and high >= sl:
            return True, "sl", "Stop Loss hit"
        if low is not None and low <= tp:
            return True, "tp", "Take Profit hit"

    return False, None, None


def check_condition(trade: dict, candle: dict) -> tuple:
    """
    Decide whether the trade idea is still valid.
    Only Order Block and Trendline patterns are supported.
    """
    pattern = (trade.get("pattern") or "").lower()

    if "ob" in pattern or "order block" in pattern:
        return _check_order_block(trade, candle)

    if "trendline" in pattern or "tl" in pattern:
        return _check_trendline(trade, candle)

    # Unknown pattern → do not force close on condition
    return False, None


# =========================================================
# CLOSE POSITION ON BROKER
# =========================================================
def close_position(trade: dict) -> tuple:
    """
    Attempt to close on Deriv.
    Returns (success: bool, result: dict or None)
    """
    try:
        from utils.deriv_client import close_position as _close
        return _close(trade)
    except Exception as e:
        print(f"[Extrastriate] Close error: {e}")
        return False, {"error": str(e)}


# =========================================================
# RECORD THE CLOSE
# =========================================================
def _close_record(trade: dict, close_price: float, reason: str,
                  exit_type: str = "condition") -> dict:
    entry     = trade.get("entry", 0)
    direction = trade.get("direction", "long")
    symbol    = trade.get("symbol", "EURUSD")
    pip       = _pip_value(symbol)

    if close_price is None:
        close_price = entry

    if direction == "long":
        pnl_pips = round((close_price - entry) / pip, 1)
    else:
        pnl_pips = round((entry - close_price) / pip, 1)

    trade["status"]       = "closed"
    trade["close_price"]  = close_price
    trade["close_time"]   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    trade["close_reason"] = reason
    trade["exit_type"]    = exit_type
    trade["pnl_pips"]     = pnl_pips

    result = "WIN" if pnl_pips > 0 else ("LOSS" if pnl_pips < 0 else "BE")
    print(
        f"[Extrastriate] CLOSED [{result}] {trade.get('pattern', '?'):22} "
        f"{direction.upper():5} {symbol} | {entry:.5f} → {close_price:.5f} "
        f"| PnL {pnl_pips:+.1f} pips | {exit_type.upper()} — {reason}"
    )

    try:
        record_closed(trade)
    except Exception:
        pass

    # Email / daily log only (no Telegram)
    try:
        from utils.daily_log import log_closed_trade
        log_closed_trade(trade)
    except Exception:
        pass

    try:
        from utils.performance import on_trade_closed
        on_trade_closed(trade)
    except Exception:
        pass

    return trade


# =========================================================
# WEEKEND PROTECTION
# =========================================================
def _should_weekend_close(symbol: str) -> bool:
    """
    Forex and Gold should not stay open over the weekend.
    """
    now = datetime.utcnow()
    # Friday after 21:00 UTC or Saturday/Sunday
    if now.weekday() == 4 and now.hour >= 21:
        return True
    if now.weekday() >= 5:
        return True
    return False


# =========================================================
# MAIN MONITOR CYCLE
# =========================================================
def monitor_cycle(active_trades: list, symbol: str = None):
    """
    Check every active trade once.

    Returns:
      (still_active, closed_now, context)

      still_active — trades that remain open
      closed_now   — trades closed in this cycle
      context      — small dict for logging (optional use)
    """
    if not active_trades:
        return [], [], {"checked": 0, "closed": 0}

    still_active = []
    closed_now = []

    for trade in active_trades:
        if trade.get("status") != "active":
            # Already closed earlier — do not re-check
            continue

        trade_symbol = trade.get("symbol", symbol or "EURUSD")

        # 1. Weekend force-close
        if _should_weekend_close(trade_symbol):
            bid, ask, mid = get_current_price(trade_symbol)
            close_price = mid or trade.get("entry")
            close_position(trade)
            _close_record(trade, close_price, "Weekend force-close", "weekend")
            closed_now.append(trade)
            continue

        # 2. Get latest 15M candle
        candle = get_latest_15m_candle(trade_symbol)
        if candle is None:
            still_active.append(trade)
            continue

        # 3. Hard SL / TP first
        hit, exit_type, reason = _check_sl_tp(trade, candle)
        if hit:
            close_price = trade["sl"] if exit_type == "sl" else trade["tp"]
            close_position(trade)
            _close_record(trade, close_price, reason, exit_type)
            closed_now.append(trade)
            continue

        # 4. Condition invalidation (OB or Trendline broken)
        violated, reason = check_condition(trade, candle)
        if violated:
            close_price = candle.get("C")
            close_position(trade)
            _close_record(trade, close_price, reason, "condition")
            closed_now.append(trade)
            continue

        # Still valid
        still_active.append(trade)

    context = {
        "checked": len(active_trades),
        "still_open": len(still_active),
        "closed": len(closed_now),
    }
    return still_active, closed_now, context


def register_trades(trade_registry: list, v1_records: list) -> int:
    """
    Add newly placed trades from V1 into the monitoring registry.
    Returns how many new trades were registered.
    """
    added = 0
    existing_ids = {t.get("id") for t in trade_registry}

    for rec in v1_records:
        if not rec.get("placed"):
            continue
        if rec.get("id") in existing_ids:
            continue

        rec["status"] = "active"
        trade_registry.append(rec)
        existing_ids.add(rec["id"])
        added += 1
        print(f"[Extrastriate] Registered for monitoring: {rec['id']}")

    return added


# =========================================================
# Quick self-test
# =========================================================
if __name__ == "__main__":
    print("=" * 55)
    print("EXTRASTRIATE – Trade Monitor (OB + Trendline only)")
    print("=" * 55)

    # Fake active trade for demonstration
    demo_trade = {
        "id": "V1_Bullish OB_long_demo",
        "symbol": "EURUSD",
        "pattern": "Bullish OB",
        "direction": "long",
        "entry": 1.08500,
        "sl": 1.08350,
        "tp": 1.08900,
        "zone_top": 1.08580,
        "zone_bottom": 1.08420,
        "status": "active",
        "placed": True,
    }

    print("\nDemo trade registered:")
    print(f"  {demo_trade['pattern']} {demo_trade['direction']} "
          f"Entry {demo_trade['entry']} SL {demo_trade['sl']} TP {demo_trade['tp']}")

    print("\nMonitor cycle (no live candle → trade stays open)...")
    remaining, closed, ctx = monitor_cycle([demo_trade])
    print(f"Still open: {len(remaining)} | Closed: {len(closed)} | Context: {ctx}")
    print("Extrastriate ready.")
