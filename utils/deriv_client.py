"""
utils/deriv_client.py
---------------------
Clean Deriv client for the SMC Bot.

Design goals:
  - Read all secrets from environment variables (GitHub Secrets / Termux)
  - Work on both Termux and GitHub Actions
  - Fall back to high-quality synthetic data when token is missing
    or the network call fails (so the pipeline never crashes)
  - Support the pairs we actually trade: EURUSD, GBPUSD, USDJPY, XAUUSD
    (easy to extend later)

Required environment variables (GitHub Secrets):
  DERIV_TOKEN     → your Deriv API token (demo or real)
  DERIV_APP_ID    → your Deriv App ID (default public 1089 works for data)
  DERIV_ACCOUNT   → optional account ID

Email secrets are handled in email_notifier.py.
"""

import os
import json
import random
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

# -------------------------------------------------
# Secrets (never hard-code real tokens)
# -------------------------------------------------
DERIV_TOKEN   = os.getenv("DERIV_TOKEN", "").strip()
DERIV_APP_ID  = os.getenv("DERIV_APP_ID", "1089").strip() or "1089"
DERIV_ACCOUNT = os.getenv("DERIV_ACCOUNT", "").strip()

# Optional: force synthetic even if token exists (useful for testing)
FORCE_SYNTHETIC = os.getenv("FORCE_SYNTHETIC", "0") == "1"

HAS_TOKEN = bool(DERIV_TOKEN) and not FORCE_SYNTHETIC

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# -------------------------------------------------
# Symbol & timeframe maps
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
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


def _to_deriv_symbol(symbol: str) -> str:
    return SYMBOL_MAP.get(symbol.upper(), symbol)


# -------------------------------------------------
# Public candle fetch via Deriv WebSocket (simple)
# -------------------------------------------------
def _fetch_candles_live(symbol: str, timeframe: str, count: int) -> Optional[List[Dict]]:
    """
    Attempt to pull real candles from Deriv.
    Uses the public WebSocket endpoint (no auth needed for history).
    Returns None on any failure so caller can fall back to synthetic.
    """
    if not HAS_REQUESTS:
        return None

    deriv_symbol = _to_deriv_symbol(symbol)
    granularity = TF_SECONDS.get(timeframe, 14400)
    end = int(time.time())

    # Deriv candles request
    req = {
        "ticks_history": deriv_symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "granularity": granularity,
        "style": "candles",
    }

    try:
        # We use a short-lived WebSocket via requests is not ideal;
        # for reliability on GitHub Actions we try the HTTP-style
        # endpoint that many community wrappers use.
        # Fallback path: if this fails we return None.
        url = f"https://api.deriv.com/api/v1/candles"
        # Note: official Deriv prefers WebSocket. This HTTP attempt
        # may not work on all environments; synthetic remains safe.
        # Real production path will be upgraded to proper WS later.
        return None          # currently force controlled fallback
    except Exception as e:
        print(f"[Deriv] live candle fetch failed: {e}")
        return None


# -------------------------------------------------
# Synthetic generator (always available)
# -------------------------------------------------
def _base_price(symbol: str) -> float:
    bases = {
        "EURUSD": 1.0850,
        "GBPUSD": 1.2750,
        "USDJPY": 149.50,
        "USDCHF": 0.8900,
        "AUDUSD": 0.6600,
        "USDCAD": 1.3600,
        "NZDUSD": 0.6100,
        "XAUUSD": 2350.0,
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
# Public API used by the rest of the bot
# -------------------------------------------------
def fetch_candles(symbol: str, timeframe: str = "H4",
                  count: int = 300) -> List[Dict]:
    """
    Main entry point for candles.
    Tries live Deriv first (when token is present).
    Falls back to synthetic so the pipeline never breaks.
    """
    if HAS_TOKEN:
        live = _fetch_candles_live(symbol, timeframe, count)
        if live and len(live) > 10:
            print(f"[Deriv] LIVE candles {symbol} {timeframe} × {len(live)}")
            return live

    print(f"[Deriv] fetch_candles({symbol}, {timeframe}, {count}) → synthetic "
          f"(live API not fully wired or token missing)")
    return _synthetic_ohlc(symbol, timeframe, count)


def get_tick(symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (bid, ask, mid)."""
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
    """
    Place a market order.
    Currently SIMULATED until live trading WebSocket is fully connected.
    When DERIV_TOKEN is present we log that we are ready for live.
    """
    if HAS_TOKEN:
        print(f"[Deriv] TOKEN present — would place LIVE {direction.upper()} "
              f"{symbol} stake={stake} entry={entry} SL={sl} TP={tp}")
        # Real proposal + buy will be added in the next live-wiring step
        return True, {
            "contract_id": f"LIVE_READY_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "status": "token_present_simulated",
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    print(f"[Deriv] SIMULATED place_order {direction.upper()} {symbol} "
          f"stake={stake} entry={entry} SL={sl} TP={tp}")
    return True, {
        "contract_id": f"SIM_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "status": "simulated",
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


def close_position(trade: dict) -> Tuple[bool, Optional[Dict]]:
    """Close an open position."""
    if HAS_TOKEN:
        print(f"[Deriv] TOKEN present — would close LIVE {trade.get('id')}")
        return True, {"sold_for": trade.get("entry"), "status": "token_present_simulated"}

    print(f"[Deriv] SIMULATED close_position {trade.get('id')}")
    return True, {"sold_for": trade.get("entry")}


def get_balance() -> Optional[float]:
    """Return account balance if token is available, else None."""
    if not HAS_TOKEN:
        return None
    # Will be implemented with real authorize + balance call
    return None


# -------------------------------------------------
# Status helper (useful for debugging on GitHub Actions)
# -------------------------------------------------
def status() -> dict:
    return {
        "has_token": HAS_TOKEN,
        "app_id": DERIV_APP_ID,
        "account_set": bool(DERIV_ACCOUNT),
        "requests_available": HAS_REQUESTS,
        "force_synthetic": FORCE_SYNTHETIC,
    }


if __name__ == "__main__":
    print("Deriv client status:", status())
    candles = fetch_candles("EURUSD", "H4", 5)
    print(f"Sample candles: {len(candles)}")
    if candles:
        print("Last candle:", candles[-1])
