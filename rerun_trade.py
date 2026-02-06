import os
import argparse
from pathlib import Path
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.constants import POLYGON

from test_buy_1usd import find_market, get_token_ids, get_best_ask
from config import TRADE_AMOUNT


def main():
    parser = argparse.ArgumentParser(description="Re-run a real trade on the current BTC 15m market")
    parser.add_argument("--direction", choices=["up", "down"], required=True, help="Trade direction: up (YES) or down (NO)")
    parser.add_argument("--amount", type=float, default=None, help="(deprecated) Trade size in shares")
    parser.add_argument("--shares", type=float, default=TRADE_AMOUNT, help="Trade size in shares")
    parser.add_argument("--token-id", type=str, help="Exact token ID to trade (overrides market lookup)")
    parser.add_argument("--price", type=float, help="Exact limit price to use (overrides best ask)")
    parser.add_argument("--size", type=float, help="Exact share size to use (overrides amount calculation)")
    args = parser.parse_args()

    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api_passphrase = os.getenv("API_PASSPHRASE")
    private_key = os.getenv("PRIVATE_KEY")
    proxy_address = os.getenv("PROXY_ADDRESS")

    missing = [name for name, val in (
        ("API_KEY", api_key),
        ("API_SECRET", api_secret),
        ("API_PASSPHRASE", api_passphrase),
        ("PRIVATE_KEY", private_key),
        ("PROXY_ADDRESS", proxy_address),
    ) if not val]
    if missing:
        print(f"❌ Missing credentials in .env: {', '.join(missing)}")
        return

    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        creds=creds,
        chain_id=POLYGON,
        funder=proxy_address,
        signature_type=2
    )

    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        balance_info = client.get_balance_allowance(params)
        print("💰 Balance/Allowance:", balance_info)
    except Exception as e:
        print(f"⚠️  Could not fetch balance/allowance: {e}")

    if args.token_id:
        token_id = args.token_id
        print(f"✅ Using provided Token ID: {token_id}")
    else:
        slug = find_market()
        if not slug:
            print("❌ Could not find an active BTC 15m market.")
            return
        print(f"✅ Found Market: {slug}")

        tokens = get_token_ids(slug)
        if not tokens:
            print("❌ Could not fetch token IDs.")
            return

        token_id = tokens["yes"] if args.direction == "up" else tokens["no"]
        print(f"✅ Token ID: {token_id}")

    best_price, min_size = get_best_ask(token_id)
    if args.price is not None:
        price = args.price
    else:
        price = best_price
        if not price:
            print("❌ Could not fetch best ask price.")
            return

    # Determine size in shares
    if args.size is not None:
        size = float(args.size)
    else:
        if args.amount is not None:
            print("ℹ️  --amount is deprecated; use --shares instead")
            size = float(args.amount)
        else:
            size = float(args.shares)

    if min_size and size < float(min_size):
        print(f"📊 Size below token minimum, using minimum size: {min_size} shares")
        size = float(min_size)

    total_cost = size * price

    print(f"🛒 Preparing to buy {size} shares at ${price:.3f} (Total: ${total_cost:.2f})")

    try:
        order_args = OrderArgs(
            price=price,
            size=size,
            side="BUY",
            token_id=token_id
        )
        resp = client.create_and_post_order(order_args)
        if isinstance(resp, dict) and resp.get("success"):
            print("✅ Order placed successfully:", resp)
        else:
            print("❌ Order failed:", resp)
    except Exception as e:
        print(f"❌ Error placing order: {e}")


if __name__ == "__main__":
    main()
