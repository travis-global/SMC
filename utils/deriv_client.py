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
    from config import MULTIPLIER, ACCOUNT_CURRENCY
except ImportError:
    MULTIPLIER = int(os.getenv("DERIV_MULTIPLIER", "100"))
    ACCOUNT_CURRENCY = os.getenv("DERIV_CURRENCY", "USD")

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


class _DerivWSError(Exception):
    pass


def _ws_recv_until(ws, expect_keys, timeout: float = 15.0) -> dict:
    """
    Read messages off an open connection until one contains any of
    expect_keys (e.g. "authorize", "proposal", "buy", "sell"), or an
    "error" envelope arrives, or timeout elapses.
    """
    ws.settimeout(timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.recv()
        data = json.loads(msg)
        if "error" in data:
            raise _DerivWSError(data["error"].get("message", str(data["error"])))
        if any(k in data for k in expect_keys):
            return data
    raise _DerivWSError(f"Timed out waiting for {expect_keys}")


def _authorize(ws):
    ws.send(json.dumps({"authorize": DERIV_TOKEN}))
    resp = _ws_recv_until(ws, ["authorize"])
    return resp["authorize"]


def _pct_distance(entry: float, level: float) -> float:
    if not entry:
        return 0.0
    return abs(level - entry) / entry


def place_order(symbol: str, direction: str, stake: float = 0.01,
                entry: float = None, sl: float = None,
                tp: float = None) -> Tuple[bool, Dict]:
    """
    Places a real MULTUP/MULTDOWN contract on Deriv when a token is
    present and reachable. Returns (True, {...}) ONLY when Deriv has
    actually confirmed a contract_id back — never on a guess. Falls
    back to an explicitly-labeled simulation if the token is missing,
    the socket fails, or Deriv rejects the request.
    """
    if not HAS_TOKEN:
        print(f"[Deriv] No token — SIMULATED place_order {direction.upper()} "
              f"{symbol} stake={stake} entry={entry} SL={sl} TP={tp}")
        return True, {
            "contract_id": f"SIM_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "status": "simulated_no_token",
            "entry": entry, "sl": sl, "tp": tp,
        }

    if not HAS_WS:
        print("[Deriv] websocket-client not installed — cannot place a real order")
        return False, {"error": "websocket-client not installed"}

    deriv_symbol = _to_deriv_symbol(symbol)
    contract_type = "MULTUP" if direction == "long" else "MULTDOWN"

    limit_order = {}
    if sl is not None and entry:
        limit_order["stop_loss"] = round(stake * MULTIPLIER * _pct_distance(entry, sl), 2)
    if tp is not None and entry:
        limit_order["take_profit"] = round(stake * MULTIPLIER * _pct_distance(entry, tp), 2)

    ws_url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        try:
            auth = _authorize(ws)
            if DERIV_ACCOUNT and auth.get("loginid") != DERIV_ACCOUNT:
                raise _DerivWSError(
                    f"Authorized account {auth.get('loginid')} does not match "
                    f"DERIV_ACCOUNT={DERIV_ACCOUNT} — refusing to trade"
                )

            proposal_req = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": ACCOUNT_CURRENCY,
                "multiplier": MULTIPLIER,
                "symbol": deriv_symbol,
            }
            if limit_order:
                proposal_req["limit_order"] = limit_order

            ws.send(json.dumps(proposal_req))
            prop_resp = _ws_recv_until(ws, ["proposal"])
            proposal = prop_resp["proposal"]
            proposal_id = proposal["id"]
            ask_price = proposal["ask_price"]

            ws.send(json.dumps({"buy": proposal_id, "price": ask_price}))
            buy_resp = _ws_recv_until(ws, ["buy"])
            buy = buy_resp["buy"]

            contract_id = buy.get("contract_id")
            if not contract_id:
                raise _DerivWSError(f"Buy confirmed but no contract_id in response: {buy}")

            print(f"[Deriv] LIVE order placed: {direction.upper()} {symbol} "
                  f"contract_id={contract_id} stake={stake} multiplier={MULTIPLIER}")
            return True, {
                "contract_id": contract_id,
                "status": "live",
                "buy_price": buy.get("buy_price"),
                "payout": buy.get("payout"),
                "entry": entry, "sl": sl, "tp": tp,
            }
        finally:
            ws.close()

    except Exception as e:
        print(f"[Deriv] LIVE place_order FAILED for {symbol}: {e} — "
              f"NOT placed, falling back to simulated record")
        return False, {"error": str(e), "status": "live_failed"}


def close_position(trade: dict) -> Tuple[bool, Optional[Dict]]:
    """
    Closes a real Deriv contract by its contract_id. Requires trade to
    carry the contract_id Deriv gave us at buy time — NOT our internal
    V1 record id, which Deriv has never heard of.
    """
    contract_id = trade.get("contract_id")

    if not HAS_TOKEN:
        print(f"[Deriv] No token — SIMULATED close_position {trade.get('id')}")
        return True, {"sold_for": trade.get("entry"), "status": "simulated_no_token"}

    if not contract_id or str(contract_id).startswith("SIM_"):
        print(f"[Deriv] Trade {trade.get('id')} has no real contract_id "
              f"(was never actually placed) — nothing to close on Deriv")
        return True, {"sold_for": trade.get("entry"), "status": "no_real_contract"}

    if not HAS_WS:
        print("[Deriv] websocket-client not installed — cannot close on Deriv")
        return False, {"error": "websocket-client not installed"}

    ws_url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        try:
            _authorize(ws)
            ws.send(json.dumps({"sell": contract_id, "price": 0}))
            sell_resp = _ws_recv_until(ws, ["sell"])
            sell = sell_resp["sell"]
            print(f"[Deriv] LIVE close confirmed: contract_id={contract_id} "
                  f"sold_for={sell.get('sold_for')}")
            return True, {"sold_for": sell.get("sold_for"), "status": "live"}
        finally:
            ws.close()

    except Exception as e:
        print(f"[Deriv] LIVE close_position FAILED for contract_id={contract_id}: {e}")
        return False, {"error": str(e), "status": "live_failed"}


def get_balance() -> Optional[float]:
    if not HAS_TOKEN or not HAS_WS:
        return None
    ws_url = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        try:
            auth = _authorize(ws)
            bal = auth.get("balance")
            return float(bal) if bal is not None else None
        finally:
            ws.close()
    except Exception as e:
        print(f"[Deriv] get_balance failed: {e}")
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
