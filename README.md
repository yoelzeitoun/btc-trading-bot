# 🚀 BTC Double Barrier Mean Reversion Bot

A sophisticated trading bot for Polymarket's BTC 15-minute binary options using the **Double Barrier Mean Reversion Strategy**.

## 🎯 Strategy Overview

The bot validates **4 strict conditions** before generating a trade signal:

1. **Statistical Barrier** (Bollinger Bands)
   - Target price must be outside 2.0 StdDev bands
   
2. **Kinetic Barrier** (ATR Projection)
   - Distance to target > (ATR × minutes_left × 1.5)
   
3. **Physical Barrier** (Order Book Depth)
   - Support/Resistance volume > 1.5x opposing volume
   
4. **Risk/Reward Filter**
   - Share price between $0.60-$0.85 (17%+ ROI)

## 🏃 Quick Start

```bash
python3 btc-trade.py
```

The bot will:
- ✅ Auto-detect the current active BTC 15m market
- ✅ Fetch the current BTC price as strike
- ✅ Monitor the 3-5 minute execution window
- ✅ Display all 4 barrier conditions
- ✅ Generate trade signal if all conditions pass
- ✅ Show WIN/LOSS at expiration

## 📊 Market Selection

The bot automatically finds the most recent BTC 15m market from Polymarket's Gamma API. Markets change every 15 minutes with new windows:

```
https://polymarket.com/event/btc-updown-15m-1769985000
https://polymarket.com/event/btc-updown-15m-1769984100  (15 min before)
https://polymarket.com/event/btc-updown-15m-1769983200  (30 min before)
```

## ⚙️ Setup

1. Install dependencies:
```bash
pip install python-binance py-clob-client requests numpy
```

2. Create `.env` file with your Polymarket credentials:
```
API_KEY=your_api_key
API_SECRET=your_api_secret
API_PASSPHRASE=your_passphrase
PRIVATE_KEY=0xyour_private_key
MY_ADDRESS=0xyour_wallet_address
```

3. Run:
```bash
python3 btc-trade.py
```

## 📝 Output Example

```
🚀 DOUBLE BARRIER MEAN REVERSION BOT
============================================================

🔍 Auto-detecting current BTC 15m market...
   Fetching active markets from Gamma API...
   Found 100 active events
   ✅ Found: btc-updown-15m-1769985000

✅ MARKET LOADED:
   Title: BITCOIN UP OR DOWN - FEBRUARY 1, 5:30PM-5:45PM ET
   URL: https://polymarket.com/event/btc-updown-15m-1769985000
   ⏰ Time Remaining: 14.2 minutes
   🎯 Strike Price (current BTC): $78,063.85

🚀 DOUBLE BARRIER MEAN REVERSION MONITORING ACTIVE
📊 Target Price: $78,063.85
⏰ Expiration in 14.2 minutes
🎯 Strategy: Statistical + Kinetic + Physical + R/R Barriers

🔔 ENTERING 5-MINUTE WINDOW (Time Left: 5.15min)
   Starting condition monitoring...

⏱️  [T-3.25min] Evaluating Trade Conditions...
   Current BTC: $78,100.50 | Target: $78,063.85

   [A] BOLLINGER BANDS (Period=20, StdDev=2.0)
       Upper: $78,150.00 | Middle: $78,080.00 | Lower: $78,010.00
       Direction: DOWN | Target vs Upper Band: $78,063.85 > $78,150.00
       Result: ✅ PASS

   [B] ATR KINETIC BARRIER (Period=14)
       ATR: $45.50
       Max Possible Move: $204.75 (ATR × 3.2min × 1.5)
       Actual Distance: $36.65
       Result: ❌ FAIL (Distance <= Max Move)

   [C] ORDER BOOK DEPTH BARRIER
       Direction: DOWN
       ASK Volume (Resistance): 125.45 BTC
       BID Volume (Threat): 85.30 BTC
       Ratio: 1.47x (Need >= 1.5x)
       Result: ❌ FAIL

   [D] RISK/REWARD FILTER
       Share Type: NO
       Share Price: $0.68 (68¢)
       Valid Range: $0.60 - $0.85
       Result: ✅ PASS

❌ CONDITIONS NOT MET [A:True B:False C:False D:True]
   No trade signal. Continuing monitoring...
```

## 🔄 Trade Flow

1. **Market Auto-Detection** → Current 15m window
2. **Time Window Check** → Only trades 3-5 min before expiration
3. **4-Barrier Validation** → All must pass for signal
4. **Signal Generation** → Shows BUY recommendation (not automatic)
5. **Expiration** → Reports WIN/LOSS

## ⚠️ Important Notes

- **No auto-trading** - Bot shows signals only, you decide to trade
- **15-minute windows** - Markets expire every 15 minutes
- **Execution window** - Only evaluates trades 3-5 minutes before expiration
- **Manual entry** - User must place trades on Polymarket website
- **Paper trading** - Start with small positions to test

## 📈 Performance Tracking

The bot provides detailed logs of:
- All 4 barrier condition checks
- Current vs target price levels
- Real-time BTC data from Binance
- Order book analysis
- Risk/reward calculations

## 🛠️ Troubleshooting

**Q: "No active BTC 15m markets found"**
- Check if markets are available at https://polymarket.com/crypto/15M
- Markets may have just expired, wait for next 15-min window

**Q: "Could not read input" error**
- Run bot interactively: `python3 btc-trade.py`
- Don't pipe input with `echo`

**Q: No trade signal after 5 minutes**
- This is normal! The strategy is selective
- Conditions are deliberately strict for profitability
- Less frequent trades = higher quality

## 📊 API Sources

- **Polymarket Gamma API**: https://gamma-api.polymarket.com
- **Binance Spot API**: Live BTC prices and orderbook
- **CLOB Client**: Polymarket order handling

---

**Happy trading! Remember: Keep position sizes small and risk management first.** 🎯
