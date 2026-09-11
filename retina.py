"""
retina.py
---------
RETINA = the Eye of the SMC Bot

This file is now CLEAN.
It only uses the latest modular indicators:

  indicators/swings.py
  indicators/order_blocks.py
  indicators/trendlines.py

All old detection functions have been removed so you will not be confused.

What Retina does:
  1. Gets candles (live 4H or synthetic)
  2. Finds Swing Highs & Swing Lows
  3. Labels market structure (HH, HL, LH, LL)
  4. Finds Order Blocks
  5. Finds Trendlines and grades them with the 3-touch rule
     (noted → provisional → confirmed)

It does NOT decide to buy or sell.
It only sees and packages clean information for LGN.
"""

import random
from datetime import datetime, timedelta

# Latest clean modular indicators only
from indicators.swings import detect_swing_highs_lows, classify_market_structure
from indicators.order_blocks import detect_order_blocks, update_ob_mitigation
from indicators.trendlines import (
    detect_trendlines,
    check_trendline_break,
    get_confirmed_trendlines,
)


# =========================================================
# 1. DATA GENERATION
# =========================================================
def generate_ohlc_data(num_candles=300):
    """
    Synthetic OHLC generator for offline testing.
    Useful when Deriv API is not yet wired or for GitHub Actions tests.
    """
    data       = []
    base_price = 1.2000
    time       = datetime.now()

    ob_active    = False
    ob_direction = None
    ob_high      = None
    ob_low       = None
    ob_countdown = 0

    for _ in range(num_candles):
        open_price = base_price

        if ob_active and ob_countdown > 0:
            if ob_direction == "bull":
                high_price  = min(open_price + random.uniform(0.0005, 0.0010), ob_high)
                low_price   = max(open_price - random.uniform(0.0005, 0.0010), ob_low)
                close_price = random.uniform(low_price, low_price + (high_price - low_price) * 0.5)
            else:
                high_price  = min(open_price + random.uniform(0.0005, 0.0010), ob_high)
                low_price   = max(open_price - random.uniform(0.0005, 0.0010), ob_low)
                close_price = random.uniform(low_price + (high_price - low_price) * 0.5, high_price)
            ob_countdown -= 1

        elif ob_active and ob_countdown == 0:
            if ob_direction == "bull":
                high_price  = open_price + random.uniform(0.0030, 0.0060)
                low_price   = open_price - random.uniform(0.0002, 0.0005)
                close_price = random.uniform(open_price + (high_price - open_price) * 0.6, high_price)
            else:
                high_price  = open_price + random.uniform(0.0002, 0.0005)
                low_price   = open_price - random.uniform(0.0030, 0.0060)
                close_price = random.uniform(low_price, low_price + (open_price - low_price) * 0.4)
            ob_active = False

        else:
            high_price  = open_price + random.uniform(0.0005, 0.0020)
            low_price   = open_price - random.uniform(0.0005, 0.0020)
            close_price = random.uniform(low_price, high_price)

            if random.random() < 0.12:
                ob_active    = True
                ob_direction = random.choice(["bull", "bear"])
                ob_high      = high_price
                ob_low       = low_price
                ob_countdown = random.randint(2, 5)

        data.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "O":    round(open_price,  5),
            "H":    round(high_price,  5),
            "L":    round(low_price,   5),
            "C":    round(close_price, 5)
        })

        base_price  = close_price
        time       += timedelta(hours=4)   # 4H spacing for Retina

    return data


def generate_ohlc_data_live(symbol="EURUSD", timeframe_str="H4", num_candles=300):
    """
    Fetch live candles from Deriv API.
    Falls back to synthetic data if Deriv is unavailable.
    """
    try:
        from utils.deriv_client import fetch_candles
        data = fetch_candles(symbol, timeframe=timeframe_str, count=num_candles)
        if data:
            return data
    except Exception as e:
        print(f"[Retina] Deriv fetch failed: {e}")

    print(f"[Retina] Using synthetic data for {symbol}")
    return generate_ohlc_data(num_candles)


# =========================================================
# 2. MAIN RETINA PIPELINE  (only latest indicators)
# =========================================================
def run_retina(data=None, symbol="EURUSD", timeframe="H4"):
    """
    Retina = the Eye of the system.

    It looks at the raw candles and extracts only what we need:
      1. Swing Highs & Swing Lows
      2. Market structure labels (HH, HL, LH, LL)
      3. Order Blocks (Bullish / Bearish)
      4. Trendlines with professional 3-touch status
         (noted → provisional → confirmed)

    Daleko Market language:
      Retina is the sharp-eyed boy at the entrance of the market
      who watches the rice price and reports only the important
      footprints and the main direction lines to the next person (LGN).
    """

    LOOKBACK = 300

    # 1. Get candles
    if data is None:
        data = generate_ohlc_data_live(symbol=symbol, timeframe_str=timeframe,
                                       num_candles=LOOKBACK)

    if not data or len(data) < 30:
        print("[Retina] Not enough candles to analyse")
        return None

    # 2. Swing Highs & Swing Lows
    swings = detect_swing_highs_lows(data, window=2)

    # 3. Label structure
    structure = classify_market_structure(swings)

    # 4. Order Blocks
    order_blocks = detect_order_blocks(data, swings)
    order_blocks = update_ob_mitigation(order_blocks, data)

    # 5. Trendlines (3-touch rule)
    trendlines = detect_trendlines(structure, data, min_points=2)
    trendlines = check_trendline_break(trendlines, data)
    confirmed_trendlines = get_confirmed_trendlines(trendlines)

    # 6. Clean package for LGN
    return {
        "symbol":               symbol,
        "timeframe":            timeframe,
        "data":                 data,
        "swings":               swings,
        "structure":            structure,
        "order_blocks":         order_blocks,
        "trendlines":           trendlines,
        "confirmed_trendlines": confirmed_trendlines,
    }


# =========================================================
# ENTRY POINT  (quick test)
# =========================================================
if __name__ == "__main__":
    print("=" * 55)
    print("RETINA – Eye of the SMC Bot (OB + Trendlines only)")
    print("=" * 55)

    result = run_retina()

    if result is None:
        print("Retina could not run – not enough data")
    else:
        print(f"\nSymbol          : {result['symbol']}")
        print(f"Timeframe       : {result['timeframe']}")
        print(f"Candles         : {len(result['data'])}")
        print(f"Swings          : {len(result['swings'])}")
        print(f"Structure points: {len(result['structure'])}")
        print(f"Order Blocks    : {len(result['order_blocks'])}")
        print(f"All Trendlines  : {len(result['trendlines'])}")
        print(f"Confirmed TLs   : {len(result['confirmed_trendlines'])}")

        noted       = [t for t in result['trendlines'] if t.get('status') == 'noted']
        provisional = [t for t in result['trendlines'] if t.get('status') == 'provisional']
        confirmed   = [t for t in result['trendlines'] if t.get('status') == 'confirmed']
        broken      = [t for t in result['trendlines'] if t.get('status') == 'broken']

        print(f"\nTrendline Status:")
        print(f"  Noted       : {len(noted)}")
        print(f"  Provisional : {len(provisional)}")
        print(f"  Confirmed   : {len(confirmed)}")
        print(f"  Broken      : {len(broken)}")

        if result['order_blocks']:
            print(f"\nSample Order Blocks:")
            for ob in result['order_blocks'][:3]:
                print(f"  {ob['type']:12} | {ob['time']} | "
                      f"Top={ob['top']:.5f} Bottom={ob['bottom']:.5f} | "
                      f"Fresh={ob.get('fresh')}")
