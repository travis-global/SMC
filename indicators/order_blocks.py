"""
indicators/order_blocks.py
--------------------------
ORDER BLOCKS  (the footprints of the big boys)

This is the second tool we build.

In Daleko Market (our rice example):

  Bullish Order Block = the last place the big importers were still 
                        selling before they suddenly started buying 
                        with heavy force and rice price jumped up.

  Bearish Order Block = the last place the big importers were still 
                        buying before they suddenly started dumping 
                        rice and the price crashed.

We only keep high-quality Order Blocks that are created by a real 
structure break (Break of Structure). Weak moves are ignored.

HOW WE DETECT THEM (expert method)
----------------------------------
1. We already have clean Swing Highs and Swing Lows from swings.py
2. When price CLOSES above a previous Swing High → Bullish structure break
3. We look back between that Swing High and the break candle
4. We take the candle that made the LOWEST low in that zone
   → that candle becomes the Bullish Order Block
5. Mirror logic for Bearish Order Block (close below Swing Low → 
   take the candle with the HIGHEST high)

This is the classic ICT / Smart Money way of finding institutional footprints.

Author: SMC Bot team
"""

from typing import List, Dict, Any, Optional


def detect_order_blocks(
    candles: List[Dict[str, Any]],
    swings: List[Dict[str, Any]],
    high_key: str = "H",
    low_key: str = "L",
    open_key: str = "O",
    close_key: str = "C",
    time_key: str = "time",
    min_break_distance: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Detect high-quality Order Blocks using structure breaks.

    Parameters
    ----------
    candles : list of dicts
        Full OHLC list (same format used in swings.py)

    swings : list of dicts
        Output from detect_swing_highs_lows()
        Each swing must have: type ("SH" or "SL"), price, index, time

    min_break_distance : float, default 0.0
        Optional filter. The break must move at least this far 
        beyond the swing level. Useful later for filtering weak breaks.
        For now we keep it at 0 (accept all real closes beyond the swing).

    Returns
    -------
    list of Order Block dicts:

        {
            "type":        "Bullish OB" or "Bearish OB",
            "top":         float,          # top of the zone
            "bottom":      float,          # bottom of the zone
            "time":        str,            # time of the OB candle
            "index":       int,            # position of the OB candle
            "break_index": int,            # candle that confirmed the break
            "swing_price": float,          # the swing that was broken
            "mitigated":   False,          # will be updated later
            "fresh":       True            # still unmitigated
        }

    Notes for the learner
    ---------------------
    - We only create an OB after a real close beyond a swing.
      This stops us from drawing Order Blocks on every small pullback.
    - The zone uses the full candle (High to Low). 
      This is safer when you are still learning.
    - Later we can refine the zone to the body only if needed.
    """

    if not candles or not swings or len(candles) < 5:
        return []

    order_blocks = []
    used_swing_indices = set()          # avoid using the same swing twice

    # Separate swing highs and swing lows for faster lookup
    swing_highs = [s for s in swings if s["type"] == "SH"]
    swing_lows  = [s for s in swings if s["type"] == "SL"]

    # -------------------------------------------------------
    # BULLISH ORDER BLOCKS
    # Price closes above a previous Swing High
    # -------------------------------------------------------
    for i in range(1, len(candles)):
        close_price = candles[i][close_key]
        high_price  = candles[i][high_key]

        # Find the most recent Swing High that is still before this candle
        # and has not been used yet
        candidate_swing = None
        for sh in reversed(swing_highs):
            if sh["index"] >= i:
                continue
            if sh["index"] in used_swing_indices:
                continue
            if close_price > sh["price"] + min_break_distance:
                candidate_swing = sh
                break

        if candidate_swing is None:
            continue

        # We have a valid bullish break of structure
        swing_idx = candidate_swing["index"]
        used_swing_indices.add(swing_idx)

        # Look for the lowest low between the swing and the break candle
        # That candle is our Order Block origin
        best_idx = i - 1          # default to the candle just before the break
        best_low = candles[best_idx][low_key]

        for j in range(swing_idx + 1, i):
            if candles[j][low_key] < best_low:
                best_low = candles[j][low_key]
                best_idx = j

        ob_candle = candles[best_idx]

        order_blocks.append({
            "type":        "Bullish OB",
            "top":         ob_candle[high_key],
            "bottom":      ob_candle[low_key],
            "time":        ob_candle[time_key],
            "index":       best_idx,
            "break_index": i,
            "swing_price": candidate_swing["price"],
            "mitigated":   False,
            "fresh":       True,
        })

    # -------------------------------------------------------
    # BEARISH ORDER BLOCKS
    # Price closes below a previous Swing Low
    # -------------------------------------------------------
    used_swing_indices = set()          # reset for bearish side

    for i in range(1, len(candles)):
        close_price = candles[i][close_key]
        low_price   = candles[i][low_key]

        candidate_swing = None
        for sl in reversed(swing_lows):
            if sl["index"] >= i:
                continue
            if sl["index"] in used_swing_indices:
                continue
            if close_price < sl["price"] - min_break_distance:
                candidate_swing = sl
                break

        if candidate_swing is None:
            continue

        swing_idx = candidate_swing["index"]
        used_swing_indices.add(swing_idx)

        # Look for the highest high between the swing and the break candle
        best_idx = i - 1
        best_high = candles[best_idx][high_key]

        for j in range(swing_idx + 1, i):
            if candles[j][high_key] > best_high:
                best_high = candles[j][high_key]
                best_idx = j

        ob_candle = candles[best_idx]

        order_blocks.append({
            "type":        "Bearish OB",
            "top":         ob_candle[high_key],
            "bottom":      ob_candle[low_key],
            "time":        ob_candle[time_key],
            "index":       best_idx,
            "break_index": i,
            "swing_price": candidate_swing["price"],
            "mitigated":   False,
            "fresh":       True,
        })

    # Sort by index so the list is in time order
    order_blocks.sort(key=lambda x: x["index"])

    return order_blocks


def update_ob_mitigation(
    order_blocks: List[Dict[str, Any]],
    candles: List[Dict[str, Any]],
    high_key: str = "H",
    low_key: str = "L",
    start_from_index: int = 0,
) -> List[Dict[str, Any]]:
    """
    Check which Order Blocks have been mitigated (price traded into them).

    A Bullish OB is mitigated when price trades down into its zone 
    (any candle low touches or goes below the top of the OB).

    A Bearish OB is mitigated when price trades up into its zone 
    (any candle high touches or goes above the bottom of the OB).

    We mark them as mitigated = True and fresh = False.
    We do NOT delete them — mitigated OBs can still be useful later 
    (especially for breaker blocks).

    Parameters
    ----------
    order_blocks : list
        Output from detect_order_blocks()
    candles : list
        The same candle data
    start_from_index : int
        Only check mitigation from this candle forward 
        (useful when running live so we do not re-check old history)

    Returns
    -------
    The same list with updated mitigated / fresh flags
    """

    for ob in order_blocks:
        if ob["mitigated"]:
            continue          # already known

        ob_index = ob["index"]

        # Start checking from the candle after the OB was formed
        check_start = max(ob_index + 1, start_from_index)

        for i in range(check_start, len(candles)):
            candle = candles[i]

            if ob["type"] == "Bullish OB":
                # Price came back down into the zone
                if candle[low_key] <= ob["top"]:
                    ob["mitigated"] = True
                    ob["fresh"] = False
                    break

            elif ob["type"] == "Bearish OB":
                # Price came back up into the zone
                if candle[high_key] >= ob["bottom"]:
                    ob["mitigated"] = True
                    ob["fresh"] = False
                    break

    return order_blocks


# -------------------------------------------------
# Quick self-test (run this file directly)
# -------------------------------------------------
if __name__ == "__main__":
    # Tiny fake rice price data
    test_candles = [
        {"time": "08:00", "O": 45.0, "H": 45.8, "L": 44.9, "C": 45.5},
        {"time": "09:00", "O": 45.5, "H": 46.5, "L": 45.3, "C": 46.2},   # Swing High
        {"time": "10:00", "O": 46.2, "H": 46.4, "L": 45.0, "C": 45.2},
        {"time": "11:00", "O": 45.2, "H": 45.5, "L": 44.0, "C": 44.3},   # low area
        {"time": "12:00", "O": 44.3, "H": 44.8, "L": 43.8, "C": 44.0},   # potential OB candle
        {"time": "13:00", "O": 44.0, "H": 47.5, "L": 43.9, "C": 47.2},   # strong break above SH
        {"time": "14:00", "O": 47.2, "H": 48.0, "L": 46.8, "C": 47.6},
        {"time": "15:00", "O": 47.6, "H": 47.9, "L": 46.5, "C": 46.8},
    ]

    # First get swings
    from indicators.swings import detect_swing_highs_lows
    swings = detect_swing_highs_lows(test_candles, window=1)

    print("Swings found:")
    for s in swings:
        print(f"  {s['time']}  {s['type']}  @ {s['price']}")

    obs = detect_order_blocks(test_candles, swings)

    print("\nOrder Blocks found:")
    for ob in obs:
        print(f"  {ob['type']}  |  {ob['time']}  |  Top={ob['top']}  Bottom={ob['bottom']}  |  Fresh={ob['fresh']}")
