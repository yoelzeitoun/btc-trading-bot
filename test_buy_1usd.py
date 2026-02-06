import os
import time
import requests
import json
import re
from dotenv import load_dotenv
from pathlib import Path
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.constants import POLYGON

# Load environment variables from this script's directory
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE")
MY_ADDRESS = os.getenv("MY_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PROXY_ADDRESS = os.getenv("PROXY_ADDRESS")

def find_market():
    """Finds the current live BTC 15m market slug."""
    url = "https://polymarket.com/crypto/15M"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        links = re.findall(r'/event/(btc-updown-15m-\d{10})', resp.text)
        if links:
            # Sort links and pick the one that hasn't expired yet
            # Current UTC time
            now_ts = int(time.time())
            valid_links = []
            for slug in set(links):
                m_ts = int(slug.split('-')[-1])
                # A market is active if current time < start_ts + 15mins
                if now_ts < (m_ts + 900):
                    valid_links.append((m_ts, slug))
            
            if valid_links:
                # Pick the one closest to now (smallest timestamp)
                valid_links.sort()
                return valid_links[0][1]
            return links[0]
    except Exception as e:
        print(f"Error finding market: {e}")
    return None

def get_token_ids(slug):
    """Fetches market details from Gamma API to get token IDs."""
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data and len(data) > 0:
            markets = data[0].get('markets', [])
            for m in markets:
                clob_token_ids = m.get('clobTokenIds')
                if clob_token_ids:
                    if isinstance(clob_token_ids, str):
                        clob_token_ids = json.loads(clob_token_ids)
                    if isinstance(clob_token_ids, list) and len(clob_token_ids) >= 2:
                        return {'yes': clob_token_ids[0], 'no': clob_token_ids[1]}
    except Exception as e:
        print(f"Error getting token IDs: {e}")
    return None

def get_best_ask(token_id):
    """Fetches best ask price and minimum order size for a token."""
    url = f"https://clob.polymarket.com/book?token_id={token_id}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        asks = data.get("asks", [])
        min_order_size = data.get("min_order_size", 1)  # Default to 1 if not provided
        if asks:
            best_price = min(float(a['price']) for a in asks)
            return best_price, float(min_order_size)
    except Exception as e:
        print(f"Error getting best ask: {e}")
    return None, None

def run_test_trade():
    print("🚀 Starting $1 Test Trade Script...")

    # Ensure API credentials are loaded
    missing = [
        name for name, val in (
            ("API_KEY", API_KEY),
            ("API_SECRET", API_SECRET),
            ("API_PASSPHRASE", API_PASSPHRASE),
        ) if not val
    ]
    if missing:
        print(f"❌ Missing credentials in .env: {', '.join(missing)}")
        return

    # Masked debug (safe to display)
    def _mask(value):
        return f"{value[:4]}...{value[-4:]}" if value and len(value) > 8 else "(too short)"

    print(f"🔐 API_KEY: {_mask(API_KEY)}")
    print(f"🔐 API_SECRET: {_mask(API_SECRET)}")
    print(f"🔐 API_PASSPHRASE: {_mask(API_PASSPHRASE)}")
    print(f"🔗 Owner (PRIVATE_KEY derives to): {MY_ADDRESS}")
    print(f"🔗 Proxy (Trader on Polymarket): {PROXY_ADDRESS}")

    if not PRIVATE_KEY:
        print("❌ Missing PRIVATE_KEY in .env (required for signed orders)")
        return

    if not PROXY_ADDRESS:
        print("❌ Missing PROXY_ADDRESS in .env (the Polymarket 'Trader' address)")
        return

    # Verify PRIVATE_KEY matches MY_ADDRESS (MetaMask owner)
    if MY_ADDRESS:
        try:
            from eth_account import Account
            derived_addr = Account.from_key(PRIVATE_KEY).address
            if derived_addr.lower() != MY_ADDRESS.lower():
                print(f"⚠️  PRIVATE_KEY derives to {derived_addr}, but MY_ADDRESS is {MY_ADDRESS}")
                print("   This is expected in proxy wallet setup. Using PROXY_ADDRESS for trading.")
        except Exception as e:
            print(f"⚠️  Could not verify wallet address: {e}")
    
    # Initialize Client
    creds = ApiCreds(api_key=API_KEY, api_secret=API_SECRET, api_passphrase=API_PASSPHRASE)
    
    # Proxy wallet setup: sign with PRIVATE_KEY (Manager/0xEad), use API keys for PROXY_ADDRESS (0x291)
    # signature_type=1 tells Polymarket: "0xEad is an authorized manager of 0x291"
    client = ClobClient(
        "https://clob.polymarket.com",
        key=PRIVATE_KEY,
        creds=creds,
        chain_id=POLYGON,
        funder=PROXY_ADDRESS,  # The Proxy address (0x291...)
        signature_type=2       # 1 = PolyProxy (Manager signing for Proxy)
    )

    # Quick credentials sanity check
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        balance_info = client.get_balance_allowance(params)
        print("💰 Balance:", balance_info)
    except Exception as e:
        print(f"❌ Failed to fetch balance: {e}")
        return

    
    # 1. Find Market
    slug = find_market()
    if not slug:
        print("❌ Could not find an active BTC 15m market.")
        return
    print(f"✅ Found Market: {slug}")
    
    # 2. Get Token IDs
    tokens = get_token_ids(slug)
    if not tokens:
        print("❌ Could not fetch token IDs.")
        return
    yes_token = tokens['yes']
    print(f"✅ YES Token ID: {yes_token}")
    
    # 3. Get Price and Minimum Order Size
    price, min_size = get_best_ask(yes_token)
    if not price:
        print("❌ Could not fetch best ask price.")
        return
    print(f"✅ Best Ask Price: ${price:.3f}")
    print(f"📋 Minimum Order Size: {min_size}")
    
    # 4. Calculate Size for $1, respecting minimum order size
    size = round(1.0 / price, 2)
    if size < min_size:
        # If $1 doesn't meet minimum, use minimum size
        size = int(min_size)
        total_cost = size * price
        print(f"🛒 $1 is below minimum ({min_size}), using minimum size: {size} shares at ${price:.3f} (Total: ${total_cost:.2f})")
    else:
        print(f"🛒 Preparing to buy {size} shares at ${price:.3f} (Total: ${size*price:.2f})")
    
    # 5. Place Order
    try:
        print("📝 Placing Order...")
        order_args = OrderArgs(
            price=price,
            size=size,
            side="BUY",
            token_id=yes_token
        )
        # Using create_and_post_order which handles both signing AND submission
        # This requires valid API keys and a matching Private Key
        resp = client.create_and_post_order(order_args)

        if isinstance(resp, dict) and resp.get("success"):
            print("✅ SUCCESS! Order placed.")
            print(f"   Order ID: {resp.get('orderID')}")
        else:
            print(f"❌ Order failed: {resp}")
            
    except Exception as e:
        print(f"❌ Exception during order placement: {e}")

if __name__ == "__main__":
    run_test_trade()
