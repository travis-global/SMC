"""
bot.py — Main Entry Point (dual mode)
=====================================
Called by GitHub Actions (or cron) with --mode h4 or --mode m15.

--mode h4  : Runs Retina on 4H for all symbols, stages signals, updates state
--mode m15 : Runs LGN (15M) + V1 + Extrastriate, manages trades

Notifications:
  Telegram → frequent (signals, placed, closed, cycle summaries)
  Email    → once per day full report only

Profitability upgrades vs original:
  - Structure on true 4H (not H1)
  - Confirmation on 15M (not 5M)
  - R:R floor 1.8 + risk-% sizing
  - Max 1 open trade per symbol
  - Strict HTF trend + session filters
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from config import SYMBOLS, MAX_OPEN_TRADES, RISK_PERCENT, MAX_DAILY_TRADES
except ImportError:
    SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    MAX_OPEN_TRADES = 3
    RISK_PERCENT = 0.75
    MAX_DAILY_TRADES = 3

# Prefer split state_manager if present; fall back to simple state
HAS_SPLIT_STATE = False
try:
    from utils.state_manager import (
        load_h1_state, save_h1_state,
        load_full_state, save_m5_state,
        update_h1_state, update_m5_state,
        get_symbol_retina, get_open_trades,
    )
    HAS_SPLIT_STATE = True
except ImportError:
    try:
        from utils.state import (
            load_state, save_state,
            get_active_trades, register_trade, update_active_trades,
        )
    except ImportError:
        # Absolute minimum fallback — still writes data/state.json
        import json as _json
        _STATE_PATH = os.path.join("data", "state.json")

        def load_state():
            try:
                if os.path.exists(_STATE_PATH):
                    with open(_STATE_PATH, "r") as f:
                        return _json.load(f)
            except Exception:
                pass
            return {
                "active_trades": [], "active_signals": [], "closed_today": [],
                "per_symbol": {}, "equity": 1000.0, "daily_trades": 0,
            }

        def save_state(state):
            try:
                os.makedirs("data", exist_ok=True)
                with open(_STATE_PATH, "w") as f:
                    _json.dump(state, f, indent=2, default=str)
                print(f"[State] Saved → {_STATE_PATH}")
            except Exception as e:
                print(f"[State] Save failed: {e}")

        def get_active_trades(state, symbol=None):
            trades = state.get("active_trades", [])
            if symbol:
                return [t for t in trades if t.get("symbol") == symbol]
            return trades

        def register_trade(state, trade):
            return state

        def update_active_trades(state, still_active, closed_now):
            return state

try:
    from utils.telegram_notifier import (
        notify_h1_complete, notify_m5_summary, notify_error,
        notify_signals_expired, notify_trade_placed, notify_trade_closed,
    )
except ImportError:
    def notify_h1_complete(*a, **k): pass
    def notify_m5_summary(*a, **k): pass
    def notify_error(*a, **k): pass
    def notify_signals_expired(*a, **k): pass
    def notify_trade_placed(*a, **k): pass
    def notify_trade_closed(*a, **k): pass

try:
    from utils.email_notifier import notify_daily_report, notify_error as email_error
except ImportError:
    def notify_daily_report(*a, **k): pass
    def email_error(*a, **k): pass


# =========================================================
# H4 MODE — Structure + staging (was H1)
# =========================================================
def run_h4_mode():
    from retina import run_retina
    from lgn import run_lgn

    print(f"\n[H4] Starting scan — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"[H4] Symbols: {len(SYMBOLS)}")

    if HAS_SPLIT_STATE:
        state = load_h1_state()
    else:
        state = load_state()
        state.setdefault("per_symbol", {})
        state.setdefault("active_signals", [])

    symbol_results = []

    for symbol in SYMBOLS:
        try:
            print(f"\n[H4] {symbol} — running Retina (4H)...")
            retina_result = run_retina(symbol=symbol)

            obs = len(retina_result.get("order_blocks", []))
            tls = len(retina_result.get("trendlines", []))
            confirmed = len(retina_result.get("confirmed_trendlines", []))
            print(f"[H4] {symbol} — OBs:{obs}  Trendlines:{tls}  Confirmed:{confirmed}")

            # Stage LGN signals (only OB + confirmed Trendlines)
            lgn_signals = run_lgn(retina_result, symbol=symbol)
            if lgn_signals:
                print(f"[H4] {symbol} — {len(lgn_signals)} signal(s) staged")

            if HAS_SPLIT_STATE:
                state = update_h1_state(state, symbol, retina_result)
            else:
                # Store only the latest clean indicators
                state["per_symbol"][symbol] = {
                    "order_blocks":         retina_result.get("order_blocks", []),
                    "trendlines":           retina_result.get("trendlines", []),
                    "confirmed_trendlines": retina_result.get("confirmed_trendlines", []),
                    "structure":            retina_result.get("structure", []),
                    "swings":               retina_result.get("swings", []),
                }

            existing_ids = {s.get("id") for s in state.get("active_signals", [])}
            fresh_staged = []
            for sig in lgn_signals:
                sig["symbol"] = symbol
                sig["staged_at"] = datetime.utcnow().isoformat()
                sig["confirmed"] = False
                sig["status"] = "pending"
                sig["id"] = (
                    f"{symbol}_{sig['pattern']}_{sig['direction']}_"
                    f"{sig.get('trigger_price')}"
                )
                if sig["id"] not in existing_ids:
                    state.setdefault("active_signals", []).append(sig)
                    existing_ids.add(sig["id"])
                    fresh_staged.append(sig)

            symbol_results.append({
                "symbol": symbol,
                "obs": obs,
                "tls": tls,
                "confirmed": confirmed,
                "signals": fresh_staged,
            })

        except Exception as e:
            print(f"[H4] {symbol} error: {e}")
            traceback.print_exc()
            notify_error(f"H4 {symbol}", str(e))
            try:
                email_error(f"H4 {symbol}", str(e))
            except Exception:
                pass

    if HAS_SPLIT_STATE:
        save_h1_state(state)
    else:
        state["last_retina_run"] = datetime.utcnow().isoformat()
        save_state(state)

    # Make it obvious in the Actions log
    n_signals = len(state.get("active_signals", []))
    n_symbols = len(state.get("per_symbol", {}))
    print(f"\n[H4] Scan complete — state saved")
    print(f"[H4] Symbols stored: {n_symbols} | Staged signals: {n_signals}")
    if os.path.exists("data/state.json"):
        print(f"[H4] File ready: data/state.json ({os.path.getsize('data/state.json')} bytes)")
    else:
        print("[H4] WARNING: data/state.json was not created")
    try:
        notify_h1_complete(symbol_results)
    except Exception:
        pass


# =========================================================
# M15 MODE — Confirmation, execution, monitoring (was M5)
# =========================================================
def run_m15_mode():
    from lgn import run_lgn
    from v1 import run_v1
    from extrastriate import monitor_cycle, register_trades

    SIGNAL_MAX_AGE_HOURS = 6   # slightly longer than 4h — 15M is slower

    print(f"\n[M15] Starting execution — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    if HAS_SPLIT_STATE:
        state = load_full_state()
    else:
        state = load_state()

    now = datetime.utcnow()

    if not state.get("per_symbol"):
        print("[M15] No H4 data in state yet — skip to protect state")
        return

    # Expire stale staged signals
    executed_ids = set()
    fresh_pending = []
    just_expired = []

    for sig in state.get("active_signals", []):
        if sig.get("status") in ("executed", "expired"):
            executed_ids.add(sig.get("id"))
            continue
        staged_at = sig.get("staged_at")
        if staged_at:
            try:
                age = now - datetime.fromisoformat(staged_at)
                if age.total_seconds() > SIGNAL_MAX_AGE_HOURS * 3600:
                    sig["status"] = "expired"
                    just_expired.append(sig)
                    print(f"[M15] Expired: {sig.get('symbol')} "
                          f"{sig.get('pattern')} {sig.get('direction')}")
                    continue
            except Exception:
                pass
        fresh_pending.append(sig)

    pending_by_symbol = {}
    for sig in fresh_pending:
        pending_by_symbol.setdefault(sig.get("symbol"), []).append(sig)

    try:
        notify_signals_expired(just_expired)
    except Exception:
        pass

    total_placed = 0
    total_filtered = 0
    total_staged_consumed = 0
    all_open_trades = []

    for symbol in SYMBOLS:
        try:
            if HAS_SPLIT_STATE:
                retina_data = get_symbol_retina(state, symbol)
            else:
                retina_data = state.get("per_symbol", {}).get(symbol)

            if not retina_data:
                continue

            # Rebuild only the clean keys that LGN expects
            retina_result = {
                "order_blocks":         retina_data.get("order_blocks", []),
                "trendlines":           retina_data.get("trendlines", []),
                "confirmed_trendlines": retina_data.get("confirmed_trendlines", []),
                "structure":            retina_data.get("structure", []),
                "swings":               retina_data.get("swings", []),
                "data":                 retina_data.get("ohlc", []),
            }

            # Fresh 15M confirmation
            lgn_signals = run_lgn(retina_result, symbol=symbol)

            # Max 1 open trade per symbol
            if HAS_SPLIT_STATE:
                open_trades = get_open_trades(state, symbol)
            else:
                open_trades = [t for t in state.get("active_trades", [])
                               if t.get("symbol") == symbol and t.get("status") == "active"]

            already_open = [t for t in open_trades if t.get("status") == "active"]
            if already_open:
                remaining, closed_trades, _ = monitor_cycle(open_trades, symbol)
                all_open_trades.extend(remaining)
                state.setdefault("closed_trades", []).extend(closed_trades)
                if HAS_SPLIT_STATE:
                    state = update_m5_state(state, symbol, new_signals=[], trade_updates=remaining)
                else:
                    other = [t for t in state.get("active_trades", []) if t.get("symbol") != symbol]
                    state["active_trades"] = other + remaining
                    for t in closed_trades:
                        state["daily_pnl_pips"] = state.get("daily_pnl_pips", 0) + (t.get("pnl_pips") or 0)
                continue

            # Merge staged + fresh
            lgn_ids = {
                f"{symbol}_{s['pattern']}_{s['direction']}_{s.get('trigger_price')}"
                for s in lgn_signals
            }
            staged_to_execute = []
            for ps in pending_by_symbol.get(symbol, []):
                if ps.get("id") in executed_ids:
                    continue
                if ps.get("id") not in lgn_ids:
                    staged_to_execute.append(ps)
                    lgn_ids.add(ps.get("id"))

            combined = lgn_signals + staged_to_execute
            if len(combined) > 1:
                print(f"[M15] {symbol} — {len(combined)} signals, taking best only")
                combined = combined[:1]

            # Hard daily trade limit (selectivity rule)
            try:
                from config import MAX_DAILY_TRADES as _MDT
            except Exception:
                _MDT = 3
            daily_count = state.get("daily_trades", 0)
            if daily_count >= _MDT and combined:
                print(f"[M15] Daily trade limit ({_MDT}) reached — skipping new entries")
                combined = []

            if combined:
                print(f"[M15] {symbol} — {len(lgn_signals)} fresh + "
                      f"{len(staged_to_execute)} staged → V1")
                v1_records = run_v1(combined, retina_result, symbol=symbol)
            else:
                v1_records = []

            total_placed += sum(1 for r in v1_records if r.get("placed"))
            total_filtered += sum(1 for r in v1_records if r.get("filtered"))

            any_placed = any(r.get("placed") for r in v1_records)
            for ps in staged_to_execute:
                if any_placed and ps.get("status") != "executed":
                    ps["status"] = "executed"
                    ps["confirmed"] = True
                    executed_ids.add(ps.get("id"))
                    total_staged_consumed += 1

            register_trades(open_trades, v1_records)
            remaining, closed_now, _ = monitor_cycle(open_trades, symbol)
            all_open_trades.extend(remaining)
            if closed_now:
                state.setdefault("closed_trades", []).extend(closed_now)

            if HAS_SPLIT_STATE:
                state = update_m5_state(state, symbol, new_signals=lgn_signals, trade_updates=remaining)
            else:
                other = [t for t in state.get("active_trades", []) if t.get("symbol") != symbol]
                state["active_trades"] = other + remaining
                for t in closed_now:
                    state["daily_pnl_pips"] = state.get("daily_pnl_pips", 0) + (t.get("pnl_pips") or 0)
                for r in v1_records:
                    if r.get("placed"):
                        state["daily_trades"] = state.get("daily_trades", 0) + 1

        except Exception as e:
            print(f"[M15] {symbol} error: {e}")
            traceback.print_exc()
            notify_error(f"M15 {symbol}", str(e))

    # Persist signal statuses
    state["active_signals"] = fresh_pending + [
        s for s in state.get("active_signals", [])
        if s.get("status") in ("executed", "expired") and s.get("id") in executed_ids
    ]

    if HAS_SPLIT_STATE:
        save_m5_state(state)
    else:
        save_state(state)

    print(f"\n[M15] Cycle complete — state saved")

    # ── Daily EMAIL report (once per day, ~21:00–23:59 UTC) ──
    if now.hour >= 21:
        last_report = state.get("last_daily_report")
        today = now.strftime("%Y-%m-%d")
        if last_report != today:
            try:
                closed_today = state.get("closed_trades", [])
                # Prefer daily_log if available
                try:
                    from utils.daily_log import load_daily_log, clear_daily_log
                    log = load_daily_log(today)
                    log_trades = log.get("trades", []) or closed_today
                except Exception:
                    log_trades = closed_today
                    def clear_daily_log(d): pass

                net = sum(t.get("pnl_pips") or 0 for t in log_trades)
                notify_daily_report(
                    date_str=today,
                    log_trades=log_trades,
                    open_trades=all_open_trades,
                    net_pips=net,
                    notes="Auto daily report",
                )
                try:
                    clear_daily_log(today)
                except Exception:
                    pass
                state["last_daily_report"] = today
                if HAS_SPLIT_STATE:
                    save_m5_state(state)
                else:
                    save_state(state)
                print(f"[M15] Daily EMAIL report sent for {today}")
            except Exception as e:
                print(f"[M15] Daily email error: {e}")

    # Telegram cycle summary (frequent)
    try:
        notify_m5_summary(
            placed=total_placed,
            filtered=total_filtered,
            staged_consumed=total_staged_consumed,
            expired=len(just_expired),
            open_trades=all_open_trades,
        )
    except Exception:
        pass


# =========================================================
# ENTRY
# =========================================================
def main():
    parser = argparse.ArgumentParser(description="SMC Visual Pathway Bot")
    parser.add_argument(
        "--mode",
        choices=["h4", "m15", "h1", "m5"],  # h1/m5 kept as aliases
        required=True,
        help="h4 = 4H structure scan, m15 = 15M confirm + execute + monitor",
    )
    args = parser.parse_args()

    mode = args.mode
    if mode in ("h1", "h4"):
        run_h4_mode()
    else:
        run_m15_mode()


if __name__ == "__main__":
    main()
