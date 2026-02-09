#!/usr/bin/env python3
"""
Test script: Open position with best score (UP or DOWN), wait 10s, then close it
"""
import os
import sys
import time
import requests
import numpy as np
import math
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import configuration
from config import (
    BOLLINGER_PERIOD, BOLLINGER_STD_DEV,
    ATR_PERIOD, ATR_MULTIPLIER,
    WEIGHT_BOLLINGER, WEIGHT_ATR,
    TRADE_AMOUNT
)

# Import trading functions
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from py_clob_client.constants import POLYGON

# Get credentials
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PROXY_ADDRESS = os.getenv("PROXY_ADDRESS")

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

def fetch_kraken_btc_price():
    """Fetch current BTC price from Kraken"""
    try:
        response = requests.get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('error') and len(data['error']) > 0:
            return None
        result = data.get('result', {})
        xbt_data = result.get('XXBTZUSD', {})
        price = float(xbt_data.get('c', [0])[0])
        return price
    except Exception:
        return None

def fetch_kraken_ohlc():
    """Fetch OHLC data from Kraken"""
    try:
        kraken_url = "https://api.kraken.com/0/public/OHLC?pair=XXBTZUSD&interval=1"
        headers = {"User-Agent": "Mozilla/5.0"}
        kraken_response = requests.get(kraken_url, headers=headers, timeout=10)
        kraken_response.raise_for_status()
        kraken_data = kraken_response.json()
        
        if kraken_data.get('error') and len(kraken_data['error']) > 0:
            return None, None, None
        
        ohlc_data = kraken_data['result']['XXBTZUSD']
        closes = [float(candle[4]) for candle in ohlc_data[-60:]]
        highs = [float(candle[2]) for candle in ohlc_data[-60:]]
        lows = [float(candle[3]) for candle in ohlc_data[-60:]]
        
        return closes, highs, lows
    except Exception:
        return None, None, None

def extract_clob_token_ids(markets):
    """Extract YES/NO token IDs from Gamma market data."""
    if not markets:
        return None

    for market in markets:
        clob_token_ids = market.get('clobTokenIds')
        if not clob_token_ids:
            continue
        try:
            import json
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)
            if isinstance(clob_token_ids, list) and len(clob_token_ids) >= 2:
                return {'yes': clob_token_ids[0], 'no': clob_token_ids[1]}
        except Exception:
            continue

    return None

def find_current_btc_15m_market():
    """Find current LIVE BTC 15m market (same as main bot)"""
    print("🔍 Searching for current LIVE BTC 15m market on Polymarket...")
    
    try:
        import re
        import json
        from datetime import datetime, timezone
        
        # Try to scrape the live market from the crypto/15M page
        print("   Fetching live market from Polymarket website...")
        crypto_page_url = "https://polymarket.com/crypto/15M"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        page_response = requests.get(crypto_page_url, headers=headers, timeout=10)
        page_response.raise_for_status()
        
        market_links = re.findall(r'/event/(btc-updown-15m-\d{10})', page_response.text)
        
        if market_links:
            live_slug = market_links[0]
            print(f"   ✅ Found LIVE market on website: {live_slug}")
            
            strike_price = None
            outcome_prices = {'up': None, 'down': None}
            page_clob_token_ids = None

            market_url = f"https://polymarket.com/event/{live_slug}"
            timestamp_match = re.search(r'-(\d{10})$', live_slug)
            target_end_time = None
            if timestamp_match:
                market_start_timestamp = int(timestamp_match.group(1))
                dt = datetime.fromtimestamp(market_start_timestamp, tz=timezone.utc)
                target_end_time = dt.strftime('%Y-%m-%dT%H:%M:%S')

            for attempt in range(1, 4):
                try:
                    market_page_response = requests.get(market_url, headers=headers, timeout=10)
                    market_page_response.raise_for_status()

                    pattern = r'\{"startTime":"([^"]+)","endTime":"([^"]+)","openPrice":([\d.]+),"closePrice":([\d.]+),"outcome":"([^"]+)","percentChange":([^}]+)\}'
                    matches = re.findall(pattern, market_page_response.text)

                    if target_end_time:
                        for start_time, end_time, open_price, close_price, outcome, pct in matches:
                            if target_end_time in end_time:
                                strike_price = float(close_price)
                                print(f"   💰 Strike Price: ${strike_price:,.2f}")
                                break

                    outcome_prices_match = re.search(r'"outcomePrices"\s*:\s*\[([^\]]+)\]', market_page_response.text)
                    if outcome_prices_match:
                        prices_str = outcome_prices_match.group(1)
                        price_values = re.findall(r'"([0-9.]+)"', prices_str)
                        if len(price_values) >= 2:
                            outcome_prices['up'] = float(price_values[0])
                            outcome_prices['down'] = float(price_values[1])

                    clob_ids_match = re.search(r'"clobTokenIds"\s*:\s*\[([^\]]+)\]', market_page_response.text)
                    if clob_ids_match:
                        ids_str = clob_ids_match.group(1)
                        id_values = re.findall(r'"([0-9a-fx]+)"', ids_str, re.IGNORECASE)
                        if len(id_values) >= 2:
                            page_clob_token_ids = {'yes': id_values[0], 'no': id_values[1]}

                    if strike_price is not None:
                        break
                except Exception:
                    pass
                time.sleep(1)

            # Calculate time remaining
            now = time.time()
            timestamp_match = re.search(r'-(\d{10})$', live_slug)
            if timestamp_match:
                market_start_timestamp = int(timestamp_match.group(1))
                market_end_timestamp = market_start_timestamp + 900
                time_remaining = (market_end_timestamp - now) / 60
                
                return {
                    'slug': live_slug,
                    'title': 'BITCOIN UP OR DOWN - 15 MINUTE',
                    'time_remaining': time_remaining,
                    'end_timestamp': market_end_timestamp,
                    'strike_price': strike_price,
                    'outcome_prices': outcome_prices,
                    'clob_token_ids': page_clob_token_ids
                }
        
        print("   ⚠️  Could not find live market")
        return None
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

def calculate_score(real_price, strike_price, closes, highs, lows, minutes_left):
    """Calculate score for a given direction"""
    # Bollinger Bands Score
    upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, period=BOLLINGER_PERIOD, std_dev=2.0)
    
    score_a = 0
    if upper_bb and lower_bb and middle_bb:
        bb_range = upper_bb - lower_bb
        target_position = (strike_price - lower_bb) / bb_range
        target_position = max(0, min(1, target_position))
        
        if real_price > strike_price:  # UP scenario
            if target_position < 0.5:
                score_a = int(round(WEIGHT_BOLLINGER * (1 - target_position / 0.5)))
                score_a = min(score_a, WEIGHT_BOLLINGER)
        else:  # DOWN scenario
            if target_position > 0.5:
                score_a = int(round(WEIGHT_BOLLINGER * ((target_position - 0.5) / 0.5)))
                score_a = min(score_a, WEIGHT_BOLLINGER)
    
    # ATR Score
    atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
    
    score_b = 0
    if atr:
        max_move = atr * math.sqrt(minutes_left) * ATR_MULTIPLIER
        dist = abs(real_price - strike_price)
        
        if dist < max_move:
            score_b = 0
        else:
            if max_move > 0:
                distance_ratio = min((dist - max_move) / max_move, 1.0)
                score_b = int(round(WEIGHT_ATR * distance_ratio))
    
    return score_a + score_b

def get_max_sellable_size(poly_client, token_id):
    """
    Get the exact balance of conditional shares and truncate to 4 decimals safely.
    Uses math.floor() to avoid rounding errors that cause rejection.
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        
        balance_params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=token_id
        )
        balance_info = poly_client.get_balance_allowance(balance_params)
        
        # Extract raw balance value
        if isinstance(balance_info, dict):
            raw_balance = balance_info.get("balance", 0)
        else:
            raw_balance = getattr(balance_info, "balance", 0)
        
        # Convert from micro-units to decimals (only if raw_balance > 1000)
        if raw_balance > 1000:
            real_size = float(raw_balance) / 1_000_000
        else:
            real_size = float(raw_balance)
        
        # Truncate to 4 decimals sans arrondir au supérieur
        safe_size = math.floor(real_size * 10000) / 10000
        
        return safe_size if safe_size > 0 else 0.0
    except Exception as e:
        print(f"   ❌ Erreur lecture solde MAX: {e}")
        return 0.0

def execute_close_trade(poly_client, token_id, size, current_btc_price=None):
    """
    Close an open position en utilisant le solde exact, tronqué à 4 décimales.
    Une seule tentative avec le montant optimal, sans boucle de fallback.
    """
    import requests
    from datetime import datetime
    from py_clob_client.clob_types import OrderArgs
    
    # 1. On demande le MAX exact et nettoyé
    max_size = get_max_sellable_size(poly_client, token_id)
    
    if max_size > 0:
        print(f"   💰 Solde exact détecté : {max_size:.4f} parts")
        trade_size = max_size
    else:
        # Fallback si l'API de solde échoue : on utilise la taille en mémoire
        print(f"   ⚠️  Impossible de lire le solde, utilisation de la taille mémoire : {size}")
        trade_size = float(size)
    
    # 2. On lance l'ordre UNE SEULE FOIS (plus besoin de boucle)
    try:
        # Get best bid
        book_url = f"https://clob.polymarket.com/book?token_id={token_id}"
        book_response = requests.get(book_url, timeout=10)
        book_response.raise_for_status()
        book_data = book_response.json()

        bids = book_data.get("bids", [])
        if not bids:
            print("   ❌ No bids available to close position")
            return None

        # Get best bid
        best_bid_price = max(float(b['price']) for b in bids)

        print(f"   📉 Vente de {trade_size:.4f} parts @ ${best_bid_price:.3f}...")

        order_args = OrderArgs(
            price=best_bid_price,
            size=trade_size,
            side="SELL",
            token_id=token_id
        )

        response = poly_client.create_and_post_order(order_args)
        
        if isinstance(response, dict) and response.get("success"):
            order_id = response.get("orderID", "unknown")
            print(f"   ✅ CLOSE ORDER PLACED: {order_id} @ ${best_bid_price:.3f}")
            close_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return {
                'success': True,
                'order_id': order_id,
                'price': best_bid_price,
                'size': trade_size,
                'token_id': token_id,
                'close_time': close_time,
                'close_btc_price': current_btc_price
            }
        else:
            error_msg = response.get("error", response) if isinstance(response, dict) else str(response)
            print(f"   ❌ CLOSE ORDER FAILED: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'price': best_bid_price,
                'size': trade_size,
                'token_id': token_id,
                'close_btc_price': current_btc_price
            }
    except Exception as e:
        error_str = str(e)
        print(f"   ❌ Erreur fermeture : {error_str}")
        return {
            'success': False,
            'error': error_str,
            'size': trade_size,
            'token_id': token_id,
            'close_btc_price': current_btc_price
        }


def main():
    print("\n" + "="*70)
    print("🧪 TEST: Open Best Position (UP or DOWN) → Wait 10s → Close")
    print("="*70)
    
    # Setup client
    creds = ApiCreds(API_KEY, API_SECRET, API_PASSPHRASE)
    if PROXY_ADDRESS:
        poly_client = ClobClient(
            "https://clob.polymarket.com",
            key=PRIVATE_KEY,
            creds=creds,
            chain_id=POLYGON,
            funder=PROXY_ADDRESS,
            signature_type=2
        )
    else:
        poly_client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, creds=creds, chain_id=POLYGON)
    
    # Get current market data
    print("\n📊 Fetching current market data...")
    
    # Get current BTC price
    real_price = fetch_kraken_btc_price()
    if not real_price:
        print("❌ Could not fetch BTC price")
        return
    
    print(f"   Current BTC: ${real_price:,.2f}")
    
    # Get OHLC data
    closes, highs, lows = fetch_kraken_ohlc()
    if not closes:
        print("❌ Could not fetch OHLC data")
        return
    
    print(f"   OHLC data: {len(closes)} candles")
    
    # Use current price as strike for testing
    strike_price = real_price
    print(f"   Strike Price (for test): ${strike_price:,.2f}")
    
    # Calculate time remaining (simulate 10 minutes left)
    minutes_left = 10.0
    
    # Calculate scores for both directions
    print("\n📈 Calculating scores...")
    
    # UP direction: price needs to be slightly above strike
    up_test_price = strike_price + 100  # $100 above
    up_score = calculate_score(up_test_price, strike_price, closes, highs, lows, minutes_left)
    print(f"   UP Score:   {up_score}/100 (BTC ${up_test_price:,.2f} > ${strike_price:,.2f})")
    
    # DOWN direction: price needs to be slightly below strike
    down_test_price = strike_price - 100  # $100 below
    down_score = calculate_score(down_test_price, strike_price, closes, highs, lows, minutes_left)
    print(f"   DOWN Score: {down_score}/100 (BTC ${down_test_price:,.2f} < ${strike_price:,.2f})")
    
    # Get current live market FIRST to get actual prices
    print("\n🔍 Finding current live market...")
    current_market = find_current_btc_15m_market()
    
    if not current_market:
        print("❌ Could not find active BTC 15m market")
        return
    
    clob_token_ids = current_market.get('clob_token_ids')
    if not clob_token_ids or not clob_token_ids.get('yes') or not clob_token_ids.get('no'):
        print("❌ Could not get token IDs")
        return
    
    yes_token = clob_token_ids['yes']
    no_token = clob_token_ids['no']
    
    print(f"   Market: {current_market.get('slug')}")
    
    # Get prices for BOTH directions
    book_response_up = requests.get(
        "https://clob.polymarket.com/book",
        params={"token_id": yes_token},
        timeout=10
    )
    book_response_up.raise_for_status()
    book_data_up = book_response_up.json()
    asks_up = book_data_up.get("asks", [])
    up_price = min(float(a.get("price", 0)) for a in asks_up if a.get("price") is not None) if asks_up else 0
    
    book_response_down = requests.get(
        "https://clob.polymarket.com/book",
        params={"token_id": no_token},
        timeout=10
    )
    book_response_down.raise_for_status()
    book_data_down = book_response_down.json()
    asks_down = book_data_down.get("asks", [])
    down_price = min(float(a.get("price", 0)) for a in asks_down if a.get("price") is not None) if asks_down else 0
    
    print(f"\n💹 Market Prices:")
    print(f"   UP (YES):   ${up_price:.3f} ({up_price*100:.1f}¢) | Score: {up_score}/100")
    print(f"   DOWN (NO):  ${down_price:.3f} ({down_price*100:.1f}¢) | Score: {down_score}/100")
    
    # Choose direction with HIGHEST PRICE (most expensive = market thinks it's most likely)
    if up_price >= down_price:
        direction = "UP"
        test_price = up_test_price
        best_score = up_score
        token_type = "YES"
        token_id = yes_token
        best_ask_price = up_price
    else:
        direction = "DOWN"
        test_price = down_test_price
        best_score = down_score
        token_type = "NO"
        token_id = no_token
        best_ask_price = down_price
    
    print(f"\n🎯 Best Direction: {direction} (HIGHEST PRICE: ${best_ask_price:.3f})")
    print(f"   Token ID ({token_type}): {token_id[:20]}...")
    
    print(f"   Best Ask Price: ${best_ask_price:.3f}")
    
    # Calculate shares needed to meet $1 minimum
    min_order_value = 1.05  # Add 5% buffer to ensure we meet $1 minimum
    required_shares = min_order_value / best_ask_price
    trade_shares = max(float(TRADE_AMOUNT), required_shares)
    actual_cost = trade_shares * best_ask_price
    
    # Open position
    print(f"\n💼 Opening {direction} position...")
    print(f"   Buying {trade_shares:.2f} shares @ ${best_ask_price:.3f}")
    print(f"   Total Cost: ${actual_cost:.2f} (min $1.00 required)")
    
    from py_clob_client.clob_types import OrderArgs
    order_args = OrderArgs(
        price=best_ask_price,
        size=trade_shares,
        side="BUY",
        token_id=token_id
    )
    
    order_response = poly_client.create_and_post_order(order_args)
    
    if not isinstance(order_response, dict) or not order_response.get("success"):
        print(f"❌ Failed to open position: {order_response}")
        return
    
    order_id = order_response.get("orderID", "unknown")
    print(f"   ✅ Position opened! Order ID: {order_id}")
    print(f"   Cost: ${actual_cost:.2f}")
    
    # Wait for order to be filled
    print("\n⏳ Waiting for order to fill...")
    filled = False
    for attempt in range(20):  # Try for up to 10 seconds
        try:
            order_status_response = requests.get(
                f"https://clob.polymarket.com/order/{order_id}",
                timeout=5
            )
            if order_status_response.status_code == 200:
                order_data = order_status_response.json()
                status = order_data.get('status', '')
                print(f"   Order status: {status}", end='\r')
                if status in ['MATCHED', 'FILLED']:
                    filled = True
                    print(f"\n   ✅ Order filled!")
                    break
        except Exception:
            pass
        time.sleep(0.5)
    
    if not filled:
        print("\n   ⚠️  Order may not be fully filled, but continuing with close attempt...")

    # Get current BTC price for close
    current_btc = fetch_kraken_btc_price()
    if not current_btc:
        current_btc = real_price
    
    # Close position
    print(f"\n🔒 Closing {direction} position...")
    close_result = execute_close_trade(
        poly_client,
        token_id,
        trade_shares,
        current_btc
    )
    
    if close_result and close_result.get('success'):
        print("\n✅ TEST COMPLETE - Position closed successfully!")
        print(f"   Close Price: ${close_result.get('price', 0):.3f}")
        print(f"   Close Time: {close_result.get('close_time', 'N/A')}")
    else:
        print(f"\n⚠️  TEST COMPLETE - Close failed: {close_result.get('error', 'Unknown error')}")


if __name__ == "__main__":
    main()
