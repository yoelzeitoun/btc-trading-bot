import requests
import re
from datetime import datetime, timedelta

url = 'https://polymarket.com/event/btc-updown-15m-1769904000'
headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

response = requests.get(url, headers=headers, timeout=10)
html = response.text

# Market starts at 1769904000
market_start = 1769904000
previous_window_end = market_start - 900  # The end time of the previous 15-min window

# Convert to ISO format for matching
dt = datetime.utcfromtimestamp(previous_window_end)
target_time = dt.strftime('%Y-%m-%dT%H:%M:%S')
print(f"Market start: {datetime.utcfromtimestamp(market_start)}")
print(f"Looking for closePrice from window ending at: {target_time}")

# Find ALL historical entries with timestamps
pattern = r'\{"startTime":"([^"]+)","endTime":"([^"]+)","openPrice":([\d.]+),"closePrice":([\d.]+),"outcome":"([^"]+)","percentChange":([^}]+)\}'
matches = re.findall(pattern, html)

print(f"\nFound {len(matches)} historical entries:")
for i, match in enumerate(matches):
    start_time, end_time, open_price, close_price, outcome, pct = match
    print(f"  {i+1}. Window ends: {end_time} -> Close: ${float(close_price):,.2f}")

# Find the one with endTime matching our target
print(f"\nSearching for endTime containing: {target_time}")
found = False
for match in matches:
    start_time, end_time, open_price, close_price, outcome, pct = match
    if target_time in end_time:
        print(f"✅ MATCH FOUND!")
        print(f"   Strike Price: ${float(close_price):,.2f}")
        found = True
        break

if not found:
    print(f"❌ Not found with exact match")
    print(f"\nLet me check all endTimes:")
    for match in matches:
        _, end_time, _, close_price, _, _ = match
        print(f"   {end_time} -> {close_price}")
