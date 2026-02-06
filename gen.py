import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PROXY_ADDRESS = os.getenv("PROXY_ADDRESS") 

print(f"🔑 Deriving keys for Proxy (Gnosis Safe): {PROXY_ADDRESS}")
print(f"✍️  Signing with Manager: {os.getenv('MY_ADDRESS')}")

try:
    # Initialize with signature_type=2 (Gnosis Safe)
    client = ClobClient(
        "https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=POLYGON,
        funder=PROXY_ADDRESS,
        signature_type=2 
    )

    # USE THIS METHOD INSTEAD:
    creds = client.create_or_derive_api_creds()
    
    print("\n✅ SUCCESS! Update your .env file with these EXACT values:\n")
    print(f'API_KEY="{creds.api_key}"')
    print(f'API_SECRET="{creds.api_secret}"')
    print(f'API_PASSPHRASE="{creds.api_passphrase}"')

except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nTroubleshooting:")
    print("1. Double check PROXY_ADDRESS is exactly what you see in Polymarket Settings.")
    print("2. Ensure PRIVATE_KEY is the one that controls the wallet connected to Polymarket.")