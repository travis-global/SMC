"""
indicators/swings.py
--------------------
SWING HIGHS & SWING LOWS  (the foundation of everything)

This is the first tool we build.  
Without clean Swing Highs and Swing Lows we cannot draw:
- Order Blocks
- Trendlines
- Break of Structure (BOS)
- Change of Character (CHoCH)

Think of it like this in Daleko Market (our rice example):

  Swing High  = the highest price the bag of rice reached 
                before buyers got tired and sellers took over.
                
  Swing Low   = the lowest price the bag of rice reached 
                before sellers got tired and buyers took over.

We only care about the IMPORTANT turning points, not every small wiggle.

HOW IT WORKS (simple fractal method)
------------------------------------
We look at a candle and check a few candles to its left and right.

  - If the current candle has the HIGHEST high in that small window 
    → it is a Swing High (SH)

  - If the current candle has the LOWEST low in that small window 
    → it is a Swing Low (SL)

The "window" size controls how strict we are:
  - window = 2  →  looks 2 candles left + 2 candles right (good default)
  - Smaller window = more swings (more sensitive, good for 15M)
  - Larger window = fewer swings (cleaner, good for 4H)

This same function works for BOTH 4H and 15M.
We just change the window size if we want.

Author: SMC Bot team
"""

from typing import List, Dict, Any, Optional


def detect_swing_highs_lows(
    candles: List[Dict[str, Any]],
    window: int = 2,
    high_key: str = "H",
    low_key: str = "L",
    time_key: str = "time",
) -> List[Dict[str, Any]]:
    """
    Detect Swing Highs and Swing Lows using the fractal method.

    Parameters
    ----------
    candles : list of dicts
        Each dict must contain at least: high, low, and time.
        Example candle:
        {
            "time": "2026-09-11 12:00:00",
            "O": 1.0850,
            "H": 1.0875,
            "L": 1.0840,
            "C": 1.0865
        }

    window : int, default 2
        How many candles to look left and right.
        Recommended:
          - 4H timeframe  → window = 2 or 3
          - 15M timeframe → window = 2 or 3
        (You can experiment later. Start with 2.)

    high_key, low_key, time_key : str
        Column names in case your data uses different keys.
        Our bot currently uses "H", "L", "time".

    Returns
    -------
    list of dicts, each looking like:
        {
            "type":  "SH" or "SL",
            "price": 1.0875,
            "time":  "2026-09-11 12:00:00",
            "index": 47          # position in the original candles list
        }

    Notes for the learner
    ---------------------
    - We never take the first or last few candles as swings 
      because we do not have enough neighbours to confirm them.
    - After detection we clean consecutive same-type swings 
      so we only keep the most extreme one (highest SH or lowest SL).
      This removes noise and keeps the structure clean.
    """

    if not candles or len(candles) < (window * 2 + 1):
        return []

    raw_swings = []

    # We start after 'window' candles and stop before the last 'window' candles
    for i in range(window, len(candles) - window):
        current_high = candles[i][high_key]
        current_low  = candles[i][low_key]

        # Check left and right neighbours
        is_swing_high = True
        is_swing_low  = True

        for j in range(1, window + 1):
            # Left side
            if candles[i - j][high_key] >= current_high:
                is_swing_high = False
            if candles[i - j][low_key]  <= current_low:
                is_swing_low = False

            # Right side
            if candles[i + j][high_key] > current_high:   # strict > on right for classic fractal
                is_swing_high = False
            if candles[i + j][low_key]  < current_low:
                is_swing_low = False

        if is_swing_high:
            raw_swings.append({
                "type":  "SH",
                "price": current_high,
                "time":  candles[i][time_key],
                "index": i
            })

        if is_swing_low:
            raw_swings.append({
                "type":  "SL",
                "price": current_low,
                "time":  candles[i][time_key],
                "index": i
            })

    # -------------------------------------------------
    # Clean consecutive same-type swings
    # Keep only the most extreme one
    # (highest Swing High or lowest Swing Low)
    # -------------------------------------------------
    if not raw_swings:
        return []

    cleaned = [raw_swings[0]]

    for swing in raw_swings[1:]:
        last = cleaned[-1]

        if swing["type"] == last["type"]:
            # Same type → keep the more extreme one
            if swing["type"] == "SH" and swing["price"] > last["price"]:
                cleaned[-1] = swing
            elif swing["type"] == "SL" and swing["price"] < last["price"]:
                cleaned[-1] = swing
            # else keep the existing one
        else:
            cleaned.append(swing)

    return cleaned


def classify_market_structure(
    swings: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Label each swing as HH, HL, LH, or LL.

    This is how we know if the market is making:
      - Higher Highs + Higher Lows  →  Bullish structure (uptrend)
      - Lower Highs + Lower Lows    →  Bearish structure (downtrend)

    Daleko Market example:
      HH = rice made a new higher peak
      HL = rice made a higher bottom (still strong)
      LH = rice made a lower peak (weakness starting)
      LL = rice made a new lower bottom (sellers winning)

    Returns
    -------
    list of dicts with extra "label" key:
        {
            "type":  "SH",
            "label": "HH",          # or LH
            "price": 1.0875,
            "time":  "...",
            "index": 47
        }
    """
    if not swings:
        return []

    labeled = []
    last_sh = None
    last_sl = None

    for swing in swings:
        label = None

        if swing["type"] == "SH":
            if last_sh is None:
                label = "SH"          # first swing high has no comparison yet
            else:
                label = "HH" if swing["price"] > last_sh["price"] else "LH"
            last_sh = swing

        elif swing["type"] == "SL":
            if last_sl is None:
                label = "SL"
            else:
                label = "HL" if swing["price"] > last_sl["price"] else "LL"
            last_sl = swing

        labeled.append({
            **swing,
            "label": label
        })

    return labeled


# -------------------------------------------------
# Quick self-test (run this file directly to see it work)
# -------------------------------------------------
if __name__ == "__main__":
    # Tiny fake rice price data for testing
    test_candles = [
        {"time": "09:00", "O": 45.0, "H": 45.5, "L": 44.8, "C": 45.2},
        {"time": "10:00", "O": 45.2, "H": 46.0, "L": 45.0, "C": 45.8},  # potential SH
        {"time": "11:00", "O": 45.8, "H": 45.9, "L": 44.5, "C": 44.7},
        {"time": "12:00", "O": 44.7, "H": 45.0, "L": 43.5, "C": 43.8},  # potential SL
        {"time": "13:00", "O": 43.8, "H": 44.5, "L": 43.6, "C": 44.2},
        {"time": "14:00", "O": 44.2, "H": 47.0, "L": 44.0, "C": 46.5},  # potential SH
        {"time": "15:00", "O": 46.5, "H": 46.8, "L": 45.5, "C": 45.9},
        {"time": "16:00", "O": 45.9, "H": 46.0, "L": 44.0, "C": 44.3},  # potential SL
        {"time": "17:00", "O": 44.3, "H": 45.0, "L": 44.1, "C": 44.8},
    ]

    swings = detect_swing_highs_lows(test_candles, window=2)
    structure = classify_market_structure(swings)

    print("Detected Swings:")
    for s in structure:
        print(f"  {s['time']}  {s['type']}  {s['label']}  @ {s['price']}")
