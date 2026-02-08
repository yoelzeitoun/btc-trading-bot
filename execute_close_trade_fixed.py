def execute_close_trade(poly_client, token_id, size, current_btc_price=None):
    """
    Close an open position by placing a SELL order at best bid.
    Uses progressive retry to close 100%, falling back to smaller amounts if needed.
    """
    import requests
    from datetime import datetime
    from py_clob_client.clob_types import OrderArgs
    
    # Try to close 100%, then progressively less if balance errors occur
    percentages = [1.0, 0.99, 0.98, 0.95, 0.93, 0.90]  # 100%, 99%, 98%, 95%, 93%, 90%
    
    for pct in percentages:
        trade_size = float(size) * pct
        print(f"   ℹ️  Attempting to close {trade_size:.6f} shares ({pct*100:.0f}% of {size})...")
        
        try:
            # Get best bid
            book_url = f"https://clob.polymarket.com/book?token_id={token_id}"
            book_response = requests.get(book_url, timeout=10)
            book_response.raise_for_status()
            book_data = book_response.json()

            bids = book_data.get("bids", [])
            if not bids:
                print("   ❌ No bids available to close position")
                if pct == percentages[-1]:  # Last attempt
                    return None
                continue  # Try next percentage

            # Get best bid
            best_bid_price = max(float(b['price']) for b in bids)

            print(f"   📉 Placing sell order: {trade_size:.4f} shares @ ${best_bid_price:.3f}...")

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
                # Check if it's a balance error
                if 'balance' in str(error_msg).lower() or 'allowance' in str(error_msg).lower():
                    print(f"   ⚠️  Balance error at {pct*100:.0f}%, trying lower amount...")
                    continue  # Try next percentage
                else:
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
            # Check if it's a balance/allowance error
            if 'balance' in error_str.lower() or 'allowance' in error_str.lower():
                print(f"   ⚠️  Balance error at {pct*100:.0f}%: {error_str}")
                if pct == percentages[-1]:  # Last attempt
                    print(f"   ❌ Failed to close even at {pct*100:.0f}%")
                    return {
                        'success': False,
                        'error': error_str,
                        'size': size,
                        'token_id': token_id,
                        'close_btc_price': current_btc_price
                    }
                continue  # Try next percentage
            else:
                # Non-balance error, return immediately
                print(f"   ❌ Error closing trade: {error_str}")
                return {
                    'success': False,
                    'error': error_str,
                    'size': size,
                    'token_id': token_id,
                    'close_btc_price': current_btc_price
                }
    
    # If we get here, all attempts failed
    return {
        'success': False,
        'error': 'All close attempts failed',
        'size': size,
        'token_id': token_id,
        'close_btc_price': current_btc_price
    }
