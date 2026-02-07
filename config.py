# Trading Bot Configuration
# === REAL TRADING ===
REAL_TRADE = True  # Set to True to execute real trades, False for simulation only
TRADE_AMOUNT = 5  # Trade amount in Shares (minimum required is 5 for real trades)
CLOSE_TRADE_ON_TARGET = True  # Whether to automatically close the trade when target price is hit
# === SCORING & TRADE EXECUTION ===
SCORE_THRESHOLD = 60  # Minimum total score required to execute a trade (0-100)

# === SCORING WEIGHTS ===
WEIGHT_BOLLINGER = 50
WEIGHT_ATR = 40
WEIGHT_RSI = 10

# === BARRIER THRESHOLDS (HARD CONSTRAINTS) ===
SHARE_PRICE_MIN = 0.35  # Minimum acceptable share price (BLOCKS TRADE if below)
SHARE_PRICE_MAX = 0.90  # Maximum acceptable share price (BLOCKS TRADE if above)
BB_BANDWIDTH_MIN = 0.01  # Minimum Bollinger Bandwidth (Upper-Lower)/Middle (BLOCKS TRADE if below = squeeze)

# === TRADING WINDOW ===
# The time window (in minutes before expiration) to execute trades
TRADE_WINDOW_MIN = 1   # Start checking conditions at this many minutes before expiration
TRADE_WINDOW_MAX = 14  # Stop checking conditions at this many minutes before expiration

# === TECHNICAL INDICATORS ===
BOLLINGER_PERIOD = 20
BOLLINGER_STD_DEV = 2.0

ATR_PERIOD = 14
ATR_MULTIPLIER = 0.6

# === MONITORING ===
LOOP_SLEEP_SECONDS = 2  # How often to check market conditions (seconds)
NEXT_MARKET_WAIT_SECONDS = 10  # Wait time before moving to next market (seconds)

