"""
utils/state.py
--------------
Simple state persistence for GitHub Actions / cron restarts.
Uses only the standard library (json).
"""

import json
import os
from datetime import datetime
from typing import Any

try:
    from config import STATE_FILE, TRADE_HISTORY_FILE
except ImportError:
    STATE_FILE = "data/state.json"
    TRADE_HISTORY_FILE = "data/trade_history.json"


def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _default_state() -> dict:
    return {
        "active_trades": [],
        "active_signals": [],
        "closed_today": [],
        "per_symbol": {},
        "last_retina_run": None,
        "last_lgn_run": None,
        "equity": 1000.0,
        "daily_pnl_pips": 0.0,
        "daily_trades": 0,
        "last_reset_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "updated_at": None,
    }


def load_state() -> dict:
    _ensure_dir(STATE_FILE)
    if not os.path.exists(STATE_FILE):
        return _default_state()
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        for k, v in _default_state().items():
            if k not in state:
                state[k] = v
        return state
    except Exception as e:
        print(f"[State] Load error: {e} — using default")
        return _default_state()


def save_state(state: dict):
    _ensure_dir(STATE_FILE)
    state["updated_at"] = datetime.utcnow().isoformat()
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
        print(f"[State] Saved → {STATE_FILE} ({os.path.getsize(STATE_FILE)} bytes)")
    except Exception as e:
        print(f"[State] Save error: {e}")


def reset_daily_if_needed(state: dict) -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("last_reset_date") != today:
        state["closed_today"] = []
        state["daily_pnl_pips"] = 0.0
        state["daily_trades"] = 0
        state["last_reset_date"] = today
        print("[State] New day — daily counters reset")
    return state


def get_active_trades(state: dict, symbol: str = None) -> list:
    trades = state.get("active_trades", [])
    if symbol:
        return [t for t in trades if t.get("symbol") == symbol and t.get("status") == "active"]
    return [t for t in trades if t.get("status") == "active"]


def register_trade(state: dict, trade: dict) -> dict:
    if not trade.get("placed") or not trade.get("id"):
        return state
    existing_ids = {t.get("id") for t in state.get("active_trades", [])}
    if trade["id"] not in existing_ids:
        trade.setdefault("status", "active")
        state.setdefault("active_trades", []).append(trade)
        state["daily_trades"] = state.get("daily_trades", 0) + 1
        print(f"[State] Registered active trade: {trade['id']}")
    return state


def update_active_trades(state: dict, still_active: list, closed_now: list) -> dict:
    state["active_trades"] = still_active
    for t in closed_now:
        state.setdefault("closed_today", []).append(t)
        pnl = t.get("pnl_pips") or 0.0
        state["daily_pnl_pips"] = state.get("daily_pnl_pips", 0.0) + pnl
    return state
