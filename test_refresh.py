import requests
import re

slug = "btc-updown-15m-1769909400"
headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

url = f"https://polymarket.com/event/{slug}"
response = requests.get(url, headers=headers, timeout=5)

# Try different patterns to find the price
patterns = [
    r'"outcomePrices"\s*:\s*\[([^\]]+)\]',
    r'"bestBid":\s*"([0-9.]+)".*?"bestAsk":\s*"([0-9.]+)"',
    r'>(\d+)¢</span>',
]

print(f"Testing URL: {url}\n")

for i, pattern in enumerate(patterns, 1):
    matches = re.findall(pattern, response.text)
    print(f"Pattern {i}: {pattern}")
    if matches:
        print(f"  ✅ Found: {matches[:3]}")
    else:
        print(f"  ❌ Not found")
    print()

# Check if outcomePrices appears anywhere
if '"outcomePrices"' in response.text:
    idx = response.text.find('"outcomePrices"')
    context = response.text[idx:idx+200]
    print(f"outcomePrices context:\n{context}\n")
else:
    print("❌ outcomePrices not found in page\n")

# Look for any price-like numbers
price_pattern = r'[0-9]+¢|[0-9]+%|0\.[0-9]+'
prices = re.findall(price_pattern, response.text)
print(f"All prices found: {set(prices)}")
