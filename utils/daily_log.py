"""
utils/daily_log.py
-------------------
This module was imported from v1.py, extrastriate.py and bot.py
(log_placed_trade, log_closed_trade, load_daily_log, clear_daily_log)
but never actually existed — every call site wrapped the import in a
silent try/except, so none of it was doing anything.

Two things live here now:

1. A per-day JSON log (data/daily_logs/YYYY-MM-DD.json) that bot.py's
   nightly email report reads and then clears — this restores that
   internal feature.

2. Two append-only CSVs meant for YOUR OWN analysis, not just the
   bot's internal use. These are never cleared, so they accumulate
   for as long as the bot runs:

     - data/trade_log.csv — one row per trade. A row is written when
       the trade opens and updated in place when it closes, so a
       finished trade's full lifecycle (entry, SL/TP, R:R, close
       price, pips, WIN/LOSS) is one row you can pull into pandas,
       Excel, or Google Sheets.

     - data/scan_log.csv — one row per symbol per H4 scan cycle:
       how many order blocks / trendlines / confirmed trendlines
       Retina found, which data source (live vs synthetic) was
       used, and how many signals got staged. This is the funnel
       data — useful for seeing whether filters are too strict,
       whether a symbol is starved for setups, or whether synthetic
       fallback is quietly eating a chunk of your history.

Both use only the standard library (csv, json) — nothing new to
install on top of requirements.txt.
"""

import csv
import json
import os
from datetime import datetime

DAILY_LOG_DIR = "data/daily_logs"
TRADE_LOG_CSV = "data/trade_log.csv"
SCAN_LOG_CSV = "data/scan_log.csv"

TRADE_CSV_FIELDS = [
    "id", "contract_id", "symbol", "pattern", "direction",
    "entry", "sl", "tp", "rr", "lot",
    "placed_at", "placed_status",
    "closed_at", "close_price", "close_reason", "exit_type",
    "pnl_pips", "result",
]

SCAN_CSV_FIELDS = [
    "timestamp", "symbol", "data_source",
    "order_blocks", "trendlines", "confirmed_trendlines",
    "signals_staged",
]


def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


# =========================================================
# Per-day JSON log — read/cleared by bot.py's nightly report
# =========================================================
def _daily_path(date_str: str) -> str:
    return os.path.join(DAILY_LOG_DIR, f"{date_str}.json")


def load_daily_log(date_str: str) -> dict:
    path = _daily_path(date_str)
    if not os.path.exists(path):
        return {"date": date_str, "trades": []}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[DailyLog] load error: {e} — using empty log")
        return {"date": date_str, "trades": []}


def _save_daily_log(date_str: str, log: dict):
    path = _daily_path(date_str)
    _ensure_dir(path)
    try:
        with open(path, "w") as f:
            json.dump(log, f, indent=2, default=str)
    except Exception as e:
        print(f"[DailyLog] save error: {e}")


def clear_daily_log(date_str: str):
    _save_daily_log(date_str, {"date": date_str, "trades": []})


def log_placed_trade(rec: dict):
    today = _today()
    log = load_daily_log(today)
    log.setdefault("trades", []).append(dict(rec))
    _save_daily_log(today, log)
    _upsert_trade_csv_row(rec)


def log_closed_trade(trade: dict):
    today = _today()
    log = load_daily_log(today)
    trades = log.setdefault("trades", [])
    for t in trades:
        if t.get("id") == trade.get("id"):
            t.update(trade)
            break
    else:
        trades.append(dict(trade))
    _save_daily_log(today, log)
    _upsert_trade_csv_row(trade)


# =========================================================
# data/trade_log.csv — one row per trade, upserted by id
# =========================================================
def _read_trade_csv() -> list:
    if not os.path.exists(TRADE_LOG_CSV):
        return []
    try:
        with open(TRADE_LOG_CSV, "r", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[DailyLog] trade_log.csv read error: {e}")
        return []


def _write_trade_csv(rows: list):
    _ensure_dir(TRADE_LOG_CSV)
    try:
        with open(TRADE_LOG_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in TRADE_CSV_FIELDS})
    except Exception as e:
        print(f"[DailyLog] trade_log.csv write error: {e}")


def _upsert_trade_csv_row(rec: dict):
    pnl = rec.get("pnl_pips")
    result = "" if pnl is None else ("WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BE")

    row = {
        "id":            rec.get("id", ""),
        "contract_id":   rec.get("contract_id", ""),
        "symbol":        rec.get("symbol", ""),
        "pattern":       rec.get("pattern", ""),
        "direction":     rec.get("direction", ""),
        "entry":         rec.get("entry", ""),
        "sl":            rec.get("sl", ""),
        "tp":            rec.get("tp", ""),
        "rr":            rec.get("rr", ""),
        "lot":           rec.get("lot", ""),
        "placed_at":     rec.get("placed_at", rec.get("timestamp", "")),
        "placed_status": (rec.get("order_result") or {}).get("status", ""),
        "closed_at":     rec.get("close_time", ""),
        "close_price":   rec.get("close_price", ""),
        "close_reason":  rec.get("close_reason", ""),
        "exit_type":     rec.get("exit_type", ""),
        "pnl_pips":      pnl if pnl is not None else "",
        "result":        result,
    }

    rows = _read_trade_csv()
    for i, existing in enumerate(rows):
        if existing.get("id") == row["id"]:
            rows[i] = row
            break
    else:
        rows.append(row)

    _write_trade_csv(rows)


# =========================================================
# data/scan_log.csv — one row per symbol per H4 scan cycle
# =========================================================
def log_scan_cycle(symbol: str, data_source: str, order_blocks: int,
                    trendlines: int, confirmed_trendlines: int,
                    signals_staged: int):
    _ensure_dir(SCAN_LOG_CSV)
    is_new = not os.path.exists(SCAN_LOG_CSV)
    try:
        with open(SCAN_LOG_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SCAN_CSV_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({
                "timestamp":            datetime.utcnow().isoformat(),
                "symbol":               symbol,
                "data_source":          data_source,
                "order_blocks":         order_blocks,
                "trendlines":           trendlines,
                "confirmed_trendlines": confirmed_trendlines,
                "signals_staged":       signals_staged,
            })
    except Exception as e:
        print(f"[DailyLog] scan_log.csv write error: {e}")
