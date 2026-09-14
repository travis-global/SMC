"""
Email Notifier
==============
Telegram  = frequent (placed, closed, cycle summaries, errors)
Email     = MILESTONE reports only (7 / 30 / 60 days)
            Detailed enough to forward to your mentor for review.

Uses pure stdlib SMTP (Termux-friendly).
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import traceback
import os
import json

try:
    from config import (
        EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT,
        EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO,
    )
except ImportError:
    EMAIL_ENABLED = False
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    EMAIL_FROM = os.getenv("EMAIL_FROM", "")
    EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_TO = os.getenv("EMAIL_TO", "")


def _send(subject: str, body: str) -> bool:
    if not EMAIL_ENABLED:
        print(f"[Email] DISABLED — would send: {subject}")
        return False
    if not EMAIL_FROM or not EMAIL_PASSWORD or "your_email" in str(EMAIL_FROM):
        # Also try env vars
        email_from = os.getenv("EMAIL_FROM") or EMAIL_FROM
        email_pass = os.getenv("EMAIL_PASSWORD") or EMAIL_PASSWORD
        email_to = os.getenv("EMAIL_TO") or EMAIL_TO
        if not email_from or not email_pass or "your_email" in str(email_from):
            print("[Email] Credentials not configured — skip")
            return False
    else:
        email_from = EMAIL_FROM
        email_pass = EMAIL_PASSWORD
        email_to = EMAIL_TO

    try:
        msg = MIMEMultipart()
        msg["From"] = email_from
        msg["To"] = email_to
        msg["Subject"] = f"[SMC Bot] {subject}"
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(email_from, email_pass)
            server.send_message(msg)
        print(f"[Email] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[Email] FAILED: {e}")
        traceback.print_exc()
        return False


def notify_error(context: str, error: str):
    """Serious pipeline errors only."""
    body = f"Error in {context}\n\n{error}\n\nTime: {datetime.utcnow().isoformat()} UTC"
    _send(f"ERROR — {context}", body)


def notify_trade_placed(trade: dict):
    """Email when a new trade is opened."""
    try:
        from config import EMAIL_ON_PLACED
        if not EMAIL_ON_PLACED:
            return
    except Exception:
        pass

    symbol = trade.get("symbol", "?")
    direction = (trade.get("direction") or "?").upper()
    pattern = trade.get("pattern", "?")
    entry = trade.get("entry")
    sl = trade.get("sl")
    tp = trade.get("tp")
    rr = trade.get("rr")
    lot = trade.get("lot")
    htf = trade.get("htf_bias") or ""

    lines = [
        f"TRADE PLACED — {symbol} {direction}",
        f"Pattern   : {pattern}",
        f"Entry     : {entry}",
        f"Stop Loss : {sl}",
        f"Take Profit: {tp}",
        f"R:R       : {rr}",
        f"Lot       : {lot}",
        f"HTF bias  : {htf}",
        f"Time (UTC): {trade.get('placed_time') or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
        f"ID        : {trade.get('id', '')}",
    ]
    _send(f"PLACED {symbol} {direction} — {pattern}", "\n".join(lines))


def notify_trade_closed(trade: dict):
    """Email when a trade is closed (SL, TP, condition, weekend)."""
    try:
        from config import EMAIL_ON_CLOSED
        if not EMAIL_ON_CLOSED:
            return
    except Exception:
        pass

    symbol = trade.get("symbol", "?")
    direction = (trade.get("direction") or "?").upper()
    pattern = trade.get("pattern", "?")
    entry = trade.get("entry")
    close_price = trade.get("close_price")
    pnl = trade.get("pnl_pips")
    reason = trade.get("close_reason") or trade.get("exit_type") or ""
    result = "WIN" if (pnl or 0) > 0 else ("LOSS" if (pnl or 0) < 0 else "BE")

    lines = [
        f"TRADE CLOSED [{result}] — {symbol} {direction}",
        f"Pattern   : {pattern}",
        f"Entry     : {entry}",
        f"Close     : {close_price}",
        f"PnL pips  : {pnl:+.1f}" if pnl is not None else "PnL pips  : n/a",
        f"Reason    : {reason}",
        f"Time (UTC): {trade.get('close_time') or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
        f"ID        : {trade.get('id', '')}",
    ]
    _send(f"CLOSED [{result}] {symbol} {direction} — {pnl:+.1f}p" if pnl is not None
          else f"CLOSED {symbol} {direction}", "\n".join(lines))


def notify_milestone_report(milestone_days: int, performance: dict):
    """
    Detailed 7 / 30 / 60 day report — forward this email to your mentor.

    Called once when the bot first reaches each milestone
    (days since first closed trade).
    """
    windows = performance.get("windows", {})
    key = f"{milestone_days}d" if milestone_days != "all" else "all"
    w = windows.get(key) or windows.get(str(milestone_days)) or {}

    lines = [
        f"SMC MILESTONE REPORT — {milestone_days}-DAY REVIEW",
        "════════════════════════════════════════════════",
        f"Generated     : {performance.get('generated_at', datetime.utcnow().isoformat())}",
        f"Rules version : {performance.get('rules_version', 'SMC_SELECTIVE_v1')}",
        f"Symbols       : {', '.join(performance.get('allowed_symbols', []))}",
        f"Total closed  : {performance.get('total_closed_trades_loaded', 0)} (all time loaded)",
        "",
        f"── {milestone_days}-DAY WINDOW ──",
        f"Trades        : {w.get('trades', 0)}",
        f"Wins          : {w.get('wins', 0)}",
        f"Losses        : {w.get('losses', 0)}",
        f"Break-even    : {w.get('breakeven', 0)}",
        f"Win rate      : {w.get('win_rate_pct', 0)}%",
        f"Net pips      : {w.get('net_pips', 0):+.1f}",
        f"Avg pips/trade: {w.get('avg_pips', 0):+.2f}",
        f"Avg win       : {w.get('avg_win_pips', 0):+.2f}",
        f"Avg loss      : {w.get('avg_loss_pips', 0):+.2f}",
        f"Profit factor : {w.get('profit_factor', 0)}",
        f"Expectancy    : {w.get('expectancy_pips', 0):+.2f} pips",
        f"Best trade    : {w.get('best_trade_pips', 0):+.1f}",
        f"Worst trade   : {w.get('worst_trade_pips', 0):+.1f}",
        f"Trades/day avg: {w.get('trades_per_day_avg', 0)}",
        "",
    ]

    by_sym = w.get("by_symbol") or {}
    if by_sym:
        lines.append("── BY SYMBOL ──")
        for sym, d in by_sym.items():
            lines.append(
                f"  {sym:12} trades:{d.get('trades',0):3}  "
                f"WR:{d.get('win_rate_pct',0):5.1f}%  "
                f"net:{d.get('net_pips',0):+.1f}"
            )
        lines.append("")

    by_pat = w.get("by_pattern") or {}
    if by_pat:
        lines.append("── BY PATTERN ──")
        for pat, d in by_pat.items():
            lines.append(
                f"  {pat:15} trades:{d.get('trades',0):3}  "
                f"WR:{d.get('win_rate_pct',0):5.1f}%  "
                f"net:{d.get('net_pips',0):+.1f}"
            )
        lines.append("")

    # Include all windows for mentor context
    lines.append("── ALL WINDOWS (context) ──")
    for label in ("7d", "30d", "60d", "all"):
        ww = windows.get(label) or {}
        lines.append(
            f"  {label:4}  trades:{ww.get('trades',0):3}  "
            f"WR:{ww.get('win_rate_pct',0):5.1f}%  "
            f"net:{ww.get('net_pips',0):+.1f}  "
            f"PF:{ww.get('profit_factor',0)}  "
            f"exp:{ww.get('expectancy_pips',0):+.2f}"
        )
    lines.append("")

    lines.append("── MENTOR VERDICT ──")
    lines.append(performance.get("mentor_verdict", "—"))
    lines.append("")
    lines.append("── HOW TO READ ──")
    for k, v in (performance.get("how_to_read") or {}).items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Forward this entire email to your SMC mentor for review.")
    lines.append("Also attach or paste state/performance.json if asked.")
    lines.append("")
    lines.append("--- RAW performance.json (for mentor) ---")
    try:
        lines.append(json.dumps(performance, indent=2, default=str))
    except Exception:
        lines.append("(could not serialize performance dict)")

    subject = f"{milestone_days}-DAY MILESTONE — net {w.get('net_pips', 0):+.1f} pips | PF {w.get('profit_factor', 0)}"
    _send(subject, "\n".join(lines))


# Kept for optional daily use — OFF by default in config
def notify_daily_report(date_str: str, log_trades: list, open_trades: list,
                        net_pips: float = None, notes: str = ""):
    """Optional daily summary. Prefer milestone emails for mentor review."""
    try:
        from config import EMAIL_DAILY_SUMMARY
        if not EMAIL_DAILY_SUMMARY:
            return
    except Exception:
        return

    wins = [t for t in log_trades if (t.get("pnl_pips") or 0) > 0]
    losses = [t for t in log_trades if (t.get("pnl_pips") or 0) < 0]
    be = [t for t in log_trades if (t.get("pnl_pips") or 0) == 0]
    if net_pips is None:
        net_pips = sum(t.get("pnl_pips") or 0 for t in log_trades)

    lines = [
        f"SMC DAILY REPORT — {date_str}",
        f"Closed:{len(log_trades)} W:{len(wins)} L:{len(losses)} BE:{len(be)} Net:{net_pips:+.1f}",
        f"Open:{len(open_trades)}",
        notes or "",
    ]
    _send(f"Daily {date_str} ({net_pips:+.1f})", "\n".join(lines))


def notify_daily_summary(summary: dict):
    notify_daily_report(
        date_str=summary.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
        log_trades=[],
        open_trades=[],
        net_pips=summary.get("net_pips", 0),
        notes=summary.get("notes", ""),
    )
