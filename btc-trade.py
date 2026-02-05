import time
import math
import sys
import os
import re
import json
import csv
import numpy as np
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone

# Load environment variables
load_dotenv()

# Load configuration
from config import (
    TRADE_WINDOW_MIN, TRADE_WINDOW_MAX,
    BOLLINGER_PERIOD, BOLLINGER_STD_DEV,
    ATR_PERIOD, ATR_MULTIPLIER,
    ORDER_BOOK_RATIO_MIN,
    SHARE_PRICE_MIN, SHARE_PRICE_MAX,
    LOOP_SLEEP_SECONDS, NEXT_MARKET_WAIT_SECONDS,
    SCORE_THRESHOLD,
    WEIGHT_BOLLINGER, WEIGHT_ATR, WEIGHT_ORDERBOOK, WEIGHT_PRICE
)

# === ORDER BOOK SCORING THRESHOLDS ===
OB_RATIO_FLOOR = 0.5
OB_RATIO_CAP = 3.0

# --- 1. API IMPORTS ---
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    from py_clob_client.constants import POLYGON
except ImportError:
    try:
        from clob_client.client import ClobClient
        from clob_client.clob_types import ApiCreds
        from clob_client.constants import POLYGON
    except ImportError:
        print("❌ Critical Error: Required library not found.")
        sys.exit(1)

# --- 2. CONFIGURATION (From .env) ---
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE")
MY_ADDRESS = os.getenv("MY_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# --- 2B. CHAINLINK BTC/USD STREAM CONFIG ---
CHAINLINK_STREAM_URLS = [
    os.getenv("CHAINLINK_STREAM_API_URL", "").strip(),
    "https://data.chain.link/api/streams/btc-usd",
    "https://data.chain.link/api/streams/btc-usd-cexprice-streams",
    "https://data.chain.link/streams/btc-usd-cexprice-streams",
    "https://data.chain.link/streams/btc-usd",
]

CHAINLINK_FEED_FALLBACK_URLS = [
    os.getenv("CHAINLINK_FEED_API_URL", "").strip(),
    "https://data.chain.link/feeds/ethereum/mainnet/btc-usd",
]

# --- 3. TECHNICAL INDICATORS ---

def calculate_bollinger_bands(closes, period=20, std_dev=2.0):
    """Calculate Bollinger Bands"""
    if len(closes) < period:
        return None, None, None
    
    closes_array = np.array(closes[-period:])
    middle_band = np.mean(closes_array)
    std = np.std(closes_array)
    upper_band = middle_band + (std_dev * std)
    lower_band = middle_band - (std_dev * std)
    
    return upper_band, middle_band, lower_band

def calculate_atr(highs, lows, closes, period=14):
    """Calculate Average True Range"""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    
    true_ranges = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        true_range = max(high_low, high_close, low_close)
        true_ranges.append(true_range)
    
    if len(true_ranges) < period:
        return None
    
    atr = np.mean(true_ranges[-period:])
    return atr

def calculate_rsi(closes, period=14):
    """Calculate Relative Strength Index (RSI)"""
    if len(closes) < period + 1:
        return None
    
    # Calculate price changes
    deltas = np.diff(closes)
    
    # Separate gains and losses
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    # Calculate average gain and loss
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    
    # Avoid division by zero
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    
    # Calculate RS and RSI
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi

def analyze_order_book_barrier(order_book, current_price, target_price, atr_value, scan_depth_override=None, direction_override=None):
    """
    Analyze order book but ONLY look at depth within the immediate ATR range.
    This prevents huge walls far away from scaring the bot.
    Only the orders within volatility range matter.
    """
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])

    def _parse_levels(levels):
        parsed = []
        for level in levels:
            if isinstance(level, dict):
                price = level.get('price')
                size = level.get('size') or level.get('amount')
            else:
                try:
                    price = level[0]
                    size = level[1]
                except Exception:
                    continue
            try:
                parsed.append((float(price), float(size)))
            except (TypeError, ValueError):
                continue
        return parsed

    bids = _parse_levels(bids)
    asks = _parse_levels(asks)
    
    # We only care about walls within the "Volatility Range"
    # If ATR is None, default to a tight range (e.g. 0.1% of price)
    scan_depth = scan_depth_override if scan_depth_override is not None else (atr_value if atr_value else current_price * 0.001)
    
    if direction_override in ("UP", "DOWN"):
        direction = direction_override
    elif current_price > target_price:
        direction = "UP"
    else:
        direction = "DOWN"

    if direction == "UP":
        # Support: Bids close to current price (cushion)
        # Scan from Current Price down to (Current - Scan Depth)
        relevant_bids = [bid[1] for bid in bids
                 if (current_price - scan_depth) < bid[0] < current_price]

        # Resistance: Asks immediately above (roof)
        # Scan from Current Price up to (Current + Scan Depth)
        relevant_asks = [ask[1] for ask in asks
                 if current_price < ask[0] < (current_price + scan_depth)]
    else:
        # Resistance: Asks close to current price
        relevant_asks = [ask[1] for ask in asks
                 if current_price < ask[0] < (current_price + scan_depth)]

        # Support: Bids immediately below
        relevant_bids = [bid[1] for bid in bids
                 if (current_price - scan_depth) < bid[0] < current_price]

    bid_volume = sum(relevant_bids)
    ask_volume = sum(relevant_asks)

    # Calculate Ratio
    if direction == "UP":
        # We want high Bid Volume relative to Ask Volume immediate overhead
        ratio = bid_volume / ask_volume if ask_volume > 0 else 10.0
    else: 
        # We want high Ask Volume relative to Bid Volume immediate underfoot
        ratio = ask_volume / bid_volume if bid_volume > 0 else 10.0

    return bid_volume, ask_volume, ratio, direction

def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def fetch_chainlink_btc_usd_price():
    """
    Fetch BTC/USD price from Kraken only (used as Chainlink proxy).
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(
            "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD",
            headers=headers,
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
        price = _safe_float(data.get("result", {}).get("XXBTZUSD", {}).get("c", [None])[0])
        if price and 20000 <= price <= 150000:
            return price
    except Exception:
        pass

    return None

# --- 4. FETCH CURRENT BTC 15M MARKET AUTOMATICALLY ---
def find_current_btc_15m_market():
    """
    Finds the current LIVE BTC 15m market by scraping the Polymarket crypto/15M page.
    This gets the actual live market shown on the website.
    """
    print("🔍 Searching for current LIVE BTC 15m market on Polymarket...")
    
    try:
        # First, try to scrape the live market from the crypto/15M page
        print("   Fetching live market from Polymarket website...")
        crypto_page_url = "https://polymarket.com/crypto/15M"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        page_response = requests.get(crypto_page_url, headers=headers, timeout=10)
        page_response.raise_for_status()
        
        # Look for the market slug in the HTML (e.g., /event/btc-updown-15m-1769900400)
        import re
        market_links = re.findall(r'/event/(btc-updown-15m-\d{10})', page_response.text)
        
        if market_links:
            # Get the first one (should be the live market)
            live_slug = market_links[0]
            print(f"   ✅ Found LIVE market on website: {live_slug}")
            
            # Fetch the individual market page to get strike price and outcome prices
            strike_price = None
            outcome_prices = {'up': None, 'down': None}
            page_clob_token_ids = None

            market_url = f"https://polymarket.com/event/{live_slug}"
            timestamp_match = re.search(r'-(\d{10})$', live_slug)
            target_end_time = None
            if timestamp_match:
                market_start_timestamp = int(timestamp_match.group(1))
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(market_start_timestamp, tz=timezone.utc)
                target_end_time = dt.strftime('%Y-%m-%dT%H:%M:%S')

            for attempt in range(1, 6):
                try:
                    market_page_response = requests.get(market_url, headers=headers, timeout=10)
                    market_page_response.raise_for_status()

                    # Find all historical closePrice entries with their endTimes
                    pattern = r'\{"startTime":"([^"]+)","endTime":"([^"]+)","openPrice":([\d.]+),"closePrice":([\d.]+),"outcome":"([^"]+)","percentChange":([^}]+)\}'
                    matches = re.findall(pattern, market_page_response.text)

                    # Find the closePrice for the window that ENDS at market start time
                    if target_end_time:
                        for start_time, end_time, open_price, close_price, outcome, pct in matches:
                            if target_end_time in end_time:
                                strike_price = float(close_price)
                                print(f"   💰 Strike Price (Price to Beat): ${strike_price:,.2f}")
                                break

                    # Also extract outcome prices from the page (Up/Down market prices)
                    outcome_prices_match = re.search(r'"outcomePrices"\s*:\s*\[([^\]]+)\]', market_page_response.text)
                    if outcome_prices_match:
                        prices_str = outcome_prices_match.group(1)
                        price_values = re.findall(r'"([0-9.]+)"', prices_str)
                        if len(price_values) >= 2:
                            outcome_prices['up'] = float(price_values[0])
                            outcome_prices['down'] = float(price_values[1])

                    # Extract clobTokenIds from page as fallback
                    clob_ids_match = re.search(r'"clobTokenIds"\s*:\s*\[([^\]]+)\]', market_page_response.text)
                    if clob_ids_match:
                        ids_str = clob_ids_match.group(1)
                        id_values = re.findall(r'"([0-9a-fx]+)"', ids_str, re.IGNORECASE)
                        if len(id_values) >= 2:
                            page_clob_token_ids = {'yes': id_values[0], 'no': id_values[1]}

                    if strike_price is not None:
                        break
                except Exception as e:
                    print(f"   ⚠️  Could not extract data from page (attempt {attempt}/5): {e}")

                time.sleep(2)

            if strike_price is None:
                print("   ❌ Price to Beat not found. Retrying market discovery...")
                return None
            
            # Now fetch details from Gamma API
            api_url = "https://gamma-api.polymarket.com"
            print(f"   Fetching market details from API...")

            # Try direct slug lookup first (more reliable for active markets)
            try:
                slug_response = requests.get(
                    f"{api_url}/events",
                    params={"slug": live_slug},
                    timeout=10
                )
                slug_response.raise_for_status()
                slug_data = slug_response.json()
                if slug_data:
                    event = slug_data[0] if isinstance(slug_data, list) else slug_data
                    now = time.time()
                    timestamp_match = re.search(r'-(\d{10})$', live_slug)
                    if timestamp_match:
                        market_start_timestamp = int(timestamp_match.group(1))
                        market_end_timestamp = market_start_timestamp + 900
                        time_remaining = (market_end_timestamp - now) / 60

                        clob_token_ids = extract_clob_token_ids(event.get('markets', []))
                        clob_token_ids = clob_token_ids or page_clob_token_ids
                        market_data = {
                            'slug': live_slug,
                            'title': event.get('title', '').upper(),
                            'event': event,
                            'markets': event.get('markets', []),
                            'end_date': event.get('end_date_iso', ''),
                            'time_remaining': time_remaining,
                            'end_timestamp': market_end_timestamp,
                            'strike_price': strike_price,
                            'outcome_prices': outcome_prices,
                            'clob_token_ids': clob_token_ids
                        }
                        print(f"   🎯 Selected LIVE market: {live_slug}")
                        print(f"      Time remaining: {time_remaining:.1f} minutes")
                        return market_data
            except Exception:
                pass
            
            # Search for this specific market
            events_response = requests.get(
                f"{api_url}/events",
                params={
                    "order": "id",
                    "ascending": "false",
                    "limit": 200  # Increase limit to find it
                },
                timeout=10
            )
            events_response.raise_for_status()
            events_data = events_response.json()
            
            # Find the matching event
            for event in events_data:
                if live_slug in event.get('slug', ''):
                    now = time.time()
                    timestamp_match = re.search(r'-(\d{10})$', live_slug)
                    if timestamp_match:
                        market_start_timestamp = int(timestamp_match.group(1))
                        # Add 15 minutes (900 seconds) since slug timestamp is START time, not END time
                        market_end_timestamp = market_start_timestamp + 900
                        time_remaining = (market_end_timestamp - now) / 60
                        
                        clob_token_ids = extract_clob_token_ids(event.get('markets', []))
                        clob_token_ids = clob_token_ids or page_clob_token_ids

                        market_data = {
                            'slug': live_slug,
                            'title': event.get('title', '').upper(),
                            'event': event,
                            'markets': event.get('markets', []),
                            'end_date': event.get('end_date_iso', ''),
                            'time_remaining': time_remaining,
                            'end_timestamp': market_end_timestamp,
                            'strike_price': strike_price,  # Include scraped strike price
                            'outcome_prices': outcome_prices,  # Include outcome prices
                            'clob_token_ids': clob_token_ids
                        }
                        
                        print(f"   🎯 Selected LIVE market: {live_slug}")
                        print(f"      Time remaining: {time_remaining:.1f} minutes")
                        return market_data
            
            print(f"   ⚠️  Market found on website but not in API, using website data...")
            # Use what we have from the website
            now = time.time()
            timestamp_match = re.search(r'-(\d{10})$', live_slug)
            if timestamp_match:
                market_start_timestamp = int(timestamp_match.group(1))
                # Add 15 minutes (900 seconds) since slug timestamp is START time, not END time
                market_end_timestamp = market_start_timestamp + 900
                time_remaining = (market_end_timestamp - now) / 60
                
                return {
                    'slug': live_slug,
                    'title': f'BITCOIN UP OR DOWN - 15 MINUTE',
                    'event': {},
                    'markets': [{}],
                    'end_date': '',
                    'time_remaining': time_remaining,
                    'end_timestamp': market_end_timestamp,
                    'strike_price': strike_price,  # Include scraped strike price
                    'outcome_prices': outcome_prices,  # Include outcome prices
                    'clob_token_ids': page_clob_token_ids
                }
        
        print("   ⚠️  Could not find live market on website, trying API...")
        
        # Fallback to API search
        api_url = "https://gamma-api.polymarket.com"
        
        # Get recent events (including those about to close)
        print("   Fetching markets from Gamma API...")
        
        all_btc_markets = []
        now = time.time()
        
        # Check both active (closed=false) and recently closed markets
        for closed_status in ['false', 'true']:
            events_response = requests.get(
                f"{api_url}/events",
                params={
                    "order": "id",
                    "ascending": "false",
                    "closed": closed_status,
                    "limit": 50
                },
                timeout=10
            )
            events_response.raise_for_status()
            events_data = events_response.json()
            
            # Look for BTC 15m updown markets
            for event in events_data:
                slug = event.get('slug', '').lower()
                title = event.get('title', '').upper()
                
                # Check if this is a BTC 15m market
                if 'btc' in slug and '15m' in slug and 'updown' in slug:
                    markets = event.get('markets', [])
                    if markets:
                        # Extract timestamp from slug to calculate time remaining
                        timestamp_match = re.search(r'-(\d{10})$', slug)
                        if timestamp_match:
                            market_start_timestamp = int(timestamp_match.group(1))
                            # Add 15 minutes (900 seconds) since slug timestamp is START time, not END time
                            market_end_timestamp = market_start_timestamp + 900
                            time_remaining = (market_end_timestamp - now) / 60  # minutes
                            
                            # Include markets that haven't expired yet (even if slightly negative due to timing)
                            if time_remaining > -5:  # Allow 5 min grace period for recently closed
                                all_btc_markets.append({
                                    'slug': slug,
                                    'title': title,
                                    'event': event,
                                    'markets': markets,
                                    'end_date': event.get('end_date_iso', ''),
                                    'time_remaining': time_remaining,
                                    'end_timestamp': market_end_timestamp
                                })
        
        print(f"   Found {len(all_btc_markets)} BTC 15m markets")
        
        if all_btc_markets:
            # Sort by time remaining (closest to expiration first)
            all_btc_markets.sort(key=lambda x: x['time_remaining'])
            
            # Show top 5
            for i, m in enumerate(all_btc_markets[:5], 1):
                status = "🟢 ACTIVE" if m['time_remaining'] > 0 else "🔴 EXPIRED"
                print(f"   {i}. {status} {m['slug']} (expires in {m['time_remaining']:.1f}min)")
            
            # Select the one closest to expiration that's still active
            market_data = all_btc_markets[0]
            print(f"\n   🎯 Selected CLOSEST market: {market_data['slug']}")
            print(f"      Time remaining: {market_data['time_remaining']:.1f} minutes")
            return market_data
        else:
            print("   ⚠️  No BTC 15m markets found")
            return None
            
    except Exception as e:
        print(f"   ❌ API Error: {e}")
        return None

# --- 5. EXTRACT STRIKE PRICE FROM QUESTION ---
def extract_strike_from_question(question):
    """Extract strike price from question"""
    import re
    # Try multiple patterns for different formats
    match = re.search(r'\$([0-9,]+\.?\d*)', str(question))
    if match:
        price_str = match.group(1).replace(',', '')
        return float(price_str)
    
    # Try pattern without dollar sign (just numbers)
    match = re.search(r'([0-9,]+\.[0-9]{2})', str(question))
    if match:
        price_str = match.group(1).replace(',', '')
        try:
            return float(price_str)
        except:
            pass
    
    return None

def extract_clob_token_ids(markets):
    """Extract YES/NO token IDs from Gamma market data."""
    if not markets:
        return None

    for market in markets:
        clob_token_ids = market.get('clobTokenIds')
        if not clob_token_ids:
            continue
        try:
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)
            if isinstance(clob_token_ids, list) and len(clob_token_ids) >= 2:
                return {'yes': clob_token_ids[0], 'no': clob_token_ids[1]}
        except Exception:
            continue

    return None

def fetch_clob_best_ask(token_id):
    """Fetch best ask price from Polymarket CLOB book."""
    if not token_id:
        return None
    try:
        response = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        asks = data.get("asks", [])
        if not asks:
            return None
        best_ask = min(float(a.get("price", 1)) for a in asks if a.get("price") is not None)
        return best_ask
    except Exception:
        return None

def fetch_clob_order_book(token_id):
    """Fetch full order book for a token from Polymarket CLOB."""
    if not token_id:
        return None
    try:
        response = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None

def fetch_clob_outcome_prices(yes_token_id, no_token_id):
    """Fetch YES/NO outcome prices from CLOB order books."""
    yes_price = fetch_clob_best_ask(yes_token_id)
    no_price = fetch_clob_best_ask(no_token_id)
    if yes_price is None and no_price is None:
        return None
    return {
        'up': yes_price,
        'down': no_price
    }

# --- 6. WINDOW STATISTICS TRACKING ---
def write_window_statistics(stats, trade_result=None):
    """Write 15-minute window statistics + trade result to readable TXT file."""
    stats_file = "results.txt"
    
    try:
        # Calculate average scores
        total = stats['total_evaluations']
        if total == 0:
            avg_a = avg_b = avg_c = avg_d = avg_total = 0
        else:
            avg_a = stats['total_score_a'] / total
            avg_b = stats['total_score_b'] / total
            avg_c = stats['total_score_c'] / total
            avg_d = stats['total_score_d'] / total
            avg_total = stats['total_score_sum'] / total
        
        with open(stats_file, 'a') as f:
            f.write("\n" + "="*80 + "\n")
            f.write(f"📅 {stats['start_time']} | Market: {stats['market_slug']}\n")
            f.write(f"🎯 Strike Price: ${stats['strike_price']:,.2f}\n")
            f.write("-"*80 + "\n")
            
            f.write(f"📊 SCORING ANALYSIS ({total} evaluations, {stats['signals_triggered']} signals triggered):\n")
            f.write(f"   • Bollinger Bands:    {avg_a:.1f}/40\n")
            f.write(f"   • ATR Kinetic:        {avg_b:.1f}/40\n")
            f.write(f"   • Order Book:         {avg_d:.1f}/10\n")
            f.write(f"   • Price/ROI:          {avg_c:.1f}/10\n")
            f.write(f"   • AVG TOTAL SCORE:    {avg_total:.1f}/100\n")
            f.write(f"   • MAX TOTAL SCORE:    {stats['max_total_score']}/100\n")
            
            if trade_result:
                f.write("-"*80 + "\n")
                f.write(f"💼 TRADE EXECUTED:\n")
                f.write(f"   Direction:     {trade_result['direction'].upper()}\n")
                f.write(f"   Entry Price:   ${trade_result['entry_price']:.3f}\n")
                f.write(f"   Final BTC:     ${trade_result['final_price']:,.2f}\n")
                f.write(f"   Trade Amount:  ${trade_result['trade_amount']:.2f}\n")
                f.write(f"   Result:        {trade_result['result'].upper()}\n")
                f.write(f"   P&L:           {trade_result['profit_loss_pct']:+.2f}% (${trade_result['profit_loss_usd']:+.2f})\n")
                
                print(f"\n📊 WINDOW STATISTICS + TRADE RESULT SAVED:")
                print(f"   Avg Scores - A: {avg_a:.1f} | B: {avg_b:.1f} | C: {avg_c:.1f} | D: {avg_d:.1f}")
                print(f"   Avg Total Score: {avg_total:.1f}/100 | Signals: {stats['signals_triggered']}")
                print(f"   Trade Result: {trade_result['result']} ({trade_result['profit_loss_pct']:+.2f}%)")
            else:
                print(f"\n📊 WINDOW STATISTICS SAVED:")
                print(f"   Avg Scores - A: {avg_a:.1f} | B: {avg_b:.1f} | C: {avg_c:.1f} | D: {avg_d:.1f}")
                print(f"   Avg Total Score: {avg_total:.1f}/100 | Signals: {stats['signals_triggered']} | Evaluations: {total}")
            
            f.write("="*80 + "\n")
        
    except Exception as e:
        print(f"❌ Error writing statistics: {e}")


# --- 7. MAIN TRADING ENGINE ---
def run_advisor():
    # Setup API connections
    creds = ApiCreds(API_KEY, API_SECRET, API_PASSPHRASE)
    poly_client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, creds=creds, chain_id=POLYGON)

    print("\n🚀 DOUBLE BARRIER MEAN REVERSION BOT - CONTINUOUS MODE")
    print("="*60)
    print("📊 Bot will monitor markets continuously and auto-switch to new ones")
    print("="*60)
    
    # Track results across markets
    total_markets = 0
    total_signals = 0
    wins = 0
    losses = 0
    
    while True:
        try:
            total_markets += 1
            print(f"\n\n{'='*60}")
            print(f"🔄 MARKET #{total_markets}")
            print(f"{'='*60}")
            
            # Auto-detect current market
            print("\n🔍 Auto-detecting current BTC 15m market...")
            market_data = find_current_btc_15m_market()
            if not market_data:
                print("\n⚠️  No active markets found. Waiting 15 seconds...")
                time.sleep(15)
                continue
    
            # === Extract market details ===
            title = market_data.get('title', 'N/A')
            slug = market_data.get('slug', 'N/A')
            markets = market_data.get('markets', [])
            end_date_str = market_data.get('end_date', '')
            
            # Use the time_remaining we already calculated
            expiry_minutes = market_data.get('time_remaining', 15)

            # Refresh outcome prices from CLOB if token IDs are available
            clob_token_ids = market_data.get('clob_token_ids')
            if clob_token_ids and clob_token_ids.get('yes') and clob_token_ids.get('no'):
                clob_prices = fetch_clob_outcome_prices(clob_token_ids['yes'], clob_token_ids['no'])
                if clob_prices:
                    market_data['outcome_prices'] = {
                        'up': clob_prices.get('up', market_data.get('outcome_prices', {}).get('up')),
                        'down': clob_prices.get('down', market_data.get('outcome_prices', {}).get('down'))
                    }
            
            print(f"\n✅ MARKET LOADED:")
            print(f"   Title: {title}")
            print(f"   URL: https://polymarket.com/event/{slug}")
            print(f"   ⏰ Time Remaining: {expiry_minutes:.1f} minutes")
            
            # Display outcome prices if available
            outcome_prices = market_data.get('outcome_prices', {})
            if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                print(f"   📊 Market Prices - Up: {outcome_prices['up']*100:.1f}¢ | Down: {outcome_prices['down']*100:.1f}¢")
            
            # Try to use the scraped strike price first
            strike_price = market_data.get('strike_price')
            
            # If not scraped, try to extract from title/question
            if not strike_price:
                strike_price = extract_strike_from_question(title)
            
            # If still not found, try to fetch from market's question field
            if not strike_price and markets:
                first_market = markets[0]
                question = first_market.get('question', '')
                strike_price = extract_strike_from_question(question)
            
            if strike_price:
                print(f"   🎯 Strike Price (Price to Beat): ${strike_price:,.2f}")
            else:
                print(f"   ❌ ALERT: Price to Beat not available. Skipping this market.")
                time.sleep(30)
                continue
            
            # Skip if market already expired
            if expiry_minutes <= -10:
                print(f"\n⚠️  Market expired {abs(expiry_minutes):.1f} minutes ago. Waiting for next market...")
                time.sleep(60)
                continue
            
            # === START MONITORING ===
            end_timestamp = market_data.get('end_timestamp')

            print("\n🚀 MONITORING ACTIVE")
            print(f"📊 Strike Price: ${strike_price:,.2f}")
            
            # Display outcome prices in monitoring status
            outcome_prices = market_data.get('outcome_prices', {})
            if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                print(f"💹 Market Prices - Up: {outcome_prices['up']*100:.1f}¢ | Down: {outcome_prices['down']*100:.1f}¢")
            
            print(f"⏰ Time Remaining: {expiry_minutes:.1f} minutes")
            print(f"🎯 Strategy: Statistical + Kinetic + Physical + R/R Barriers")
            print("\n" + "="*60)
            
            # State tracking
            five_min_announced = False
            three_min_announced = False
            trade_signal_given = False
            signal_details = {}
            
            # Window statistics tracking
            # Use market end time in readable format (correlates to when the 15min window closes)
            end_timestamp = market_data.get('end_timestamp')
            end_datetime = datetime.fromtimestamp(end_timestamp, tz=timezone.utc)
            end_time_readable = end_datetime.strftime('%Y-%m-%d %H:%M')
            
            window_stats = {
                'market_slug': slug,
                'strike_price': strike_price,
                'start_time': end_time_readable,
                'total_score_a': 0,
                'total_score_b': 0,
                'total_score_c': 0,
                'total_score_d': 0,
                'total_score_sum': 0,
                'max_total_score': 0,
                'total_evaluations': 0,
                'signals_triggered': 0
            }
            
            while True:
                now = time.time()
                minutes_left = (end_timestamp - now) / 60
                
                if minutes_left <= 0:
                    print("\n" + "="*60)
                    print("⏰ MARKET EXPIRED!")
                    print("="*60)
                    
                    # Check final result (Chainlink BTC/USD stream price)
                    final_price = fetch_chainlink_btc_usd_price()
                    if final_price is None:
                        print("⚠️  Chainlink price unavailable. Skipping final resolution check.")
                    else:
                        print(f"\n📊 FINAL RESULTS:")
                        print(f"   Strike Price: ${strike_price:,.2f}")
                        print(f"   Final BTC: ${final_price:,.2f}")
                        print(f"   Change: ${final_price - strike_price:,.2f} ({((final_price/strike_price - 1) * 100):+.2f}%)")
                    
                    if trade_signal_given and final_price is not None:
                        total_signals += 1
                        direction = signal_details.get('direction')
                        entry_price = signal_details.get('price')
                        
                        # Determine win/loss
                        is_win = False
                        if direction == "YES" and final_price > strike_price:
                            is_win = True
                            wins += 1
                        elif direction == "NO" and final_price < strike_price:
                            is_win = True
                            wins += 1
                        else:
                            losses += 1
                        
                        # Calculate P&L (assuming $100 trade)
                        trade_amount = 100
                        if is_win:
                            payout = trade_amount / entry_price
                            profit = payout - trade_amount
                            profit_pct = (profit / trade_amount) * 100
                            result_status = "WIN"
                            print(f"\n✅ TRADE WIN!")
                            print(f"   Direction: {direction}")
                            print(f"   Entry: ${entry_price:.2f}")
                            print(f"   Profit: ${profit:.2f} (+{profit_pct:.1f}%)")
                        else:
                            profit = -trade_amount
                            profit_pct = -100
                            result_status = "LOSS"
                            print(f"\n❌ TRADE LOSS!")
                            print(f"   Direction: {direction}")
                            print(f"   Entry: ${entry_price:.2f}")
                            print(f"   Loss: -${trade_amount:.2f}")
                        
                        # Prepare trade result for CSV
                        result_data = {
                            'timestamp': end_time_readable,
                            'market_slug': slug,
                            'strike_price': strike_price,
                            'direction': direction,
                            'entry_price': entry_price,
                            'final_price': final_price,
                            'result': result_status,
                            'profit_loss_pct': profit_pct,
                            'profit_loss_usd': profit,
                            'trade_amount': trade_amount
                        }
                    else:
                        print(f"\n⏸️  NO TRADE SIGNAL - Conditions not met")
                    
                    # === WRITE WINDOW STATISTICS + TRADE RESULT ===
                    if window_stats['total_evaluations'] > 0:
                        if trade_signal_given and final_price is not None and 'result_data' in locals():
                            write_window_statistics(window_stats, result_data)
                        else:
                            write_window_statistics(window_stats)
                    
                    # Print session stats
                    print(f"\n📈 SESSION STATS:")
                    print(f"   Markets: {total_markets} | Signals: {total_signals}")
                    if total_signals > 0:
                        print(f"   W/L: {wins}/{losses} | Win Rate: {(wins/total_signals)*100:.1f}%")
                    
                    print("="*60)
                    print(f"⏭️  Moving to next market in {NEXT_MARKET_WAIT_SECONDS} seconds...\n")
                    time.sleep(NEXT_MARKET_WAIT_SECONDS)
                    break

                try:
                    # 1. Get Real-Time BTC Price from Kraken only
                    real_price = fetch_chainlink_btc_usd_price()
                    
                    if real_price is None:
                        print("   ⚠️  Kraken price unavailable, skipping this evaluation")
                        time.sleep(LOOP_SLEEP_SECONDS)
                        continue
                    
                    # 2. Get Historical Candles from Kraken (OHLC data)
                    try:
                        kraken_url = "https://api.kraken.com/0/public/OHLC?pair=XXBTZUSD&interval=1"
                        headers = {"User-Agent": "Mozilla/5.0"}
                        kraken_response = requests.get(kraken_url, headers=headers, timeout=10)
                        kraken_response.raise_for_status()
                        kraken_data = kraken_response.json()
                        
                        if kraken_data.get('error') and len(kraken_data['error']) > 0:
                            raise Exception(f"Kraken API error: {kraken_data['error']}")
                        
                        ohlc_data = kraken_data['result']['XXBTZUSD']
                        # Take last 60 candles: [time, open, high, low, close, vwap, volume, count]
                        closes = [float(candle[4]) for candle in ohlc_data[-60:]]
                        highs = [float(candle[2]) for candle in ohlc_data[-60:]]
                        lows = [float(candle[3]) for candle in ohlc_data[-60:]]
                        
                    except Exception as e:
                        print(f"   ⚠️  Kraken OHLC data unavailable: {e}")
                        time.sleep(LOOP_SLEEP_SECONDS)
                        continue
                    
                    # 3. Time Window Announcements
                    window_midpoint = (TRADE_WINDOW_MIN + TRADE_WINDOW_MAX) / 2
                    if TRADE_WINDOW_MAX - 0.5 < minutes_left <= TRADE_WINDOW_MAX + 0.5 and not five_min_announced:
                        print(f"\n🔔 ENTERING TRADING WINDOW (Time Left: {minutes_left:.2f}min)")
                        print(f"   Window: {TRADE_WINDOW_MIN}-{TRADE_WINDOW_MAX} minutes before expiration")
                        print("   Starting condition monitoring...")
                        five_min_announced = True
                    
                    if TRADE_WINDOW_MIN - 0.5 < minutes_left <= TRADE_WINDOW_MIN + 0.5 and not three_min_announced:
                        print(f"\n⚠️  APPROACHING MINIMUM WINDOW (Time Left: {minutes_left:.2f}min)")
                        print("   Final opportunity zone!")
                        three_min_announced = True
                    
                    # 4. EXECUTION WINDOW CHECK
                    if TRADE_WINDOW_MIN <= minutes_left <= TRADE_WINDOW_MAX and not trade_signal_given:

                        # Refresh outcome prices from CLOB each evaluation (for live prices)
                        clob_token_ids = market_data.get('clob_token_ids')
                        if clob_token_ids and clob_token_ids.get('yes') and clob_token_ids.get('no'):
                            clob_prices = fetch_clob_outcome_prices(clob_token_ids['yes'], clob_token_ids['no'])
                            if clob_prices:
                                market_data['outcome_prices'] = {
                                    'up': clob_prices.get('up', market_data.get('outcome_prices', {}).get('up')),
                                    'down': clob_prices.get('down', market_data.get('outcome_prices', {}).get('down'))
                                }
                        
                        print(f"\n⏱️  [T-{minutes_left:.2f}min] Calculating Score...")
                        print(f"   Current BTC: ${real_price:,.2f} | Target: ${strike_price:,.2f}")
                        
                        # Show outcome prices at each evaluation
                        outcome_prices = market_data.get('outcome_prices', {})
                        if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                            print(f"   💹 Market Prices - Up: {outcome_prices['up']*100:.1f}¢ | Down: {outcome_prices['down']*100:.1f}¢")
                        
                        trade_score = 0
                        details = []
                        
                        # Max score for each component
                        MAX_SCORE_BB = WEIGHT_BOLLINGER
                        MAX_SCORE_ATR = WEIGHT_ATR
                        MAX_SCORE_ORDERBOOK = WEIGHT_ORDERBOOK
                        MAX_SCORE_PRICE = WEIGHT_PRICE
                        
                        # === A. BOLLINGER BANDS SCORE (Proportional, Max 34) ===
                        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, period=BOLLINGER_PERIOD, std_dev=2.0)
                        
                        score_a = 0
                        if upper_bb and lower_bb:
                            bb_range = upper_bb - lower_bb
                            target_position = (strike_price - lower_bb) / bb_range
                            target_position = max(0, min(1, target_position))  # Clamp to 0-1
                            
                            if real_price > strike_price:  # UP scenario
                                # Target should be LOW (position close to 0)
                                # Score = MAX when position is 0, Score = 0 when position > 0.5
                                if target_position < 0.5:
                                    score_a = int(round(MAX_SCORE_BB * (1 - target_position / 0.5)))
                                else:
                                    score_a = 0
                            else:  # DOWN scenario
                                # Target should be HIGH (position close to 1)
                                # Score = MAX when position is 1, Score = 0 when position < 0.5
                                if target_position > 0.5:
                                    score_a = int(round(MAX_SCORE_BB * ((target_position - 0.5) / 0.5)))
                                else:
                                    score_a = 0
                        
                        trade_score += score_a
                        # Explain BB score
                        if upper_bb and lower_bb:
                            if real_price > strike_price:
                                # UP trade: we want strike LOW in band (near bottom)
                                bb_explain = f"Strike at {target_position:.1%} from BOTTOM of band | UP trade ✓"
                            else:
                                # DOWN trade: we want strike HIGH in band (near top)
                                distance_from_top = 1 - target_position
                                bb_explain = f"Strike at {distance_from_top:.1%} from TOP of band | DOWN trade ✓"
                        else:
                            bb_explain = "Bollinger Bands unavailable"
                        details.append(f"BB: {score_a}/{MAX_SCORE_BB} - {bb_explain}")
                        
                        # === B. ATR KINETIC BARRIER SCORE (Proportional, Max 33) ===
                        atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
                        
                        score_b = 0
                        if atr:
                            max_move = atr * math.sqrt(minutes_left) * ATR_MULTIPLIER
                            dist = abs(real_price - strike_price)
                            
                            # Proportional: max score at 1.5x distance, 0 at 0 distance
                            if max_move > 0:
                                distance_ratio = min(dist / (max_move * 1.5), 1.0)  # Clamp to 0-1
                                score_b = int(round(MAX_SCORE_ATR * distance_ratio))
                        
                        trade_score += score_b
                        # Explain ATR score
                        if atr:
                            atr_explain = f"Distance: ${dist:.2f} | Max move: ${max_move:.2f} | Ratio: {distance_ratio:.1%}"
                        else:
                            atr_explain = "ATR unavailable"
                        details.append(f"ATR: {score_b}/{MAX_SCORE_ATR} - {atr_explain}")
                        
                        # === C. PRICE / VALUE SCORE (Proportional, Max 25) ===
                        score_c = 0
                        share_price = None
                        share_type = "UNKNOWN"
                        
                        try:
                            outcome_prices = market_data.get('outcome_prices', {})
                            if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                                share_price = outcome_prices['up'] if real_price > strike_price else outcome_prices['down']
                                share_type = "YES" if real_price > strike_price else "NO"
                                
                                # Price valuation scoring (proportional)
                                # Max score at 0.30, decreases linearly to 0 at 0.80
                                if share_price < 0.30:
                                    score_c = MAX_SCORE_PRICE  # Max score for very cheap
                                elif share_price <= 0.80:
                                    # Linear interpolation from 0.30 (max) to 0.80 (0)
                                    score_c = int(round(MAX_SCORE_PRICE * (1 - (share_price - 0.30) / (0.80 - 0.30))))
                                else:
                                    score_c = 0  # KILL SWITCH for expensive shares
                        except Exception as api_err:
                            print(f"   ⚠️  Error calculating price score: {api_err}")
                        
                        trade_score += score_c
                        if share_price is None:
                            details.append(f"Price(n/a): 0/{MAX_SCORE_PRICE} - Market prices unavailable")
                        elif share_price > 0.80:
                            details.append(f"Price({share_price:.2f}): 0/{MAX_SCORE_PRICE} - Too expensive (>${0.80:.2f})")
                        else:
                            roi = ((1/share_price)-1)*100 if share_price > 0 else 0
                            price_explain = f"ROI: {roi:.0f}%"
                            details.append(f"Price({share_price:.2f}): {score_c}/{MAX_SCORE_PRICE} - {price_explain}")

                        # === D. ORDER BOOK BARRIER SCORE (Proportional, Max 20) ===
                        score_d = 0
                        order_book_explain = "Data unavailable"
                        order_book = None

                        clob_token_ids = market_data.get('clob_token_ids')
                        if clob_token_ids and clob_token_ids.get('yes') and clob_token_ids.get('no') and share_price is not None:
                            ob_token_id = clob_token_ids['yes'] if real_price > strike_price else clob_token_ids['no']
                            order_book = fetch_clob_order_book(ob_token_id)

                        if order_book and share_price is not None:
                            ob_direction = "UP"
                            ob_scan_depth = max(share_price * 0.05, 0.01)
                            bid_vol, ask_vol, ratio, direction = analyze_order_book_barrier(
                                order_book,
                                share_price,
                                share_price,
                                atr,
                                scan_depth_override=ob_scan_depth,
                                direction_override=ob_direction
                            )

                            if ratio <= OB_RATIO_FLOOR:
                                score_d = 0
                                percent = 0.0
                            elif ratio >= OB_RATIO_CAP:
                                score_d = MAX_SCORE_ORDERBOOK
                                percent = 1.0
                            else:
                                progress = (ratio - OB_RATIO_FLOOR) / (OB_RATIO_CAP - OB_RATIO_FLOOR)
                                score_d = int(round(progress * MAX_SCORE_ORDERBOOK))
                                percent = progress

                            order_book_explain = (
                                f"Ref {share_price:.3f} ±{ob_scan_depth:.3f} | "
                                f"Bid {bid_vol:.1f} vs Ask {ask_vol:.1f} | "
                                f"Ratio {ratio:.2f} -> {percent*100:.0f}% pts"
                            )

                        trade_score += score_d
                        details.append(f"OrderBook: {score_d}/{MAX_SCORE_ORDERBOOK} - {order_book_explain}")
                        
                        # === DECISION ===
                        # Clamp negative scores to 0 for display
                        display_score = max(0, trade_score)
                        
                        window_stats['total_evaluations'] += 1
                        window_stats['total_score_a'] += score_a
                        window_stats['total_score_b'] += score_b
                        window_stats['total_score_c'] += score_c
                        window_stats['total_score_d'] += score_d
                        window_stats['total_score_sum'] += display_score
                        
                        # Track maximum score hit during window
                        if display_score > window_stats['max_total_score']:
                            window_stats['max_total_score'] = display_score
                        
                        print(f"\n   📊 SCORE TOTAL: {display_score}/100  (Seuil: {SCORE_THRESHOLD})")
                        for detail in details:
                            print(f"      {detail}")
                        
                        # === HARD CONSTRAINTS CHECK ===
                        constraint_violations = []
                        if share_price is not None:
                            if share_price < SHARE_PRICE_MIN:
                                constraint_violations.append(f"Price too low (${share_price:.2f} < ${SHARE_PRICE_MIN})")
                            if share_price > SHARE_PRICE_MAX:
                                constraint_violations.append(f"Price too high (${share_price:.2f} > ${SHARE_PRICE_MAX})")
                        if order_book and 'ratio' in locals():
                            if ratio < ORDER_BOOK_RATIO_MIN:
                                constraint_violations.append(f"OrderBook ratio too low ({ratio:.2f} < {ORDER_BOOK_RATIO_MIN})")
                        
                        print("\n" + "-"*60)
                        if constraint_violations:
                            print(f"🚫 TRADE BLOCKED - Hard constraints violated:")
                            for violation in constraint_violations:
                                print(f"   ⛔ {violation}")
                        elif display_score >= SCORE_THRESHOLD:
                            window_stats['signals_triggered'] += 1
                            print(f"🎯 ✅ TRADE CONFIRMÉ (Score {display_score})")
                            print(f"   📈 SIGNAL: BUY {share_type} @ ${share_price:.2f} ({share_price*100:.0f}¢)")
                            print(f"   💰 Risk: ${share_price:.2f} | Potential: ${1-share_price:.2f} | ROI: {((1/share_price)-1)*100:.1f}%")
                            print(f"   🎲 Strategy: Price stays {'ABOVE' if real_price > strike_price else 'BELOW'} ${strike_price:,.2f}")
                            
                            trade_signal_given = True
                            signal_details = {
                                'direction': share_type,
                                'price': share_price,
                                'entry_time': minutes_left,
                                'btc_price': real_price
                            }
                        else:
                            print(f"❌ Score insuffisant ({display_score}/100). Attente...")
                        print("-"*60)
                    
                    elif minutes_left > TRADE_WINDOW_MAX:
                        if not five_min_announced:
                            print(f"⏳ Waiting... {minutes_left:.1f} minutes until expiration", end='\r')
                    
                    elif minutes_left < TRADE_WINDOW_MIN and not trade_signal_given:
                        if not three_min_announced:
                            print(f"\n⚠️  Below {TRADE_WINDOW_MIN}-minute threshold. Window closed without signal.")
                        three_min_announced = True

                except Exception as e:
                    print(f"\n❌ Error in loop: {e}")
                    import traceback
                    traceback.print_exc()

                time.sleep(LOOP_SLEEP_SECONDS)
                
        except Exception as e:
            print(f"\n❌ Error processing market: {e}")
            import traceback
            traceback.print_exc()
            print("\n⏭️  Trying next market in 30 seconds...")
            time.sleep(30)

if __name__ == "__main__":
    try:
        run_advisor()
    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
    run_advisor()
