# Trading Bot Configuration
# === REAL TRADING ===
REAL_TRADE = True  # Set to True to execute real trades, False for simulation only
TRADE_AMOUNT = 7  # Trade amount in Shares (increased to 7 to avoid "min 5" errors on partial fills)

# === CLOSE CONDITIONS ===
CLOSE_ON_TP = False  # Take Profit: Close position when target price is reached
CLOSE_TP_PRICE = 0.99  # Take Profit: Close position when share price reaches this level
CLOSE_SL_SHARE_DROP_PERCENT = 50  # Stop Loss: Close if share price drops by this % from entry
CLOSE_ON_STRIKE = True  # Stop Loss: Close on strike price hit
# === SCORING & TRADE EXECUTION ===
SCORE_THRESHOLD = 70  # Minimum total score required to execute a trade (0-100)

# === SCORING WEIGHTS ===
WEIGHT_BOLLINGER = 50
WEIGHT_ATR = 50

# === BARRIER THRESHOLDS (HARD CONSTRAINTS) ===
SHARE_PRICE_MAX = 0.95  # Maximum acceptable share price

# === TRADING WINDOW ===
# The time window (in minutes before expiration) to execute trades
TRADE_WINDOW_MIN = 1   # Start checking conditions at this many minutes before expiration
TRADE_WINDOW_MAX = 14  # Stop checking conditions at this many minutes before expiration

