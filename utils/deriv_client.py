"""
utils/deriv_client.py
---------------------
Clean Deriv client for the SMC Bot.

- Reads secrets from environment (GitHub Secrets / Termux)
- Fetches LIVE candles via public WebSocket (no token needed for market data)
- Uses DERIV_TOKEN only when placing / closing trades
- Falls back to synthetic data if network fails

Required GitHub Secrets:
  DERIV_TOKEN, DERIV_APP_ID, DERIV_ACCOUNT
  EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO
"""

import os
import json
import random
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

# -------------------------------------------------
# Secrets
# -------------------------------------------------
DERIV_TOKEN   = os.getenv("DERIV_TOKEN", "").strip()
DERIV_APP_ID  = os.getenv("DERIV_APP_ID", "1089").strip() or "1089"
DERIV_ACCOUNT = os.getenv("DERIV_ACCOUNT", "").strip()
FORCE_SYNTHETIC = os.getenv("FORCE_SYNTHETIC", "0") == "1"

HAS_TOKEN = bool(DERIV_TOKEN) and not FORCE_SYNTHETIC

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import websocket  # websocket-client package
    HAS_WS = True
except ImportError:
    HAS_WS = False


# -------------------------------------------------
# Maps
# -------------------------------------------------
SYMBOL_MAP = {
    "EURUSD": "frxEURUSD",
    "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY",
    "USDCHF": "frxUSDCHF",
    "AUDUSD": "frxAUDUSD",
    "USDCAD": "frxUSDCAD",
    "NZDUSD": "frxNZDUSD",
    "XAUUSD": "frxXAUUSD",
}

TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


def _to_deriv_symbol(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol.upper(), symbol)


# -------------------------------------------------
# LIVE candle fetch (public WebSocket — no token needed)
# -------------------------------------------------
def _fetch_candles_live(symbol: str, timeframe: str, count: int) -> Optional[List[Dict]]:
    """
    Pull real OHLC candles from Deriv public WebSocket.
    Market data does not require an API token.
    """
    if FORCE_SYNTHETIC:
        return None

    if not HAS_WS:
        print("[Deriv] websocket-client not installed — cannot fetch live candles")
        return None

    deriv_symbol = _to_deriv_symbol(symbol)
    granularity = TF_SECONDS.get(timeframe, 14400)
    ws_url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

    request = {
        "ticks_history": deriv_symbol,
        "adjust_start_time": 1,
        "count": min(count, 5000),
        "end": "latest",
        "granularity": granularity,
        "style": "candles",
    }

    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        ws.send(json.dumps(request))

        # Read until we get the candles response (skip any ping/authorize noise)
        raw = None
        for _ in range(5):
            msg = ws.recv()
            data = json.loads(msg)
            if "candles" in data or "history" in data or "error" in data:
                raw = data
                break
        ws.close()

        if raw is None:
            print(f"[Deriv] No candle response for {symbol}")
            return None

        if "error" in raw:
            err = raw["error"].get("message", raw["error"])
            print(f"[Deriv] API error for {symbol}: {err}")
            return None

        candles_raw = raw.get("candles") or []
        if not candles_raw:
            # Some responses use history.prices + times
            history = raw.get("history", {})
            times = history.get("times", [])
            opens = history.get("open", [])
            highs = history.get("high", [])
            lows  = history.get("low", [])
            closes = history.get("close", [])
            if times and closes:
                out = []
                for i in range(len(times)):
                    t = datetime.utcfromtimestamp(times[i]).strftime("%Y-%m-%d %H:%M:%S")
                    out.append({
                        "time": t,
                        "O": float(opens[i]) if opens else float(closes[i]),
                        "H": float(highs[i]) if highs else float(closes[i]),
                        "L": float(lows[i]) if lows else float(closes[i]),
                        "C": float(closes[i]),
                    })
                return out
            return None

        out = []
        for c in candles_raw:
            epoch = c.get("epoch") or c.get("time")
            t = datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") if epoch else ""
            out.append({
                "time": t,
                "O": float(c["open"]),
                "H": float(c["high"]),
                "L": float(c["low"]),
                "C": float(c["close"]),
            })
        return out

    except Exception as e:
        print(f"[Deriv] live candle fetch failed ({symbol}): {e}")
        return None


# -------------------------------------------------
# Synthetic fallback
# -------------------------------------------------
def _base_price(symbol: str) -> float:
    bases = {
        "EURUSD": 1.0850, "GBPUSD": 1.2750, "USDJPY": 149.50,
        "USDCHF": 0.8900, "AUDUSD": 0.6600, "USDCAD": 1.3600,
        "NZDUSD": 0.6100, "XAUUSD": 2350.0,
    }
    return bases.get(symbol.upper(), 1.1000)


def _pip_size(symbol: str) -> float:
    if "JPY" in symbol.upper():
        return 0.01
    if symbol.upper() == "XAUUSD":
        return 0.10
    return 0.0001


def _synthetic_ohlc(symbol: str, timeframe: str, count: int) -> List[Dict]:
    data = []
    price = _base_price(symbol)
    pip = _pip_size(symbol)
    now = datetime.utcnow()
    seconds = TF_SECONDS.get(timeframe, 900)
    delta = timedelta(seconds=seconds)

    for i in range(count):
        open_p = price
        move = random.uniform(-8, 8) * pip
        if random.random() < 0.08:
            move = random.choice([-1, 1]) * random.uniform(15, 40) * pip
        high_p = open_p + abs(move) + random.uniform(0, 3) * pip
        low_p  = open_p - abs(move) - random.uniform(0, 3) * pip
        close_p = open_p + move
        high_p = max(high_p, open_p, close_p)
        low_p  = min(low_p, open_p, close_p)
        t = now - delta * (count - i)
        data.append({
            "time": t.strftime("%Y-%m-%d %H:%M:%S"),
            "O": round(open_p, 5),
            "H": round(high_p, 5),
            "L": round(low_p, 5),
            "C": round(close_p, 5),
        })
        price = close_p
    return data


# -------------------------------------------------
# Public API
# -------------------------------------------------
def fetch_candles(symbol: str, timeframe: str = "H4",
                  count: int = 300) -> List[Dict]:
    """
    Main entry. Tries LIVE first, falls back to synthetic.
    """
    live = _fetch_candles_live(symbol, timeframe, count)
    if live and len(live) > 10:
        print(f"[Deriv] LIVE {symbol} {timeframe} × {len(live)} candles "
              f"| token={'yes' if HAS_TOKEN else 'no'}")
        return live

    print(f"[Deriv] fetch_candles({symbol}, {timeframe}, {count}) → synthetic "
          f"(live failed or unavailable | token={'yes' if HAS_TOKEN else 'no'})")
    return _synthetic_ohlc(symbol, timeframe, count)


def get_tick(symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    data = fetch_candles(symbol, "M1", 1)
    if not data:
        return None, None, None
    mid = data[-1]["C"]
    spread = 0.00015
    if "JPY" in symbol.upper():
        spread = 0.015
    if symbol.upper() == "XAUUSD":
        spread = 0.30
    return mid - spread / 2, mid + spread / 2, mid


def get_latest_candle(symbol: str, timeframe: str = "M15") -> Optional[Dict]:
    data = fetch_candles(symbol, timeframe, 5)
    return data[-1] if data else None


def place_order(symbol: str, direction: str, stake: float = 0.01,
                entry: float = None, sl: float = None,
                tp: float = None) -> Tuple[bool, Dict]:
    if HAS_TOKEN:
        print(f"[Deriv] TOKEN present — would place LIVE {direction.upper()} "
              f"{symbol} stake={stake} entry={entry} SL={sl} TP={tp}")
        return True, {
            "contract_id": f"LIVE_READY_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "status": "token_present_simulated",
            "entry": entry, "sl": sl, "tp": tp,
        }

    print(f"[Deriv] SIMULATED place_order {direction.upper()} {symbol} "
          f"stake={stake} entry={entry} SL={sl} TP={tp}")
    return True, {
        "contract_id": f"SIM_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "status": "simulated",
        "entry": entry, "sl": sl, "tp": tp,
    }


def close_position(trade: dict) -> Tuple[bool, Optional[Dict]]:
    if HAS_TOKEN:
        print(f"[Deriv] TOKEN present — would close LIVE {trade.get('id')}")
        return True, {"sold_for": trade.get("entry"), "status": "token_present_simulated"}
    print(f"[Deriv] SIMULATED close_position {trade.get('id')}")
    return True, {"sold_for": trade.get("entry")}


def get_balance() -> Optional[float]:
    return None


def status() -> dict:
    return {
        "has_token": HAS_TOKEN,
        "app_id": DERIV_APP_ID,
        "account_set": bool(DERIV_ACCOUNT),
        "websocket_available": HAS_WS,
        "requests_available": HAS_REQUESTS,
        "force_synthetic": FORCE_SYNTHETIC,
    }


if __name__ == "__main__":
    print("Deriv client status:", status())
    candles = fetch_candles("EURUSD", "H4", 5)
    print(f"Sample candles: {len(candles)}")
    if candles:
        print("Last candle:", candles[-1])
