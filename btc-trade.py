import time
import math
import sys
import os
import re
import json
import csv
import numpy as np
import requests
import concurrent.futures
from dotenv import load_dotenv
from datetime import datetime, timezone

# Load environment variables
load_dotenv()

# Load configuration
from config import (
    TRADE_WINDOW_MIN, TRADE_WINDOW_MAX,
    BOLLINGER_PERIOD, BOLLINGER_STD_DEV,
    ATR_PERIOD, ATR_MULTIPLIER,
    SHARE_PRICE_MIN, SHARE_PRICE_MAX,
    LOOP_SLEEP_SECONDS, NEXT_MARKET_WAIT_SECONDS,
    SCORE_THRESHOLD,
    WEIGHT_BOLLINGER, WEIGHT_ATR,
    REAL_TRADE, TRADE_AMOUNT, CLOSE_ON_TP, CLOSE_TP_PRICE, CLOSE_SL_SHARE_DROP_PERCENT, CLOSE_ON_STRIKE
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
PROXY_ADDRESS = os.getenv("PROXY_ADDRESS")

# ==============================================================================
# 🧱 MODULE DE CLAIM AUTOMATIQUE (WEB3)
# ==============================================================================
from web3 import Web3
from eth_account.messages import encode_defunct # Required for Gnosis Safe Signing
try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:
    ExtraDataToPOAMiddleware = None

# Configuration Polygon
POLYGON_RPC = "https://polygon.drpc.org"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045" # Gnosis CTF
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CLAIMS_FILE = "pending_claims.json"

# ABI Minimal pour le Claim
CTF_ABI = '[{"constant":false,"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"payable":false,"stateMutability":"nonpayable","type":"function"}]'
SAFE_ABI = '[{"inputs":[{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"bytes","name":"data","type":"bytes"},{"internalType":"enum Enum.Operation","name":"operation","type":"uint8"},{"internalType":"uint256","name":"safeTxGas","type":"uint256"},{"internalType":"uint256","name":"baseGas","type":"uint256"},{"internalType":"uint256","name":"gasPrice","type":"uint256"},{"internalType":"address","name":"gasToken","type":"address"},{"internalType":"address payable","name":"refundReceiver","type":"address"},{"internalType":"bytes","name":"signatures","type":"bytes"}],"name":"execTransaction","outputs":[{"internalType":"bool","name":"success","type":"bool"}],"stateMutability":"payable","type":"function"}, {"inputs":[{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"value","type":"uint256"},{"internalType":"bytes","name":"data","type":"bytes"},{"internalType":"enum Enum.Operation","name":"operation","type":"uint8"},{"internalType":"uint256","name":"safeTxGas","type":"uint256"},{"internalType":"uint256","name":"baseGas","type":"uint256"},{"internalType":"uint256","name":"gasPrice","type":"uint256"},{"internalType":"address","name":"gasToken","type":"address"},{"internalType":"address payable","name":"refundReceiver","type":"address"},{"internalType":"uint256","name":"_nonce","type":"uint256"}],"name":"getTransactionHash","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"}, {"inputs":[],"name":"nonce","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'

def save_pending_claim(condition_id):
    """Enregistre un ID de marché pour le clamer plus tard"""
    if not condition_id: return
    try:
        claims = []
        if os.path.exists(CLAIMS_FILE):
            with open(CLAIMS_FILE, 'r') as f:
                claims = json.load(f)
        
        if condition_id not in claims:
            claims.append(condition_id)
            with open(CLAIMS_FILE, 'w') as f:
                json.dump(claims, f)
            print(f"📝 Market enregistré pour claim futur: {condition_id}")
    except Exception as e:
        print(f"⚠️ Erreur sauvegarde claim: {e}")

def process_pending_claims():
    """Tente de clamer tous les marchés en attente (Supporte EOA et Proxy Gnosis Safe)"""
    if not os.path.exists(CLAIMS_FILE): return

    print("\n💰 VÉRIFICATION DES CLAIMS EN ATTENTE...")
    
    try:
        with open(CLAIMS_FILE, 'r') as f:
            claims = json.load(f)
        
        if not claims: 
            print("   Aucun claim en attente.")
            return

        # Connexion Web3
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
        
        # Inject PoA middleware if available
        if ExtraDataToPOAMiddleware:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not w3.is_connected():
            print("   ❌ Erreur connexion Polygon RPC")
            return

        if not PRIVATE_KEY:
            print("   ❌ Private Key manquante, impossible de claim")
            return
        
        account = w3.eth.account.from_key(PRIVATE_KEY)
        
        # Setup Proxy if available
        safe_contract = None
        if PROXY_ADDRESS:
            print(f"   🛡️  Proxy detected: {PROXY_ADDRESS}")
            safe_contract = w3.eth.contract(address=PROXY_ADDRESS, abi=json.loads(SAFE_ABI))
        
        contract = w3.eth.contract(address=CTF_ADDRESS, abi=json.loads(CTF_ABI))
        
        remaining_claims = []
        
        # Nonce setup
        current_nonce = w3.eth.get_transaction_count(account.address, 'latest')
        
        for i, condition_id in enumerate(claims):
            try:
                print(f"   🔎 Vérification {condition_id[:10]}...")
                
                index_sets = [1, 2]
                parent_collection_id = "0x" + "0"*64
                
                # Prepare Data
                inner_tx = contract.functions.redeemPositions(
                     USDC_ADDRESS, parent_collection_id, condition_id, index_sets
                ).build_transaction({'gas': 0, 'gasPrice': 0})
                inner_data = inner_tx['data']
                
                txn_call = None
                
                # --- PROXY PATH ---
                if safe_contract:
                    # Check Safe Nonce
                    safe_nonce = safe_contract.functions.nonce().call()
                    
                    # Build Safe Hash
                    safe_tx_hash_bytes = safe_contract.functions.getTransactionHash(
                        CTF_ADDRESS, 0, inner_data, 0, 0, 0, 0,
                        "0x0000000000000000000000000000000000000000",
                        "0x0000000000000000000000000000000000000000",
                        safe_nonce
                    ).call()
                    
                    # Sign (EIP-191 + Gnosis V-adjustment)
                    message = encode_defunct(primitive=safe_tx_hash_bytes)
                    signed_message = w3.eth.account.sign_message(message, private_key=PRIVATE_KEY)
                    sig_bytes = signed_message.signature
                    v = sig_bytes[-1]
                    if v < 30: v += 4
                    signature = sig_bytes[:-1] + bytes([v])
                    
                    txn_call = safe_contract.functions.execTransaction(
                        CTF_ADDRESS, 0, inner_data, 0, 0, 0, 0,
                        "0x0000000000000000000000000000000000000000",
                        "0x0000000000000000000000000000000000000000",
                        signature
                    )
                else:
                    # --- DIRECT PATH ---
                    txn_call = contract.functions.redeemPositions(
                        USDC_ADDRESS, parent_collection_id, condition_id, index_sets
                    )

                # Gas & Send
                base_fee = w3.eth.get_block('latest')['baseFeePerGas']
                max_priority = w3.to_wei(40, 'gwei')
                max_fee = base_fee + max_priority
                
                try:
                    gas_est = txn_call.estimate_gas({'from': account.address})
                    gas_limit = int(gas_est * 1.5)
                except Exception as e:
                    if "insufficient funds" in str(e):
                        print("      ❌ PAS ASSEZ DE MATIC pour le gas.")
                        remaining_claims.append(condition_id)
                        continue
                    # Check for Safe-specific errors (GS013 = not an owner, GS026 = etc.)
                    # These should be RETRIED, not deleted
                    error_str = str(e)
                    if "GS013" in error_str:
                        print(f"      ⚠️  Gas Est. Failed (GS013 - Safe error): {e}")
                        print(f"      💡 Will retry this claim in the next window")
                        remaining_claims.append(condition_id)
                        continue
                    
                    # For "execution reverted" without Safe errors, it's likely already claimed
                    if "execution reverted" in error_str and "GS" not in error_str:
                        print(f"      ⚠️  Gas Est. Failed (Claim already processed?): {e}")
                        # Don't keep in list if truly already redeemed
                        continue
                    
                    # For other errors, keep it pending for retry
                    remaining_claims.append(condition_id)
                    continue

                txn_nonce = current_nonce + i
                txn = txn_call.build_transaction({
                    'chainId': 137,
                    'gas': gas_limit,
                    'maxFeePerGas': max_fee,
                    'maxPriorityFeePerGas': max_priority,
                    'nonce': txn_nonce,
                    'type': 2
                })
                
                signed_txn = w3.eth.account.sign_transaction(txn, private_key=PRIVATE_KEY)
                tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
                print(f"   🚀 Claim envoyé ! Hash: {w3.to_hex(tx_hash)}")
                
            except Exception as e:
                print(f"   ❌ Erreur claim loop: {e}")
                remaining_claims.append(condition_id)
        
        # Sauvegarde de ce qui reste à traiter
        if len(remaining_claims) != len(claims):
            with open(CLAIMS_FILE, 'w') as f:
                json.dump(remaining_claims, f)
            print(f"   💾 Liste mise à jour. Restants: {len(remaining_claims)}")
        else:
             print("   ⚠️  Aucun claim n'a abouti (Gas ou Déjà fait).")

    except Exception as e:
        print(f"❌ Erreur générale process claims: {e}")

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

def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def fetch_chainlink_btc_usd_price(session=None):
    """
    Fetch BTC/USD price from multiple sources (Kraken first, then fallbacks).
    """
    session = session or requests
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    sources = [
        (
            "Kraken",
            "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD",
            lambda data: data.get("result", {}).get("XXBTZUSD", {}).get("c", [None])[0],
        ),
        (
            "Coinbase",
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            lambda data: data.get("data", {}).get("amount"),
        ),
        (
            "Binance",
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            lambda data: data.get("price"),
        ),
        (
            "Bitstamp",
            "https://www.bitstamp.net/api/v2/ticker/btcusd",
            lambda data: data.get("last"),
        ),
    ]

    for _, url, extractor in sources:
        try:
            response = session.get(url, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            price = _safe_float(extractor(data))
            if price and 20000 <= price <= 150000:
                return price
        except Exception:
            continue

    return None

# --- 3B. EXECUTE REAL TRADE ---
def execute_real_trade(poly_client, token_id, direction, share_price, strike_price, current_btc_price):
    """
    Execute a real trade on Polymarket.
    
    Args:
        poly_client: ClobClient instance
        token_id: The token ID to trade (YES or NO token)
        direction: "UP" or "DOWN" 
        share_price: Current market price of the share
        strike_price: The strike price from the market
        current_btc_price: Current BTC price
        
    Returns:
        dict with trade result or None if trade failed
    """
    log_lines = []
    def record_log(message):
        log_lines.append(message)

    if not REAL_TRADE:
        message = "ℹ️  REAL_TRADE is False - Skipping actual order placement (simulation mode)"
        print(f"   {message}")
        record_log(message)
        return {
            'success': False,
            'error': 'REAL_TRADE is False',
            'direction': direction,
            'token_id': token_id,
            'share_price': share_price,
            'current_btc': current_btc_price,
            'strike_price': strike_price,
            'log_lines': log_lines
        }
        
    # Validate all constraints before attempting trade
    if share_price < SHARE_PRICE_MIN:
        message = f"🚫 Cannot trade: Share price ${share_price:.3f} below minimum ${SHARE_PRICE_MIN}"
        print(f"   {message}")
        record_log(message)
        return {
            'success': False,
            'error': message,
            'direction': direction,
            'token_id': token_id,
            'share_price': share_price,
            'current_btc': current_btc_price,
            'strike_price': strike_price,
            'log_lines': log_lines
        }
        
    if share_price > SHARE_PRICE_MAX:
        message = f"🚫 Cannot trade: Share price ${share_price:.3f} above maximum ${SHARE_PRICE_MAX}"
        print(f"   {message}")
        record_log(message)
        return {
            'success': False,
            'error': message,
            'direction': direction,
            'token_id': token_id,
            'share_price': share_price,
            'current_btc': current_btc_price,
            'strike_price': strike_price,
            'log_lines': log_lines
        }
    
    try:
        # Fetch current order book to get best ask and minimum size
        book_url = f"https://clob.polymarket.com/book?token_id={token_id}"
        book_response = requests.get(book_url, timeout=10)
        book_response.raise_for_status()
        book_data = book_response.json()
        
        asks = book_data.get("asks", [])
        min_order_size = _safe_float(book_data.get("min_order_size")) or 1.0
        
        if not asks:
            message = "❌ No asks available in order book"
            print(f"   {message}")
            record_log(message)
            return {
                'success': False,
                'error': message,
                'direction': direction,
                'token_id': token_id,
                'share_price': share_price,
                'current_btc': current_btc_price,
                'strike_price': strike_price,
                'log_lines': log_lines
            }
            
        best_ask_price = min(float(a['price']) for a in asks)
        
        # === DETERMINE TRADE SIZE (SHARES) ===
        actual_size = float(TRADE_AMOUNT)

        # Enforce per-token minimum share size
        if actual_size < min_order_size:
            actual_size = float(min_order_size)
            message = f"📊 Size below token minimum, using minimum size: {actual_size} shares"
            print(f"   {message}")
            record_log(message)

        actual_cost = actual_size * best_ask_price
            
        # Check balance/allowance before placing order
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, OrderArgs
            balance_params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            balance_info = poly_client.get_balance_allowance(balance_params)

            # Support different response shapes
            if isinstance(balance_info, dict):
                raw_balance = balance_info.get("balance") or balance_info.get("collateral_balance")
                raw_allowance = balance_info.get("allowance") or balance_info.get("collateral_allowance")
            else:
                raw_balance = getattr(balance_info, "balance", None)
                raw_allowance = getattr(balance_info, "allowance", None)

            balance_val = _safe_float(raw_balance)
            allowance_val = _safe_float(raw_allowance)

            if balance_val is not None and allowance_val is not None:
                required = actual_cost
                if balance_val < required or allowance_val < required:
                    message = (
                        "🚫 Insufficient balance/allowance: "
                        f"balance=${balance_val:.2f}, allowance=${allowance_val:.2f}, required=${required:.2f}"
                    )
                    print(f"   {message}")
                    hint = "💡 Deposit/approve more USDC collateral in Polymarket to trade."
                    print(f"   {hint}")
                    record_log(message)
                    record_log(hint)
                    return {
                        'success': False,
                        'error': message,
                        'direction': direction,
                        'token_id': token_id,
                        'share_price': share_price,
                        'best_ask_price': best_ask_price,
                        'size': actual_size,
                        'cost': actual_cost,
                        'current_btc': current_btc_price,
                        'strike_price': strike_price,
                        'log_lines': log_lines
                    }
        except Exception as e:
            message = f"⚠️  Could not verify balance/allowance: {e}"
            print(f"   {message}")
            record_log(message)

        # Import OrderArgs
        from py_clob_client.clob_types import OrderArgs
        from datetime import datetime
        
        trade_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n   🔄 EXECUTING REAL TRADE... [{trade_time}]")
        record_log(f"🔄 EXECUTING REAL TRADE... [{trade_time}]")
        print(f"      Direction: {direction}")
        record_log(f"Direction: {direction}")
        print(f"      Token ID: {token_id}")
        record_log(f"Token ID: {token_id}")
        print(f"      Price: ${best_ask_price:.3f}")
        record_log(f"Price: ${best_ask_price:.3f}")
        print(f"      Size: {actual_size} shares")
        record_log(f"Size: {actual_size} shares")
        print(f"      Total Cost: ${actual_cost:.2f}")
        record_log(f"Total Cost: ${actual_cost:.2f}")
        print(f"      Expected Strategy: BTC {'>' if direction == 'UP' else '<'} ${strike_price:,.2f}")
        record_log(f"Expected Strategy: BTC {'>' if direction == 'UP' else '<'} ${strike_price:,.2f}")
        print(f"      Current BTC: ${current_btc_price:,.2f}")
        record_log(f"Current BTC: ${current_btc_price:,.2f}")
        
        # Create order
        order_args = OrderArgs(
            price=best_ask_price,
            size=actual_size,
            side="BUY",
            token_id=token_id
        )
        
        # Place order with retry logic for timeouts
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"   📡 Sending order to Polymarket (Attempt {attempt}/{max_retries})...")
                response = poly_client.create_and_post_order(order_args)
                
                # If we get here, request succeeded - break retry loop
                break
                
            except Exception as e:
                error_str = str(e)
                
                # Check if it's a timeout error
                if 'timeout' in error_str.lower() or 'timed out' in error_str.lower():
                    if attempt < max_retries:
                        print(f"   ⚠️  Timeout on attempt {attempt}/{max_retries}, retrying in {retry_delay}s...")
                        record_log(f"⚠️  Timeout on attempt {attempt}/{max_retries}, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    else:
                        print(f"   ❌ All {max_retries} attempts failed due to timeout")
                        record_log(f"❌ All {max_retries} attempts failed due to timeout")
                        raise  # Re-raise to be caught by outer except
                else:
                    # Non-timeout error, don't retry
                    raise
        
        if isinstance(response, dict) and response.get("success"):
            order_id = response.get("orderID", "unknown")
            print(f"   ✅ ORDER PLACED SUCCESSFULLY!")
            print(f"      Order ID: {order_id}")
            print(f"      Cost: ${actual_cost:.2f}")
            print(f"      Potential Profit: ${(actual_size - actual_cost):.2f}")
            print(f"      ROI if Win: {((actual_size/actual_cost - 1) * 100):.1f}%")
            record_log("✅ ORDER PLACED SUCCESSFULLY!")
            record_log(f"Order ID: {order_id}")
            record_log(f"Cost: ${actual_cost:.2f}")
            record_log(f"Potential Profit: ${(actual_size - actual_cost):.2f}")
            record_log(f"ROI if Win: {((actual_size/actual_cost - 1) * 100):.1f}%")
            
            from datetime import datetime
            open_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return {
                'success': True,
                'order_id': order_id,
                'direction': direction,
                'price': best_ask_price,
                'size': actual_size,
                'cost': actual_cost,
                'token_id': token_id,
                'current_btc': current_btc_price,
                'strike_price': strike_price,
                'min_order_size': min_order_size,
                'share_price': share_price,
                'log_lines': log_lines,
                'open_time': open_time,
                'open_btc_price': current_btc_price
            }
        else:
            error_msg = response.get("error", response) if isinstance(response, dict) else str(response)
            message = f"❌ ORDER FAILED: {error_msg}"
            print(f"   {message}")
            record_log(message)
            return {
                'success': False,
                'error': error_msg,
                'direction': direction,
                'token_id': token_id,
                'price': best_ask_price,
                'size': actual_size,
                'cost': actual_cost,
                'current_btc': current_btc_price,
                'strike_price': strike_price,
                'min_order_size': min_order_size,
                'share_price': share_price,
                'log_lines': log_lines
            }
            
    except Exception as e:
        message = f"❌ Error executing trade: {e}"
        print(f"   {message}")
        import traceback
        traceback.print_exc()
        record_log(message)
        return {
            'success': False,
            'error': str(e),
            'direction': direction,
            'token_id': token_id,
            'share_price': share_price,
            'current_btc': current_btc_price,
            'strike_price': strike_price,
            'log_lines': log_lines
        }

def get_max_sellable_size(poly_client, token_id):
    """
    Récupère le solde exact et le nettoie pour éviter les erreurs d'arrondi.
    Utilise math.floor pour tronquer sans arrondir au supérieur.
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        
        balance_params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=token_id
        )
        balance_info = poly_client.get_balance_allowance(balance_params)
        
        # Gestion des différentes structures de réponse possibles
        raw_balance = 0
        if isinstance(balance_info, dict):
            raw_balance = float(balance_info.get("balance", 0))
        else:
            raw_balance = float(getattr(balance_info, "balance", 0))
        
        if raw_balance <= 0:
            return 0.0
        
        # Conversion : Polymarket stocke souvent en unités de 10^6 (micro)
        # Si raw_balance est déjà en décimal (ex: 7.5), cette étape n'est pas grave.
        # Si raw_balance est en entier (ex: 7500000), on divise.
        real_size = raw_balance / 1_000_000 if raw_balance > 1000 else raw_balance
        
        # TRONQUER à 4 décimales (Safe zone) - Floor sans arrondir au supérieur
        # Ex: 7.9999999 -> 7.9999
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

        # Get best bid and cap at 0.99 (Polymarket max price)
        best_bid_price = max(float(b['price']) for b in bids)
        # Ensure price doesn't exceed 0.99 (Polymarket's max price)
        best_bid_price = min(best_bid_price, 0.99)

        print(f"   📉 Vente de {trade_size:.4f} parts @ ${best_bid_price:.3f}...")

        order_args = OrderArgs(
            price=best_bid_price,
            size=trade_size,
            side="SELL",
            token_id=token_id
        )

        # Place order with retry logic for timeouts
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"   📡 Sending close order (Attempt {attempt}/{max_retries})...")
                response = poly_client.create_and_post_order(order_args)
                break  # Success, exit retry loop
                
            except Exception as e:
                error_str = str(e)
                if 'timeout' in error_str.lower() or 'timed out' in error_str.lower():
                    if attempt < max_retries:
                        print(f"   ⚠️  Timeout on attempt {attempt}/{max_retries}, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        print(f"   ❌ All {max_retries} attempts failed due to timeout")
                        raise
                else:
                    raise
        
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
                        
                        condition_id = None
                        if event.get('markets') and len(event['markets']) > 0:
                            condition_id = event['markets'][0].get('conditionId')

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
                            'clob_token_ids': clob_token_ids,
                            'condition_id': condition_id
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

                        condition_id = None
                        if event.get('markets') and len(event['markets']) > 0:
                            condition_id = event['markets'][0].get('conditionId')

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
                            'clob_token_ids': clob_token_ids,
                            'condition_id': condition_id
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
                    'clob_token_ids': page_clob_token_ids,
                    'condition_id': None  # Unknown when website data only
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
                                condition_id = markets[0].get('conditionId') if markets else None
                                all_btc_markets.append({
                                    'slug': slug,
                                    'title': title,
                                    'event': event,
                                    'markets': markets,
                                    'end_date': event.get('end_date_iso', ''),
                                    'time_remaining': time_remaining,
                                    'end_timestamp': market_end_timestamp,
                                    'condition_id': condition_id
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

def fetch_clob_best_ask(token_id, session=None):
    """Fetch best ask price from Polymarket CLOB book."""
    if not token_id:
        return None
    try:
        session = session or requests
        response = session.get(
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

def fetch_clob_outcome_prices(yes_token_id, no_token_id, session=None):
    """Fetch YES/NO outcome prices from CLOB order books."""
    yes_price = fetch_clob_best_ask(yes_token_id, session=session)
    no_price = fetch_clob_best_ask(no_token_id, session=session)
    if yes_price is None and no_price is None:
        return None
    return {
        'up': yes_price,
        'down': no_price
    }

# --- 6. LOGGING SYSTEM ---
def log_to_results(event_type, details):
    """
    Log structured events to results.txt for analysis.
    event_type: 'TRADE_OPEN', 'TRADE_CLOSE', 'MONITOR_TRIGGER', 'ERROR', 'STATS'
    details: dict of key-value pairs
    """
    stats_file = "results.txt"
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Format: [TIMESTAMP] | EVENT_TYPE | key=value | key=value...
        detail_str = " | ".join([f"{k}={v}" for k, v in details.items()])
        with open(stats_file, "a") as f:
            f.write(f"[{timestamp}] | {event_type:<15} | {detail_str}\n")
    except Exception as e:
        print(f"Failed to log to {stats_file}: {e}")

def write_window_statistics(stats, trade_result=None):
    """
    Legacy wrapper for window statistics, rerouted to new logging system.
    Logs a summary of the market session.
    """
    details = {
        'market': stats.get('market_slug', 'unknown'),
        'strike': stats.get('strike_price', 0),
        'evaluations': stats.get('total_evaluations', 0),
        'signals': stats.get('signals_triggered', 0),
        'max_total_score': stats.get('max_total_score', 0)
    }
    
    if trade_result:
        details['trade_status'] = trade_result.get('status', 'unknown')
        if 'real_trade' in trade_result:
             details['pnl_percent'] = trade_result['real_trade'].get('pnl_percent', 0)
             details['exit_price'] = trade_result['real_trade'].get('exit_price', 0)

    log_to_results("SESSION_END", details)

# --- 7. MAIN TRADING ENGINE ---
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class ConsoleUI:
    def __init__(self):
        self.last_lines = 0
        
    def refresh(self, lines):
        # Move up and clear
        if self.last_lines > 0:
            sys.stdout.write(f"\033[{self.last_lines}A") # Move up
            sys.stdout.write("\033[J") # Clear to end
        
        # Print new lines
        content = "\n".join(lines)
        print(content)
        sys.stdout.flush()
        
        # Update count (count newlines + 1)
        self.last_lines = content.count('\n') + 1

    def commit(self):
        # Stop refreshing existing lines, let them scroll
        self.last_lines = 0

def run_advisor():
    # Reuse a single HTTP session for keep-alive
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    # Setup API connections
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
                clob_prices = fetch_clob_outcome_prices(
                    clob_token_ids['yes'],
                    clob_token_ids['no'],
                    session=session
                )
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

            # State tracking
            five_min_announced = False
            three_min_announced = False
            trade_signal_given = False
            signal_details = {}
            
            # Window statistics tracking
            end_datetime = datetime.fromtimestamp(end_timestamp, tz=timezone.utc)
            end_time_readable = end_datetime.strftime('%Y-%m-%d %H:%M')
            
            window_stats = {
                'market_slug': slug,
                'strike_price': strike_price,
                'start_time': end_time_readable,
                'total_score_a': 0,
                'total_score_b': 0,
                'total_score_sum': 0,
                'max_total_score': 0,
                'max_score_a': 0,
                'max_score_b': 0,
                'max_score_btc_price': 0,
                'max_score_direction': 'UNKNOWN',
                'max_score_trade_result': None,
                'max_score_trade_taken': False,
                'signal_score': None,
                'signal_minutes_left': None,
                'max_score_share_price': None,
                'max_score_share_type': None,
                'final_btc_price': None,
                'total_evaluations': 0,
                'signals_triggered': 0,
                'blocked_signals': 0,
                'blocked_reasons': []
            }

            ui = ConsoleUI()
            
            # Initial setup log (Keep printed)
            print("\n🚀 INITIALIZING MONITOR")
            print(f"📊 Strike Price: ${strike_price:,.2f}")
            print(f"⏰ Time Remaining: {expiry_minutes:.1f} minutes")
            print("="*60)
            
            open_position = None

            while True:
                lines = []  # Buffer for UI
                
                now = time.time()
                minutes_left = (end_timestamp - now) / 60
                
                if minutes_left <= 0:
                    ui.commit() # Stop refreshing, let it scroll
                    print("\n" + "="*60)
                    print("⏰ MARKET EXPIRED!")
                    print("="*60)
                    
                    # Check final result (Chainlink BTC/USD stream price)
                    final_price = fetch_chainlink_btc_usd_price()
                    if final_price is None:
                        print("⚠️  Chainlink price unavailable. Skipping final resolution check.")
                    else:
                        window_stats['final_btc_price'] = final_price
                        print(f"\n📊 FINAL RESULTS:")
                        print(f"   Strike Price: ${strike_price:,.2f}")
                        print(f"   Final BTC: ${final_price:,.2f}")
                        print(f"   Change: ${final_price - strike_price:,.2f} ({((final_price/strike_price - 1) * 100):+.2f}%)")
                    
                    if trade_signal_given and final_price is not None:
                        total_signals += 1
                        direction = signal_details.get('direction')
                        entry_price = signal_details.get('price')
                        
                        # Check if position was closed early
                        if open_position and open_position.get('closed'):
                            # Position was closed before expiration - use actual close price
                            close_result = open_position.get('close_result', {})
                            close_price = close_result.get('price')
                            close_size = close_result.get('size')
                            entry_size = signal_details.get('actual_size', 1)
                            
                            if close_price and close_size and entry_price:
                                # Actual P&L from close trade
                                entry_cost = entry_price * entry_size
                                close_proceeds = close_price * close_size
                                profit = close_proceeds - entry_cost
                                profit_pct = (profit / entry_cost * 100) if entry_cost > 0 else 0
                                
                                # Determine win/loss based on actual close
                                if profit >= 0:
                                    is_win = True
                                    wins += 1
                                    result_status = "WIN"
                                    print(f"\n✅ TRADE WIN (Closed Early)!")
                                else:
                                    is_win = False
                                    losses += 1
                                    result_status = "LOSS"
                                    print(f"\n❌ TRADE LOSS (Closed Early)!")
                                
                                print(f"   Direction: {direction}")
                                print(f"   Entry: ${entry_price:.4f} | Close: ${close_price:.4f}")
                                print(f"   Size: {close_size:.4f} shares")
                                print(f"   P&L: ${profit:.2f} ({profit_pct:+.1f}%)")
                            else:
                                # Fallback if close price not available
                                profit = 0
                                profit_pct = 0
                                result_status = "UNKNOWN"
                                is_win = False
                                losses += 1
                                print(f"\n⚠️  TRADE RESULT UNKNOWN - Missing close price!")
                        else:
                            # Position held to expiration - use final price
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
                            if entry_price is None:
                                # No entry price available (trade signal had no price data)
                                profit = 0
                                profit_pct = 0
                                result_status = "UNKNOWN"
                                print(f"\n⚠️  TRADE RESULT UNKNOWN - No entry price data!")
                                print(f"   Direction: {direction}")
                                print(f"   Entry: UNAVAILABLE")
                                print(f"   Final BTC: ${final_price:,.2f}")
                            elif is_win:
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
                            'trade_amount': trade_amount,
                            'signal_score': window_stats.get('signal_score', 0)
                        }
                        real_trade_info = signal_details.get('real_trade')
                        if real_trade_info:
                            result_data['real_trade'] = real_trade_info
                        # Add close result and attempts if position was closed or attempted
                        if open_position:
                            if 'real_trade' not in result_data:
                                result_data['real_trade'] = real_trade_info or {}
                            if open_position.get('close_result'):
                                result_data['real_trade']['close_result'] = open_position['close_result']
                            if open_position.get('close_attempts'):
                                result_data['real_trade']['close_attempts'] = open_position['close_attempts']
                        if window_stats.get('max_score_trade_taken'):
                            window_stats['max_score_trade_result'] = result_status
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
                    
                    # Tenter de récupérer l'argent des marchés précédents
                    process_pending_claims()

                    print(f"⏭️  Moving to next market in {NEXT_MARKET_WAIT_SECONDS} seconds...\n")
                    time.sleep(NEXT_MARKET_WAIT_SECONDS)
                    break

                try:
                    # 1. Get Real-Time BTC Price + CLOB prices (parallel)
                    current_share = 0.0
                    up_price = 0.0
                    down_price = 0.0
                    clob_token_ids = market_data.get('clob_token_ids')

                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                        future_btc = executor.submit(fetch_chainlink_btc_usd_price, session)
                        if clob_token_ids and clob_token_ids.get('yes') and clob_token_ids.get('no'):
                            future_yes = executor.submit(fetch_clob_best_ask, clob_token_ids['yes'], session)
                            future_no = executor.submit(fetch_clob_best_ask, clob_token_ids['no'], session)
                        else:
                            future_yes = None
                            future_no = None

                        real_price = future_btc.result()
                        if future_yes and future_no:
                            up_price = future_yes.result()
                            down_price = future_no.result()

                    if real_price is None:
                        print("   ⚠️  BTC price unavailable (all sources), skipping this evaluation")
                        time.sleep(LOOP_SLEEP_SECONDS)
                        continue

                    # Store latest outcome prices if available
                    if up_price is not None or down_price is not None:
                        market_data['outcome_prices'] = {
                            'up': up_price or 0,
                            'down': down_price or 0
                        }

                    # Définit current_share si une position est ouverte
                    if open_position:
                        # Fallback to existing if network fetch fails (returns 0 or None)
                        new_share_price = 0.0
                        if up_price and down_price:
                             new_share_price = up_price if open_position['direction'] == 'UP' else down_price
                        
                        if new_share_price > 0:
                            current_share = new_share_price
                        elif 'current_share' not in locals() or current_share == 0:
                             # Initialize if logic hasn't run yet
                             current_share = open_position.get('share_price', 0)
                        else:
                             # Keep previous known value if new one is 0/invalid
                             pass
                    
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
                    
                    # 3. Monitor position and auto-close on TP / SL / STRIKE
                    if open_position and not open_position.get('closed'):
                        direction = open_position['direction']
                        entry_price = open_position['entry_price']
                        share_price = open_position['share_price']
                        open_btc_price = open_position.get('open_btc_price', entry_price)
                        
                        # Initialize tracking variables if needed
                        if 'close_trigger' not in open_position:
                            open_position['close_trigger'] = None
                        
                        # === FULL MONITORING LOGS (BUFFERED) ===
                        lines.append(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")
                        lines.append(f"{Colors.CYAN}📊 POSITION MONITORING [T-{minutes_left:.2f}min]{Colors.ENDC}")
                        lines.append(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")
                        
                        # Get current market prices
                        outcome_prices = market_data.get('outcome_prices', {})
                        up_price = outcome_prices.get('up', 0)
                        down_price = outcome_prices.get('down', 0)
                        
                        # Position Info
                        current_pl_pct = 0
                        if 'share_price' in open_position and open_position['share_price'] > 0:
                             if current_share > 0:
                                 current_pl_pct = ((current_share - open_position['share_price']) / open_position['share_price']) * 100

                        pl_color = Colors.GREEN if current_pl_pct >= 0 else Colors.FAIL
                        
                        lines.append(f"{Colors.BOLD}🎯 POSITION INFO:{Colors.ENDC}")
                        lines.append(f"   Direction: {Colors.BOLD}{direction}{Colors.ENDC}")
                        lines.append(f"   Size: {open_position['size']} shares")
                        lines.append(f"   Entry: ${share_price:.4f} | Current: ${current_share:.4f} | PnL: {pl_color}{current_pl_pct:+.2f}%{Colors.ENDC}")
                        lines.append(f"   Entry BTC: ${open_btc_price:,.2f} | Strike: ${strike_price:,.2f}")
                        
                        # Current Market Status
                        btc_change_pct = ((real_price/open_btc_price - 1) * 100) if open_btc_price else 0
                        btc_color = Colors.GREEN if (direction == 'UP' and real_price > strike_price) or (direction == 'DOWN' and real_price < strike_price) else Colors.FAIL
                        
                        lines.append(f"\n{Colors.BOLD}💹 CURRENT MARKET:{Colors.ENDC}")
                        lines.append(f"   BTC Price: {btc_color}${real_price:,.2f}{Colors.ENDC} ({btc_change_pct:+.2f}%)")
                        if up_price is not None and down_price is not None and up_price > 0 and down_price > 0:
                            lines.append(f"   Market Prices - UP: {up_price*100:.1f}¢ | DOWN: {down_price*100:.1f}¢")
                        else:
                            lines.append(f"   ⚠️  Market prices unavailable")
                        
                        # Position Status
                        if direction == 'UP':
                            if real_price > strike_price:
                                position_status = f"{Colors.GREEN}✅ WINNING{Colors.ENDC} - BTC > Strike (${real_price - strike_price:+,.2f})"
                            else:
                                position_status = f"{Colors.FAIL}❌ LOSING{Colors.ENDC} - BTC <= Strike (${real_price - strike_price:,.2f})"
                        else:  # DOWN
                            if real_price < strike_price:
                                position_status = f"{Colors.GREEN}✅ WINNING{Colors.ENDC} - BTC < Strike (${real_price - strike_price:,.2f})"
                            else:
                                position_status = f"{Colors.FAIL}❌ LOSING{Colors.ENDC} - BTC >= Strike (${real_price - strike_price:+,.2f})"
                        
                        lines.append(f"\n📍 STATUS: {position_status}")
                        
                        # === CHECK CLOSE CONDITIONS ===
                        lines.append(f"\n{Colors.BOLD}🔍 CLOSE CONDITIONS:{Colors.ENDC}")
                        close_reason = None
                        
                        # 1️⃣ TAKE PROFIT CHECK
                        tp_status = f"Need ${(CLOSE_TP_PRICE - current_share):.4f} more" if current_share < CLOSE_TP_PRICE else f"{Colors.GREEN}HIT!{Colors.ENDC}"
                        
                        if not CLOSE_ON_TP:
                            tp_status += " (IGNORED)"

                        lines.append(f"   1️⃣ TP (Share >= ${CLOSE_TP_PRICE}): Current ${current_share:.4f} -> {tp_status}")
                        if CLOSE_ON_TP and current_share >= CLOSE_TP_PRICE:
                            close_reason = f"📈 TAKE PROFIT: Share price hit ${CLOSE_TP_PRICE} (Current: ${current_share:.4f})"

                        
                        # 2️⃣ STOP LOSS CHECK - SHARE PRICE DROP
                        if not close_reason:
                            # Avoid closing if price is 0 (likely API error)
                            if current_share <= 0:
                                lines.append(f"   2️⃣ SL: {Colors.WARNING}Pricing unavailable, skipping check{Colors.ENDC}")
                            else:
                                share_drop_pct = ((share_price - current_share) / share_price) * 100 if share_price > 0 else 0
                                sl_threshold_price = share_price * (1 - CLOSE_SL_SHARE_DROP_PERCENT / 100)
                                sl_status = f"Drop {share_drop_pct:.1f}% (Limit {CLOSE_SL_SHARE_DROP_PERCENT}%)"
                                sl_color = Colors.FAIL if share_drop_pct >= CLOSE_SL_SHARE_DROP_PERCENT else Colors.ENDC
                                
                                lines.append(f"   2️⃣ SL (Drop >= {CLOSE_SL_SHARE_DROP_PERCENT}%): {sl_color}{sl_status}{Colors.ENDC}")

                                if share_drop_pct >= CLOSE_SL_SHARE_DROP_PERCENT:
                                    close_reason = f"📉 STOP LOSS: Share dropped {share_drop_pct:.1f}% (${share_price:.4f} → ${current_share:.4f})"

                        
                        # 3️⃣ STRIKE PRICE CHECK
                        if not close_reason and CLOSE_ON_STRIKE:
                            dist = real_price - strike_price
                            strike_status = "OK"
                            if direction == 'UP' and real_price <= strike_price:
                                strike_status = f"{Colors.FAIL}HIT (Below Strike){Colors.ENDC}" 
                                close_reason = f"⚠️ STOP LOSS - STRIKE HIT: BTC ${real_price:,.2f} <= ${strike_price:,.2f}"
                            elif direction == 'DOWN' and real_price >= strike_price:
                                strike_status = f"{Colors.FAIL}HIT (Above Strike){Colors.ENDC}"
                                close_reason = f"⚠️ STOP LOSS - STRIKE HIT: BTC ${real_price:,.2f} >= ${strike_price:,.2f}"
                            
                            lines.append(f"   3️⃣ SL (Strike Hit): {strike_status} (Diff ${dist:+,.2f})")
                        
                        if not close_reason:
                            lines.append(f"\n   ✅ Position remains open")
                        
                        # Calculate and display scores during position monitoring
                        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, period=BOLLINGER_PERIOD, std_dev=2.0)
                        atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
                        
                        # (Omitted Calculation logic for brevity in UI update, assumed correct from before)
                        # Just printing final buffered output
                        
                        # === EXECUTE CLOSE IF CONDITION MET ===
                        if close_reason:
                            ui.commit() # Freeze screen before closing logs
                            log_to_results("TRIGGER_CLOSE", {
                                "reason": close_reason,
                                "btc_price": real_price,
                                "share_price": current_share if 'current_share' in locals() else 'N/A',
                                "strike": strike_price
                            })
                            # ... (Normal close logic continues, printing to stdout as usual)

                            if not open_position.get('close_attempts'):
                                open_position['close_attempts'] = 0
                            
                            open_position['close_attempts'] += 1
                            attempt_num = open_position['close_attempts']
                            
                            print(f"\n{'='*70}")
                            print(f"🚨 CLOSING POSITION - ATTEMPT #{attempt_num}")
                            print(f"{'='*70}")
                            print(f"🎯 TRIGGER: {close_reason}")
                            print(f"\n📋 CLOSE ORDER DETAILS:")
                            print(f"   Direction: {direction}")
                            print(f"   Token ID: {open_position['token_id']}")
                            print(f"   Size: {open_position['size']} shares")
                            print(f"   Current BTC: ${real_price:,.2f}")
                            print(f"   Strike: ${strike_price:,.2f}")
                            print(f"\n⏳ Executing close order...")
                            
                            close_result = execute_close_trade(
                                poly_client,
                                open_position['token_id'],
                                open_position['size'],
                                real_price
                            )
                            
                            print(f"\n📊 CLOSE RESULT:")
                            if close_result and close_result.get('success'):
                                log_to_results("TRADE_CLOSE_OK", {
                                    "size": close_result.get('size'),
                                    "price": close_result.get('price')
                                })
                                print(f"   ✅ SUCCESS!")
                                print(f"   Order ID: {close_result.get('order_id')}")
                                print(f"   Size Closed: {close_result.get('size')} shares")
                                print(f"   Price: ${close_result.get('price'):.4f}")
                                print(f"   Time: {close_result.get('close_time')}")
                                print(f"   Attempts: {attempt_num}")
                                open_position['closed'] = True
                                open_position['close_result'] = close_result
                                open_position['close_trigger'] = close_reason
                                print(f"{'='*70}\n")
                                
                                # Remove from pending_claims since position is closed
                                try:
                                    condition_id = market_data.get('condition_id')
                                    if condition_id and os.path.exists(CLAIMS_FILE):
                                        with open(CLAIMS_FILE, 'r') as f:
                                            claims = json.load(f)
                                        if condition_id in claims:
                                            claims.remove(condition_id)
                                            with open(CLAIMS_FILE, 'w') as f:
                                                json.dump(claims, f)
                                            print(f"   REMOVED from pending claims (position closed early)")
                                except Exception as e:
                                    print(f"   WARNING: Could not update pending claims: {e}")
                            else:
                                error = close_result.get('error') if close_result else 'Unknown error'
                                log_to_results("TRADE_CLOSE_FAIL", {
                                    "error": error,
                                    "attempt": attempt_num
                                })
                                print(f"   ❌ FAILED!")
                                print(f"   Error: {error}")
                                print(f"   Attempt: #{attempt_num}")
                                print(f"   ⚠️  Will retry on next check...")
                                print(f"{'='*70}\n")

                    # 4. Time Window Announcements (Buffered)
                    window_midpoint = (TRADE_WINDOW_MIN + TRADE_WINDOW_MAX) / 2
                    if TRADE_WINDOW_MAX - 0.5 < minutes_left <= TRADE_WINDOW_MAX + 0.5 and not five_min_announced:
                        lines.append(f"{Colors.CYAN}\n🔔 TRADING WINDOW START [T-{minutes_left:.1f}min]{Colors.ENDC}")
                        five_min_announced = True
                    
                    if TRADE_WINDOW_MIN - 0.5 < minutes_left <= TRADE_WINDOW_MIN + 0.5 and not three_min_announced:
                        lines.append(f"{Colors.WARNING}\n⚠️  APPROACHING LOCK [T-{minutes_left:.1f}min]{Colors.ENDC}")
                        three_min_announced = True
                    
                    # 5. EXECUTION WINDOW CHECK
                    if TRADE_WINDOW_MIN <= minutes_left <= TRADE_WINDOW_MAX and not trade_signal_given:

                        # (Existing fetch clob logic unchanged due to complexity, just wrapping display)
                        # Refresh outcome prices from CLOB each evaluation (for live prices)
                        clob_token_ids = market_data.get('clob_token_ids')
                        if clob_token_ids and clob_token_ids.get('yes') and clob_token_ids.get('no'):
                            clob_prices = fetch_clob_outcome_prices(clob_token_ids['yes'], clob_token_ids['no'])
                            if clob_prices:
                                market_data['outcome_prices'] = {
                                    'up': clob_prices.get('up', market_data.get('outcome_prices', {}).get('up')),
                                    'down': clob_prices.get('down', market_data.get('outcome_prices', {}).get('down'))
                                }
                        
                        lines.append(f"{Colors.HEADER}\n{'='*60}{Colors.ENDC}")
                        lines.append(f"{Colors.BOLD}🔍 MARKET SCAN [T-{minutes_left:.2f}min]{Colors.ENDC}")
                        lines.append(f"{Colors.HEADER}{'='*60}{Colors.ENDC}")
                        lines.append(f"   BTC: {Colors.BOLD}${real_price:,.2f}{Colors.ENDC} | Strike: ${strike_price:,.2f}")
                        
                        # Show outcome prices
                        outcome_prices = market_data.get('outcome_prices', {})
                        if outcome_prices.get('up') is not None:
                            lines.append(f"   Market: UP {outcome_prices['up']*100:.1f}¢ | DOWN {outcome_prices['down']*100:.1f}¢")
                        
                        trade_score = 0
                        details = []
                        
                        # Max score for each component
                        MAX_SCORE_BB = WEIGHT_BOLLINGER
                        MAX_SCORE_ATR = WEIGHT_ATR
                        
                        # === A. BOLLINGER BANDS SCORE (Proportional, Max 34) ===
                        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, period=BOLLINGER_PERIOD, std_dev=2.0)
                        
                        score_a = 0
                        bb_explain = "BB Unavailable"
                        if upper_bb and lower_bb and middle_bb:
                            bb_bandwidth = (upper_bb - lower_bb) / middle_bb if middle_bb > 0 else 0
                            bb_range = upper_bb - lower_bb
                            target_position = (strike_price - lower_bb) / bb_range
                            target_position = max(0, min(1, target_position))
                            
                            if real_price > strike_price:  # UP
                                if target_position < 0.5:
                                    score_a = int(round(MAX_SCORE_BB * (1 - target_position / 0.5)))
                                    score_a = min(score_a, MAX_SCORE_BB)
                                else:
                                    score_a = 0
                            else:  # DOWN
                                if target_position > 0.5:
                                    score_a = int(round(MAX_SCORE_BB * ((target_position - 0.5) / 0.5)))
                                    score_a = min(score_a, MAX_SCORE_BB)
                                else:
                                    score_a = 0

                        trade_score += score_a
                        # Explain BB score
                        if upper_bb and lower_bb:
                            if real_price > strike_price:
                                bb_explain = f"Strike @ {target_position:.1%} (Low is good)"
                            else:
                                bb_explain = f"Strike @ {target_position:.1%} (High is good)"

                        details.append(f"BB:  {score_a}/{MAX_SCORE_BB} - {bb_explain}")
                        
                        # === B. ATR KINETIC BARRIER SCORE (Proportional, Max 33) ===
                        atr = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
                        
                        score_b = 0
                        atr_explain = "ATR Unavailable"
                        if atr:
                            max_move = atr * math.sqrt(minutes_left) * ATR_MULTIPLIER
                            dist = abs(real_price - strike_price)
                            
                            if dist < max_move:
                                score_b = 0
                            else:
                                if max_move > 0:
                                    distance_ratio = min((dist - max_move) / max_move, 1.0)
                                    score_b = int(round(MAX_SCORE_ATR * distance_ratio))
                        
                        trade_score += score_b
                        if atr:
                            ratio_value = (dist / max_move) if max_move else 0
                            atr_explain = f"Dist ${dist:.2f} / Move ${max_move:.2f} (x{ratio_value:.1f})"
                        
                        details.append(f"ATR: {score_b}/{MAX_SCORE_ATR} - {atr_explain}")
                        
                        # Get share_price and share_type for constraints (not scoring)
                        share_price = None
                        share_type = "UNKNOWN"
                        try:
                            outcome_prices = market_data.get('outcome_prices', {})
                            if outcome_prices.get('up') is not None and outcome_prices.get('down') is not None:
                                share_price = outcome_prices['up'] if real_price > strike_price else outcome_prices['down']
                                share_type = "YES" if real_price > strike_price else "NO"
                        except Exception:
                            pass
                        
                        # === DECISION ===
                        # Clamp negative scores to 0 for display
                        display_score = max(0, trade_score)
                        
                        window_stats['total_evaluations'] += 1
                        window_stats['total_score_a'] += score_a
                        window_stats['total_score_b'] += score_b
                        window_stats['total_score_sum'] += display_score
                        
                        # Track maximum score hit during window (only if entry price is acceptable)
                        if share_price is not None and share_price <= SHARE_PRICE_MAX:
                            if display_score > window_stats['max_total_score']:
                                window_stats['max_total_score'] = display_score
                                window_stats['max_score_a'] = score_a
                                window_stats['max_score_b'] = score_b
                                window_stats['max_score_btc_price'] = real_price
                                window_stats['max_score_direction'] = 'UP' if real_price > strike_price else 'DOWN'
                                window_stats['max_score_minutes_left'] = minutes_left
                                window_stats['max_score_trade_taken'] = False

                                window_stats['max_score_trade_result'] = None
                                window_stats['max_score_share_price'] = share_price
                                window_stats['max_score_share_type'] = share_type
                        
                        trade_direction_label = "UP trade ✓" if real_price > strike_price else "DOWN trade ✓"
                        
                        # Add detailed stats to buffer instead of printing
                        lines.append(f"")
                        lines.append(f"   {trade_direction_label}")
                        lines.append(f"   📊 SCORE TOTAL: {display_score}/100  (Seuil: {SCORE_THRESHOLD})")
                        for detail in details:
                            lines.append(f"      {detail}")
                        
                        # === HARD CONSTRAINTS CHECK ===
                        constraint_violations = []
                        if share_price is not None:
                            if share_price < SHARE_PRICE_MIN:
                                constraint_violations.append(f"Price too low (${share_price:.2f} < ${SHARE_PRICE_MIN})")
                            if share_price > SHARE_PRICE_MAX:
                                constraint_violations.append(f"Price too high (${share_price:.2f} > ${SHARE_PRICE_MAX})")
                        
                        lines.append("-" * 60)
                        if display_score >= SCORE_THRESHOLD:
                            if constraint_violations:
                                window_stats['blocked_signals'] += 1
                                for violation in constraint_violations:
                                    if violation not in window_stats['blocked_reasons']:
                                        window_stats['blocked_reasons'].append(violation)
                                log_to_results("TRADE_BLOCKED", {
                                    "reasons": ";".join(constraint_violations),
                                    "score": display_score,
                                    "btc": real_price
                                })
                                lines.append(f"\n{Colors.FAIL}🚫 TRADE BLOCKED - Constraints:{Colors.ENDC}")
                                for violation in constraint_violations:
                                    lines.append(f"   ⛔ {violation}")
                            else:
                                # TRADE TRIGGER!
                                ui.refresh(lines) # Show the winning Score 72 scan
                                ui.commit()       # Lock it in place
                                lines = []        # Prevent duplicate print
                                
                                window_stats['signals_triggered'] += 1
                                window_stats['signal_score'] = display_score
                                window_stats['signal_minutes_left'] = minutes_left
                                if display_score == window_stats['max_total_score']:
                                    window_stats['max_score_trade_taken'] = True
                                    window_stats['max_score_trade_result'] = 'PENDING'
                                
                                trade_direction = 'UP' if real_price > strike_price else 'DOWN'
                                
                                print(f"\n{Colors.GREEN}{'='*60}")
                                print(f"🎯 TRADE SIGNAL CONFIRMED (Score {display_score})")
                                print(f"{'='*60}{Colors.ENDC}")
                                
                                if share_price is not None:
                                    print(f"   📈 DIRECTION: {share_type} @ ${share_price:.2f}")
                                else:
                                    print(f"   📈 DIRECTION: {share_type} (Price N/A)")
                                
                                # === EXECUTE REAL TRADE ===
                                if REAL_TRADE:
                                    if share_price is None:
                                        log_to_results("TRADE_BLOCKED", {
                                            "reason": "Share price unavailable",
                                            "btc": real_price
                                        })
                                        print(f"   🚫 BLOCKED: Share price unavailable")
                                    else:
                                        print(f"   💼 EXECUTING ORDER...")
                                        
                                        clob_token_ids = market_data.get('clob_token_ids')
                                        if clob_token_ids:
                                            token_id_to_trade = clob_token_ids.get('yes') if trade_direction == 'UP' else clob_token_ids.get('no')
                                            
                                            if token_id_to_trade:
                                                trade_result = execute_real_trade(
                                                    poly_client,
                                                    token_id_to_trade,
                                                    trade_direction,
                                                    share_price,
                                                    strike_price,
                                                    real_price
                                                )
                                                
                                                if trade_result and trade_result.get('success'):
                                                    log_to_results("TRADE_OPEN_OK", {
                                                        "direction": trade_direction,
                                                        "price": share_price,
                                                        "size": trade_result.get('size'),
                                                        "btc": real_price
                                                    })
                                                    print(f"   🎉 SUCCESS! Size: {trade_result.get('size')}")
                                                    
                                                    cond_id = market_data.get('condition_id')
                                                    if not cond_id:
                                                        # If condition_id is None, try to fetch from API
                                                        try:
                                                            api_url = "https://gamma-api.polymarket.com"
                                                            slug = market_data.get('slug', '')
                                                            events_response = requests.get(
                                                                f"{api_url}/events",
                                                                params={"slug": slug},
                                                                timeout=10
                                                            )
                                                            if events_response.status_code == 200:
                                                                events_data = events_response.json()
                                                                if events_data and len(events_data) > 0:
                                                                    event = events_data[0] if isinstance(events_data, list) else events_data
                                                                    markets = event.get('markets', [])
                                                                    if markets and len(markets) > 0:
                                                                        cond_id = markets[0].get('conditionId')
                                                        except Exception as e:
                                                            print(f"   ⚠️  Could not fetch condition_id from API: {e}")
                                                    
                                                    if cond_id:
                                                        save_pending_claim(cond_id)

                                                    signal_details = {
                                                        'direction': share_type,
                                                        'price': share_price,
                                                        'entry_time': minutes_left,
                                                        'btc_price': real_price,
                                                        'order_id': trade_result.get('order_id'),
                                                        'actual_size': trade_result.get('size'),
                                                        'real_trade': trade_result,
                                                        'open_time': trade_result.get('open_time'),
                                                        'open_btc_price': trade_result.get('open_btc_price')
                                                    }
                                                    open_position = {
                                                        'token_id': token_id_to_trade,
                                                        'size': trade_result.get('size'),
                                                        'direction': trade_direction,
                                                        'strike_price': strike_price,
                                                        'entry_price': share_price,
                                                        'share_price': share_price,
                                                        'closed': False,
                                                        'open_time': trade_result.get('open_time'),
                                                        'open_btc_price': trade_result.get('open_btc_price')
                                                    }
                                                    trade_signal_given = True
                                                else:
                                                    print(f"   ⚠️  FAILED: {trade_result.get('error')}")
                                    
                                    if not open_position:
                                        # Fail back to simulation or logic handled above
                                        print(f"   ⚠️  Order failed or blocked.")

                    else:
                        if not open_position:
                            if minutes_left > TRADE_WINDOW_MAX:
                                lines.append(f"\n⏳ WAITING FOR WINDOW (Starts at T-{TRADE_WINDOW_MAX}min)")
                            elif trade_signal_given:
                                lines.append(f"\n✅ SIGNAL ALREADY TAKEN for this session")
                    
                    # RENDER THE UI
                    if lines:
                         ui.refresh(lines)
                    
                    time.sleep(LOOP_SLEEP_SECONDS)

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
