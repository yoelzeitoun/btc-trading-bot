import time
import math
import sys
import os
import re
import numpy as np
import requests
from binance.client import Client as BinanceClient
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
    LOOP_SLEEP_SECONDS, NEXT_MARKET_WAIT_SECONDS
)

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

def analyze_order_book_barrier(order_book, current_price, target_price):
    """Analyze order book depth between current price and target price"""
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    
    if current_price > target_price:
        # Betting UP: analyze support (bids) below
        relevant_bids = [float(bid[1]) for bid in bids if float(bid[0]) >= target_price and float(bid[0]) < current_price]
        relevant_asks = [float(ask[1]) for ask in asks if float(ask[0]) >= target_price and float(ask[0]) < current_price]
        
        bid_volume = sum(relevant_bids)
        ask_volume = sum(relevant_asks)
        
        if ask_volume > 0:
            ratio = bid_volume / ask_volume
        else:
            ratio = float('inf') if bid_volume > 0 else 0
        
        return bid_volume, ask_volume, ratio, "UP"
    else:
        # Betting DOWN: analyze resistance (asks) above
        relevant_bids = [float(bid[1]) for bid in bids if float(bid[0]) > current_price and float(bid[0]) <= target_price]
        relevant_asks = [float(ask[1]) for ask in asks if float(ask[0]) > current_price and float(ask[0]) <= target_price]
        
        bid_volume = sum(relevant_bids)
        ask_volume = sum(relevant_asks)
        
        if bid_volume > 0:
            ratio = ask_volume / bid_volume
        else:
            ratio = float('inf') if ask_volume > 0 else 0
        
        return bid_volume, ask_volume, ratio, "DOWN"

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
            try:
                import json
                from datetime import datetime
                
                market_url = f"https://polymarket.com/event/{live_slug}"
                market_page_response = requests.get(market_url, headers=headers, timeout=10)
                market_page_response.raise_for_status()
                
                # Extract market start timestamp to find the CORRECT price to beat
                timestamp_match = re.search(r'-(\d{10})$', live_slug)
                if timestamp_match:
                    market_start_timestamp = int(timestamp_match.group(1))
                    # Convert to ISO format - we want the closePrice where endTime = market start time
                    from datetime import datetime, timezone
                    dt = datetime.fromtimestamp(market_start_timestamp, tz=timezone.utc)
                    target_end_time = dt.strftime('%Y-%m-%dT%H:%M:%S')
                    
                    # Find all historical closePrice entries with their endTimes
                    pattern = r'\{"startTime":"([^"]+)","endTime":"([^"]+)","openPrice":([\d.]+),"closePrice":([\d.]+),"outcome":"([^"]+)","percentChange":([^}]+)\}'
                    matches = re.findall(pattern, market_page_response.text)
                    
                    # Find the closePrice for the window that ENDS at market start time
                    for start_time, end_time, open_price, close_price, outcome, pct in matches:
                        if target_end_time in end_time:
                            strike_price = float(close_price)
                            print(f"   💰 Strike Price (Price to Beat): ${strike_price:,.2f}")
                            break
                    
                    # If not found by exact match, take the last one (fallback)
                    if not strike_price and matches:
                        strike_price = float(matches[-1][3])  # closePrice is at index 3
                        print(f"   ⚠️  Using latest historical price: ${strike_price:,.2f}")
                
                # Also extract outcome prices from the page (Up/Down market prices)
                # Look for "outcomePrices" field in the JSON data
                outcome_prices_match = re.search(r'"outcomePrices"\s*:\s*\[([^\]]+)\]', market_page_response.text)
                if outcome_prices_match:
                    prices_str = outcome_prices_match.group(1)
                    # Extract quoted strings
                    price_values = re.findall(r'"([0-9.]+)"', prices_str)
                    if len(price_values) >= 2:
                        outcome_prices['up'] = float(price_values[0])
                        outcome_prices['down'] = float(price_values[1])
                        print(f"   📊 Outcome Prices - Up: {int(float(price_values[0])*100)}¢ | Down: {int(float(price_values[1])*100)}¢")
            except Exception as e:
                print(f"   ⚠️  Could not extract data from page: {e}")
            
            # Now fetch details from Gamma API
            api_url = "https://gamma-api.polymarket.com"
            print(f"   Fetching market details from API...")
            
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
                        
                        market_data = {
                            'slug': live_slug,
                            'title': event.get('title', '').upper(),
                            'event': event,
                            'markets': event.get('markets', []),
                            'end_date': event.get('end_date_iso', ''),
                            'time_remaining': time_remaining,
                            'end_timestamp': market_end_timestamp,
                            'strike_price': strike_price,  # Include scraped strike price
                            'outcome_prices': outcome_prices  # Include outcome prices
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
                    'outcome_prices': outcome_prices  # Include outcome prices
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

# --- 6. MAIN TRADING ENGINE ---
def run_advisor():
    # Setup API connections
    creds = ApiCreds(API_KEY, API_SECRET, API_PASSPHRASE)
    poly_client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, creds=creds, chain_id=POLYGON)
    binance = BinanceClient()

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
                print("\n⚠️  No active markets found. Waiting 30 seconds...")
                time.sleep(30)
                continue
    
            # === Extract market details ===
            title = market_data.get('title', 'N/A')
            slug = market_data.get('slug', 'N/A')
            markets = market_data.get('markets', [])
            end_date_str = market_data.get('end_date', '')
            
            # Use the time_remaining we already calculated
            expiry_minutes = market_data.get('time_remaining', 15)
            
            print(f"\n✅ MARKET LOADED:")
            print(f"   Title: {title}")
            print(f"   URL: https://polymarket.com/event/{slug}")
            print(f"   ⏰ Time Remaining: {expiry_minutes:.1f} minutes")
            
            # Display outcome prices if available
            outcome_prices = market_data.get('outcome_prices', {})
            if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                print(f"   📊 Market Prices - Up: {int(outcome_prices['up']*100)}¢ | Down: {int(outcome_prices['down']*100)}¢")
            
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
                # Use current BTC price as fallback
                try:
                    btc_ticker = binance.get_symbol_ticker(symbol="BTCUSDT")
                    strike_price = float(btc_ticker['price'])
                    print(f"   🎯 Strike Price (current BTC fallback): ${strike_price:,.2f}")
                except Exception as e:
                    strike_price = 78200.0
                    print(f"   ⚠️  Error fetching BTC price: {e}")
                    print(f"   Using default strike price: ${strike_price:,.2f}")
            
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
                print(f"💹 Market Prices - Up: {int(outcome_prices['up']*100)}¢ | Down: {int(outcome_prices['down']*100)}¢")
            
            print(f"⏰ Time Remaining: {expiry_minutes:.1f} minutes")
            print(f"🎯 Strategy: Statistical + Kinetic + Physical + R/R Barriers")
            print("\n" + "="*60)
            
            # State tracking
            five_min_announced = False
            three_min_announced = False
            trade_signal_given = False
            signal_details = {}
            
            while True:
                now = time.time()
                minutes_left = (end_timestamp - now) / 60
                
                if minutes_left <= 0:
                    print("\n" + "="*60)
                    print("⏰ MARKET EXPIRED!")
                    print("="*60)
                    
                    # Check final result
                    btc_data = binance.get_symbol_ticker(symbol="BTCUSDT")
                    final_price = float(btc_data['price'])
                    
                    print(f"\n📊 FINAL RESULTS:")
                    print(f"   Strike Price: ${strike_price:,.2f}")
                    print(f"   Final BTC: ${final_price:,.2f}")
                    print(f"   Change: ${final_price - strike_price:,.2f} ({((final_price/strike_price - 1) * 100):+.2f}%)")
                    
                    if trade_signal_given:
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
                            print(f"\n✅ TRADE WIN!")
                            print(f"   Direction: {direction}")
                            print(f"   Entry: ${entry_price:.2f}")
                            print(f"   Profit: ${profit:.2f} (+{(profit/trade_amount)*100:.1f}%)")
                        else:
                            print(f"\n❌ TRADE LOSS!")
                            print(f"   Direction: {direction}")
                            print(f"   Entry: ${entry_price:.2f}")
                            print(f"   Loss: -${trade_amount:.2f}")
                    else:
                        print(f"\n⏸️  NO TRADE SIGNAL - Conditions not met")
                    
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
                    # 1. Get Real-Time BTC Price
                    btc_data = binance.get_symbol_ticker(symbol="BTCUSDT")
                    real_price = float(btc_data['price'])
                    
                    # 2. Get Historical Candles
                    klines = binance.get_klines(symbol="BTCUSDT", interval='1m', limit=60)
                    closes = [float(k[4]) for k in klines]
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    
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
                        
                        # Print header only once per monitoring loop
                        import sys
                        
                        # Fetch market data once
                        btc_data = binance.get_symbol_ticker(symbol="BTCUSDT")
                        real_price = float(btc_data['price'])
                        
                        klines = binance.get_klines(symbol="BTCUSDT", interval='1m', limit=60)
                        closes = [float(k[4]) for k in klines]
                        highs = [float(k[2]) for k in klines]
                        lows = [float(k[3]) for k in klines]
                        
                        # Build the evaluation output
                        eval_output = f"\r⏱️  [T-{minutes_left:.2f}min] Evaluating Trade Conditions..."
                        eval_output += f"\n   Current BTC: ${real_price:,.2f} | Target: ${strike_price:,.2f}"
                        
                        # Show outcome prices
                        outcome_prices = market_data.get('outcome_prices', {})
                        if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                            eval_output += f"\n   💹 Market Prices - Up: {int(outcome_prices['up']*100)}¢ | Down: {int(outcome_prices['down']*100)}¢"
                        
                        # === CONDITION A: BOLLINGER BANDS ===
                        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, period=BOLLINGER_PERIOD, std_dev=BOLLINGER_STD_DEV)
                        
                        condition_a_pass = False
                        if upper_bb and lower_bb:
                            if real_price > strike_price:
                                condition_a_pass = strike_price < lower_bb
                                eval_output += f"\n\n   [A] BOLLINGER BANDS (Period={BOLLINGER_PERIOD}, StdDev={BOLLINGER_STD_DEV})"
                                eval_output += f"\n       Upper: ${upper_bb:,.2f} | Middle: ${middle_bb:,.2f} | Lower: ${lower_bb:,.2f}"
                                eval_output += f"\n       Direction: UP | Target vs Lower Band: ${strike_price:,.2f} < ${lower_bb:,.2f}"
                                eval_output += f"\n       Result: {'✅ PASS' if condition_a_pass else '❌ FAIL'}"
                            else:
                                condition_a_pass = strike_price > upper_bb
                                eval_output += f"\n\n   [A] BOLLINGER BANDS (Period={BOLLINGER_PERIOD}, StdDev={BOLLINGER_STD_DEV})"
                                eval_output += f"\n       Upper: ${upper_bb:,.2f} | Middle: ${middle_bb:,.2f} | Lower: ${lower_bb:,.2f}"
                                eval_output += f"\n       Direction: DOWN | Target vs Upper Band: ${strike_price:,.2f} > ${upper_bb:,.2f}"
                                eval_output += f"\n       Result: {'✅ PASS' if condition_a_pass else '❌ FAIL'}"
                        else:
                            eval_output += f"\n\n   [A] BOLLINGER BANDS: ⚠️  Insufficient data"
                        
                        # === CONDITION B: ATR KINETIC BARRIER ===
                        atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
                        
                        condition_b_pass = False
                        if atr:
                            max_possible_move = atr * minutes_left * ATR_MULTIPLIER
                            actual_distance = abs(real_price - strike_price)
                            condition_b_pass = actual_distance > max_possible_move
                            
                            eval_output += f"\n\n   [B] ATR KINETIC BARRIER (Period={ATR_PERIOD})"
                            eval_output += f"\n       ATR: ${atr:,.2f}"
                            eval_output += f"\n       Max Possible Move: ${max_possible_move:,.2f} (ATR × {minutes_left:.1f}min × {ATR_MULTIPLIER})"
                            eval_output += f"\n       Actual Distance: ${actual_distance:,.2f}"
                            eval_output += f"\n       Result: {'✅ PASS' if condition_b_pass else '❌ FAIL'}"
                        else:
                            eval_output += f"\n\n   [B] ATR KINETIC BARRIER: ⚠️  Insufficient data"
                        
                        # === CONDITION C: ORDER BOOK DEPTH ===
                        order_book = binance.get_order_book(symbol="BTCUSDT", limit=1000)
                        bid_vol, ask_vol, ratio, direction = analyze_order_book_barrier(order_book, real_price, strike_price)
                        
                        condition_c_pass = ratio >= ORDER_BOOK_RATIO_MIN
                        
                        eval_output += f"\n\n   [C] ORDER BOOK DEPTH BARRIER"
                        eval_output += f"\n       Direction: {direction}"
                        if direction == "UP":
                            eval_output += f"\n       BID Volume (Support): {bid_vol:,.2f} BTC"
                            eval_output += f"\n       ASK Volume (Threat): {ask_vol:,.2f} BTC"
                        else:
                            eval_output += f"\n       ASK Volume (Resistance): {ask_vol:,.2f} BTC"
                            eval_output += f"\n       BID Volume (Threat): {bid_vol:,.2f} BTC"
                        eval_output += f"\n       Ratio: {ratio:.2f}x (Need >= {ORDER_BOOK_RATIO_MIN}x)"
                        eval_output += f"\n       Result: {'✅ PASS' if condition_c_pass else '❌ FAIL'}"
                        
                        # === CONDITION D: PRICE / R/R FILTER ===
                        try:
                            market_info = poly_client.get_market(token_id)
                            
                            if real_price > strike_price:
                                share_price = float(market_info.get('best_ask', 0.5))
                                share_type = "YES"
                            else:
                                share_price = 1.0 - float(market_info.get('best_bid', 0.5))
                                share_type = "NO"
                            
                            condition_d_pass = SHARE_PRICE_MIN <= share_price <= SHARE_PRICE_MAX
                            
                            eval_output += f"\n\n   [D] RISK/REWARD FILTER"
                            eval_output += f"\n       Share Type: {share_type}"
                            eval_output += f"\n       Share Price: ${share_price:.2f} (${share_price*100:.0f}¢)"
                            eval_output += f"\n       Valid Range: ${SHARE_PRICE_MIN:.2f} - ${SHARE_PRICE_MAX:.2f}"
                            eval_output += f"\n       Result: {'✅ PASS' if condition_d_pass else '❌ FAIL'}"
                            
                        except Exception as api_err:
                            eval_output += f"\n\n   [D] RISK/REWARD FILTER: ⚠️  API Error"
                            condition_d_pass = False
                            share_price = 0.5
                            share_type = "UNKNOWN"
                        
                        # === FINAL DECISION ===
                        all_conditions_met = (condition_a_pass and condition_b_pass and 
                                             condition_c_pass and condition_d_pass)
                        
                        eval_output += "\n" + "-"*60
                        if all_conditions_met:
                            trade_direction = "YES" if real_price > strike_price else "NO"
                            eval_output += f"\n🎯 ✅✅ TRADE CONDITIONS MET! ✅✅"
                            eval_output += f"\n   📈 SIGNAL: BUY {trade_direction} @ ${share_price:.2f} ({share_price*100:.0f}¢)"
                            eval_output += f"\n   💰 Risk: ${share_price:.2f} | Potential: ${1-share_price:.2f} | ROI: {((1/share_price)-1)*100:.1f}%"
                            eval_output += f"\n   🎲 Strategy: Price stays {'ABOVE' if real_price > strike_price else 'BELOW'} ${strike_price:,.2f}"
                            
                            trade_signal_given = True
                            signal_details = {
                                'direction': trade_direction,
                                'price': share_price,
                                'entry_time': minutes_left,
                                'btc_price': real_price
                            }
                        else:
                            conditions_summary = f"A:{condition_a_pass} B:{condition_b_pass} C:{condition_c_pass} D:{condition_d_pass}"
                            eval_output += f"\n❌ CONDITIONS NOT MET [{conditions_summary}]"
                            eval_output += f"\n   No trade signal. Continuing monitoring..."
                        
                        eval_output += "\n" + "-"*60
                        
                        # Store previous output line count for clearing
                        if not hasattr(run_advisor, 'prev_eval_lines'):
                            run_advisor.prev_eval_lines = 0
                        
                        # Calculate number of lines in current output
                        current_lines = eval_output.count('\n') + 1
                        
                        # Clear previous evaluation output by moving cursor up and clearing lines
                        if run_advisor.prev_eval_lines > 0:
                            # Move cursor up by previous line count and clear those lines
                            for _ in range(run_advisor.prev_eval_lines):
                                print('\033[A\033[2K', end='')  # Move up one line and clear it
                            print('\r', end='')  # Return to start of line
                        
                        # Print the new evaluation output
                        print(eval_output)
                        
                        # Store current line count for next iteration
                        run_advisor.prev_eval_lines = current_lines
                    
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
