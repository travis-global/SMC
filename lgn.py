"""
lgn.py
------
LGN = Lateral Geniculate Nucleus (the Filter)

Receives the clean package from Retina and decides
which Order Blocks and Trendlines are good enough
to be passed to V1 for possible entry.

We only work with the latest indicators:
  - Order Blocks (from indicators/order_blocks.py)
  - Confirmed Trendlines (3-touch rule)

Everything else has been removed so you stay focused.
"""

from datetime import datetime, timedelta
import random

try:
    from config import (
        TRADING_SESSIONS,
        REQUIRE_HTF_TREND,
        MIN_ZONE_PIPS,
    )
except ImportError:
    TRADING_SESSIONS = {
        "EURUSD": [(7, 21)],
        "GBPUSD": [(7, 21)],
        "USDJPY": [(0, 23)],
        "XAUUSD": [(7, 21)],
    }
    REQUIRE_HTF_TREND = True
    MIN_ZONE_PIPS = 5


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def _in_trading_session(symbol: str) -> bool:
    """Simple session filter (London + New York for most pairs)."""
    now_hour = datetime.utcnow().hour
    sessions = TRADING_SESSIONS.get(symbol, [(0, 23)])
    for start, end in sessions:
        if start <= now_hour < end:
            return True
    return False


def _get_htf_bias(structure: list) -> str:
    """
    Very simple bias from the latest structure labels.
    HH + HL → bullish
    LH + LL → bearish
    """
    if not structure or len(structure) < 4:
        return "unknown"

    recent = structure[-6:]
    highs = [s for s in recent if s["type"] == "SH"]
    lows  = [s for s in recent if s["type"] == "SL"]

    if len(highs) >= 2 and len(lows) >= 2:
        last_two_highs = highs[-2:]
        last_two_lows  = lows[-2:]
        higher_highs = last_two_highs[-1]["price"] > last_two_highs[0]["price"]
        higher_lows  = last_two_lows[-1]["price"]  > last_two_lows[0]["price"]
        lower_highs  = last_two_highs[-1]["price"] < last_two_highs[0]["price"]
        lower_lows   = last_two_lows[-1]["price"]  < last_two_lows[0]["price"]

        if higher_highs and higher_lows:
            return "bullish"
        if lower_highs and lower_lows:
            return "bearish"

    return "unknown"


def fetch_15m_data(symbol: str, num_candles: int = 100) -> list:
    """
    Placeholder for 15M candles.
    Later this will call Deriv. For now we generate synthetic.
    """
    from retina import generate_ohlc_data
    # Re-use the generator but treat it as 15M for testing
    data = generate_ohlc_data(num_candles)
    return data


# -------------------------------------------------
# MAIN LGN PIPELINE
# -------------------------------------------------
def run_lgn(retina_result: dict, symbol: str = "EURUSD") -> list:
    """
    Filter the clean Retina package.

    Only keeps:
      - Fresh Order Blocks that align with HTF bias
      - Confirmed Trendlines (3+ touches)

    Returns a short list of high-quality signals for V1.
    """

    if not retina_result:
        print("[LGN] Empty Retina result — abort")
        return []

    if not _in_trading_session(symbol):
        print(f"[LGN] {symbol} — outside session, skip")
        return []

    structure   = retina_result.get("structure", [])
    order_blocks = retina_result.get("order_blocks", [])
    confirmed_tls = retina_result.get("confirmed_trendlines", [])

    htf_bias = _get_htf_bias(structure)
    print(f"[LGN] {symbol} — 4H bias: {htf_bias.upper()}")

    signals = []

    # -------------------------------------------------
    # 1. Order Blocks (only fresh ones that match bias)
    # -------------------------------------------------
    for ob in order_blocks:
        if not ob.get("fresh", False):
            continue
        if ob.get("mitigated", False):
            continue

        direction = None
        if ob["type"] == "Bullish OB" and htf_bias in ("bullish", "unknown"):
            direction = "long"
        elif ob["type"] == "Bearish OB" and htf_bias in ("bearish", "unknown"):
            direction = "short"

        if direction is None:
            continue

        # Basic zone width check
        zone_size = abs(ob["top"] - ob["bottom"])
        if zone_size < 0.0005:          # too tight
            continue

        signals.append({
            "pattern":       ob["type"],
            "direction":     direction,
            "top":           ob["top"],
            "bottom":        ob["bottom"],
            "time":          ob["time"],
            "index":         ob["index"],
            "source":        "order_block",
            "htf_bias":      htf_bias,
            "confluence":    ["fresh_ob", f"htf_{htf_bias}"],
        })

    # -------------------------------------------------
    # 2. Confirmed Trendlines only
    # -------------------------------------------------
    for tl in confirmed_tls:
        if tl.get("broken"):
            continue

        direction = "long" if tl["type"] == "Uptrend" else "short"

        # Only keep if it agrees with HTF bias
        if REQUIRE_HTF_TREND:
            if direction == "long" and htf_bias == "bearish":
                continue
            if direction == "short" and htf_bias == "bullish":
                continue

        signals.append({
            "pattern":       f"Confirmed {tl['type']} TL",
            "direction":     direction,
            "slope":         tl["slope"],
            "touch_count":   tl.get("touch_count", 0),
            "status":        tl.get("status"),
            "source":        "trendline",
            "htf_bias":      htf_bias,
            "confluence":    ["confirmed_tl", f"htf_{htf_bias}"],
        })

    if signals:
        print(f"[LGN] {len(signals)} high-quality signal(s) → V1")
        for s in signals:
            print(f"  [{s['direction'].upper():5}] {s['pattern']}")
    else:
        print("[LGN] No high-quality signals this cycle")

    return signals


# -------------------------------------------------
# Quick test
# -------------------------------------------------
if __name__ == "__main__":
    from retina import run_retina

    print("=" * 55)
    print("LGN – Filter (OB + Confirmed Trendlines only)")
    print("=" * 55)

    retina = run_retina()
    if retina:
        signals = run_lgn(retina)
        print(f"\nFinal signals passed to V1: {len(signals)}")
