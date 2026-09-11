"""
v1.py
-----
V1 = Primary Visual Cortex (the Decision + Execution stage)

Receives the short list of high-quality signals from LGN and:

  1. Calculates Stop Loss (beyond the Order Block / invalidation)
  2. Calculates Take Profit (using structure swings, minimum R:R)
  3. Rejects any trade with R:R below 1.8
  4. Sizes the position by risk % of equity
  5. Places the order (or simulates it)
  6. Returns clean trade records for Extrastriate to monitor

Daleko Market language:
  LGN has already filtered the rice market and brought only the
  best zones. V1 is the experienced trader who decides the exact
  entry, stop loss, take profit and how many bags to buy/sell.

Only the latest indicators are used.
No old FVG / Breaker / Double Top logic remains.
"""

from datetime import datetime

try:
    from config import (
        MIN_RR as CFG_MIN_RR,
        RISK_PERCENT,
        DEFAULT_EQUITY,
        MAX_OPEN_TRADES,
        SL_BUFFER_PIPS,
    )
except ImportError:
    CFG_MIN_RR = 1.8
    RISK_PERCENT = 0.75
    DEFAULT_EQUITY = 1000.0
    MAX_OPEN_TRADES = 3
    SL_BUFFER_PIPS = 3

MIN_RR = CFG_MIN_RR

try:
    from utils.metrics import record_placed, record_filtered
except ImportError:
    def record_placed(t): pass
    def record_filtered(s, r): pass


# =========================================================
# HELPERS
# =========================================================
def _pip(symbol: str) -> float:
    if "JPY" in symbol:
        return 0.01
    if symbol == "XAUUSD":
        return 0.10
    return 0.0001


def _sl_buffer(symbol: str) -> float:
    return SL_BUFFER_PIPS * _pip(symbol)


def _get_zone(signal: dict) -> tuple:
    """
    Normalise zone top/bottom from the clean LGN signal.
    LGN now sends "top" and "bottom".
    """
    top = signal.get("top") or signal.get("zone_top")
    bottom = signal.get("bottom") or signal.get("zone_bottom")
    return top, bottom


def _get_entry(signal: dict) -> float:
    """
    Entry price.
    For Order Blocks we use the edge of the zone that price is expected
    to react from (conservative).
    """
    if "trigger_price" in signal and signal["trigger_price"]:
        return signal["trigger_price"]

    top, bottom = _get_zone(signal)
    if top is None or bottom is None:
        return None

    # Long → we expect reaction from the bottom of the bullish OB
    # Short → we expect reaction from the top of the bearish OB
    if signal["direction"] == "long":
        return bottom
    return top


# =========================================================
# 1. STOP LOSS (SMC invalidation)
# =========================================================
def calculate_sl(signal: dict, symbol: str = "EURUSD") -> float:
    """
    Long  → SL just below the Order Block bottom
    Short → SL just above the Order Block top

    This is pure SMC: if price closes beyond the zone,
    the idea is invalid.
    """
    direction = signal["direction"]
    top, bottom = _get_zone(signal)
    buf = _sl_buffer(symbol)

    if top is None or bottom is None:
        return None

    if direction == "long":
        sl = bottom - buf
    else:
        sl = top + buf

    return round(sl, 5)


# =========================================================
# 2. TAKE PROFIT
# =========================================================
def calculate_tp(signal: dict, retina_result: dict, symbol: str = "EURUSD",
                 entry: float = None, sl: float = None) -> float:
    """
    TP = nearest clean swing in the trade direction
    that gives at least 2× the risk distance.

    Fallback: 2.5 × risk distance if no good swing exists.
    """
    if entry is None:
        entry = _get_entry(signal)
    if sl is None:
        sl = calculate_sl(signal, symbol)

    if entry is None or sl is None:
        return None

    direction = signal["direction"]
    swings = retina_result.get("swings", [])
    sl_distance = abs(entry - sl)
    min_tp_dist = sl_distance * 2.0

    if direction == "long":
        candidates = [
            s for s in swings
            if s["type"] == "SH" and s["price"] > entry + min_tp_dist
        ]
        if candidates:
            tp = min(candidates, key=lambda s: s["price"])["price"]
        else:
            tp = entry + (sl_distance * 2.5)
    else:
        candidates = [
            s for s in swings
            if s["type"] == "SL" and s["price"] < entry - min_tp_dist
        ]
        if candidates:
            tp = max(candidates, key=lambda s: s["price"])["price"]
        else:
            tp = entry - (sl_distance * 2.5)

    return round(tp, 5)


# =========================================================
# 3. RISK : REWARD
# =========================================================
def calculate_rr(entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return 0.0
    return round(reward / risk, 2)


# =========================================================
# 4. POSITION SIZE
# =========================================================
def calculate_lot(entry: float, sl: float, symbol: str,
                  equity: float = None) -> float:
    """
    Risk a fixed % of equity.
    Conservative cap at 0.5 lots for small accounts.
    """
    if equity is None:
        equity = DEFAULT_EQUITY

    risk_amount = equity * (RISK_PERCENT / 100.0)
    sl_pips = abs(entry - sl) / _pip(symbol)

    if sl_pips == 0:
        return 0.01

    # Rough pip value approximation for standard lot
    pip_value = 10.0 if "JPY" not in symbol else 9.0
    if symbol == "XAUUSD":
        pip_value = 1.0

    lot = risk_amount / (sl_pips * pip_value)
    lot = max(0.01, min(lot, 0.50))          # safety cap
    return round(lot, 2)


# =========================================================
# 5. PLACE ORDER
# =========================================================
def _place_order(symbol: str, direction: str, entry: float,
                 sl: float, tp: float, lot: float = 0.01) -> tuple:
    """
    Tries real Deriv order.
    Falls back to simulation if API is not ready.
    """
    try:
        from utils.deriv_client import place_order
        return place_order(symbol, direction, stake=lot,
                           entry=entry, sl=sl, tp=tp)
    except Exception as e:
        # Simulation mode (for testing before live wiring)
        print(f"  [SIM] Would place {direction} {symbol} "
              f"lot={lot} entry={entry:.5f} SL={sl:.5f} TP={tp:.5f}")
        return True, {"simulated": True, "error": None}


# =========================================================
# 6. TRADE RECORD
# =========================================================
def _build_trade_record(signal, entry, sl, tp, rr, placed, order_result,
                        symbol, lot=0.01, filtered_reason=None):
    top, bottom = _get_zone(signal)
    return {
        "id": f"V1_{signal['pattern']}_{signal['direction']}_"
              f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "symbol": symbol,
        "source": "V1",
        "pattern": signal["pattern"],
        "direction": signal["direction"],
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "lot": lot,
        "zone_top": top,
        "zone_bottom": bottom,
        "placed": placed,
        "filtered": filtered_reason is not None,
        "filtered_reason": filtered_reason,
        "order_result": order_result,
        "confluence": signal.get("confluence", []),
        "htf_bias": signal.get("htf_bias"),
        "placed_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "active" if placed else "rejected",
        "close_price": None,
        "close_time": None,
        "close_reason": None,
        "pnl_pips": None,
    }


# =========================================================
# 7. MAIN V1 PIPELINE
# =========================================================
def run_v1(lgn_signals: list, retina_result: dict,
           symbol: str = "EURUSD", equity: float = None) -> list:
    """
    Turn high-quality LGN signals into real (or simulated) trades.
    """
    trade_records = []

    if not lgn_signals:
        print("[V1] No signals from LGN")
        return trade_records

    print(f"[V1] Processing {len(lgn_signals)} signal(s)")

    for signal in lgn_signals:
        pattern   = signal["pattern"]
        direction = signal["direction"]

        # --- Entry ---
        entry = _get_entry(signal)
        if entry is None:
            print(f"  [SKIP] {pattern} — no valid entry price")
            continue

        # --- Stop Loss ---
        sl = calculate_sl(signal, symbol=symbol)
        if sl is None:
            print(f"  [SKIP] {pattern} — cannot calculate SL")
            continue

        if direction == "long" and sl >= entry:
            rec = _build_trade_record(signal, entry, sl, None, 0, False, None,
                                      symbol, filtered_reason="SL above entry")
            trade_records.append(rec)
            _log(rec)
            continue

        if direction == "short" and sl <= entry:
            rec = _build_trade_record(signal, entry, sl, None, 0, False, None,
                                      symbol, filtered_reason="SL below entry")
            trade_records.append(rec)
            _log(rec)
            continue

        # --- Take Profit ---
        tp = calculate_tp(signal, retina_result, symbol=symbol,
                          entry=entry, sl=sl)
        if tp is None:
            print(f"  [SKIP] {pattern} — cannot calculate TP")
            continue

        # --- R:R filter ---
        rr = calculate_rr(entry, sl, tp)
        if rr < MIN_RR:
            rec = _build_trade_record(signal, entry, sl, tp, rr, False, None,
                                      symbol, filtered_reason=f"R:R {rr} < {MIN_RR}")
            trade_records.append(rec)
            _log(rec)
            record_filtered(signal, rec["filtered_reason"])
            continue

        # --- Position size ---
        lot = calculate_lot(entry, sl, symbol, equity=equity)

        # --- Place order ---
        placed, order_result = _place_order(symbol, direction, entry, sl, tp, lot=lot)

        rec = _build_trade_record(signal, entry, sl, tp, rr, placed,
                                  order_result, symbol, lot=lot)
        trade_records.append(rec)
        _log(rec)

        if placed:
            record_placed(rec)
            try:
                from utils.daily_log import log_placed_trade
                log_placed_trade(rec)
            except Exception:
                pass
        else:
            err = (order_result or {}).get("error", "Unknown")
            print(f"  [REJECTED] {pattern} {direction} | {err}")

    placed_n   = sum(1 for r in trade_records if r["placed"])
    filtered_n = sum(1 for r in trade_records if r["filtered"])
    print(f"[V1] Done — {placed_n} placed, {filtered_n} filtered")
    return trade_records


def _log(record: dict):
    status = "PLACED  " if record["placed"] else ("FILTERED" if record["filtered"] else "REJECTED")
    rr_str = f"R:R {record['rr']}" if record.get("rr") else "R:R —"
    entry  = record.get("entry") or 0
    sl     = record.get("sl") or 0
    tp     = record.get("tp")
    print(
        f"  [{status}] {record['pattern']:22} {record['direction'].upper():5} "
        f"| Entry {entry:.5f} SL {sl:.5f} "
        f"TP {tp if tp else '—'} | {rr_str} | lot {record.get('lot', 0.01)}"
    )


# =========================================================
# Quick test
# =========================================================
if __name__ == "__main__":
    from retina import run_retina
    from lgn import run_lgn

    print("=" * 55)
    print("FULL CLEAN PIPELINE TEST")
    print("=" * 55)

    print("\n1. RETINA (Eye)...")
    retina = run_retina()

    print("\n2. LGN (Filter)...")
    signals = run_lgn(retina) if retina else []

    print("\n3. V1 (Execution)...")
    trades = run_v1(signals, retina) if retina else []

    print(f"\nFinal trade records: {len(trades)}")
