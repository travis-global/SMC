"""
CONFIG — Single source of truth for the SMC Visual Pathway Bot
==============================================================
All tunable parameters live here. Change values here, nowhere else.
Designed for Termux + GitHub Actions (no heavy dependencies).
"""

import os

# =========================================================
# ACCOUNT & RISK
# =========================================================
# Risk a fixed % of equity per trade. Never risk more than this.
RISK_PERCENT          = 0.75          # 0.75% of account per trade (conservative)
MAX_OPEN_TRADES       = 3             # hard limit on concurrent positions
MAX_OPEN_PER_SYMBOL   = 1             # never more than one position per pair
MAX_DAILY_TRADES      = 3             # max NEW trades per day (selectivity)
MAX_DAILY_LOSS_PCT    = 3.0           # stop trading for the day if equity drops this %
DEFAULT_EQUITY        = 1000.0        # fallback if equity cannot be fetched (for sizing)

# =========================================================
# TIMEFRAMES
# =========================================================
# Retina = higher timeframe bias & structure
RETINA_TIMEFRAME      = "H4"          # 4-hour candles (Deriv uses H4)
RETINA_CANDLES        = 300           # lookback for structure

# LGN = entry confirmation timeframe (changed from M5 → M15)
LGN_TIMEFRAME         = "M15"         # 15-minute confirmation
LGN_CANDLES           = 150           # lookback for 15M confirmation
CONFIRM_CANDLES       = 4             # only last 4 × 15M candles count for confirmation (~1 hour)

# =========================================================
# SMC FILTERS (profitability upgrades)
# =========================================================
MIN_RR                = 1.8           # raised from 1.5 — only take quality R:R
ENTRY_BUFFER_PIPS     = 3             # minimum pips of closing intent on 15M
ZONE_PROXIMITY_PIPS   = 5             # how close price must be to zone
TRENDLINE_PROX_PIPS   = 4

# Only trade high-confluence setups
POI_MIN_TIER          = 2             # Tier 2+ (was Tier 3 only — too rare; Tier 2 is still strong)
REQUIRE_HTF_TREND     = True          # never trade against 4H bias
REQUIRE_MOMENTUM      = True          # 15M body must show conviction

# =========================================================
# SESSION FILTER (UTC)
# =========================================================
# Forex / Gold: London + New York only
SESSION_START_UTC     = 7
SESSION_END_UTC       = 21

# =========================================================
# SYMBOLS
# =========================================================
# Start with a small, high-quality list. Add more only after proving edge.
SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "XAUUSD",          # Gold — excellent SMC structure
    # Add synthetics only after you have proven edge on the majors above
    # "Volatility 75 Index",
]


# =========================================================
# EMAIL (ONLY notification channel — no Telegram)
# =========================================================
# Set these via GitHub Secrets (recommended) or environment variables.
# The code reads: EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO
EMAIL_ENABLED         = True
SMTP_SERVER           = "smtp.gmail.com"
SMTP_PORT             = 587
EMAIL_FROM            = os.getenv("EMAIL_FROM", "your_email@gmail.com")
EMAIL_PASSWORD        = os.getenv("EMAIL_PASSWORD", "your_app_password")
EMAIL_TO              = os.getenv("EMAIL_TO", "your_email@gmail.com")

# What to send by email
EMAIL_ON_SIGNAL       = False
EMAIL_ON_PLACED       = True        # notify when a trade is opened
EMAIL_ON_CLOSED       = True        # notify when a trade is closed
EMAIL_ON_FILTERED     = False
EMAIL_DAILY_SUMMARY   = True

# =========================================================
# STATE & LOGGING
# =========================================================
STATE_FILE            = "data/state.json"
TRADE_HISTORY_FILE    = "data/trade_history.json"
DAILY_LOG_DIR         = "logs"
MONITOR_INTERVAL_SEC  = 60

# =========================================================
# DERIV / EXECUTION
# =========================================================
# All sensitive values come from environment / GitHub Secrets:
#   DERIV_TOKEN, DERIV_APP_ID, DERIV_ACCOUNT
# Never commit real tokens to the repository.
DERIV_APP_ID          = os.getenv("DERIV_APP_ID", "1089")
USE_DEMO              = True        # stay on demo until edge is proven

# =========================================================
# SL BUFFER (in pips — converted to price units inside code)
# =========================================================
SL_BUFFER_PIPS        = 3
