# Trading Bot Configuration

# === TRADING WINDOW ===
# The time window (in minutes before expiration) to execute trades
TRADE_WINDOW_MIN = 1   # Start checking conditions at this many minutes before expiration
TRADE_WINDOW_MAX = 14  # Stop checking conditions at this many minutes before expiration

# === TECHNICAL INDICATORS ===
BOLLINGER_PERIOD = 20
BOLLINGER_STD_DEV = 1.75

ATR_PERIOD = 14
ATR_MULTIPLIER = 1.5

# === BARRIER THRESHOLDS ===
ORDER_BOOK_RATIO_MIN = 2  # Minimum ratio for order book depth barrier

SHARE_PRICE_MIN = 0.60  # Minimum acceptable share price
SHARE_PRICE_MAX = 0.85  # Maximum acceptable share price

# === MONITORING ===
LOOP_SLEEP_SECONDS = 5  # How often to check market conditions (seconds)
NEXT_MARKET_WAIT_SECONDS = 10  # Wait time before moving to next market (seconds)
