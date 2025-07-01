import time
import requests
from datetime import datetime, timezone
from itertools import cycle

held_symbols_input = input("Enter the coin symbols you are holding (comma separated): ").strip()
HELD_SYMBOLS = [sym.strip().upper() for sym in held_symbols_input.split(",") if sym.strip()]

api_keys_input = input("Enter your Rugplay API keys (comma separated): ").strip()
API_KEYS = [key.strip() for key in api_keys_input.split(",") if key.strip()]
if not API_KEYS:
    raise ValueError("At least one API key is required.")

ntfy_topic = input("Enter your ntfy.sh topic (e.g. rugplay-alerts): ").strip()

URL_MARKET = "https://rugplay.com/api/v1/market"
URL_HOLDERS = "https://rugplay.com/api/v1/holders/{}"

LIMIT = 100
MAX_PAGES = 3
REQUEST_DELAY = 24

MIN_PERCENT_GAIN = 75
MAX_AGE_HOURS = 24
MIN_PRICE = 0.005
MIN_HOLDERS = 20

#cycles through api keys to bypass the 2k/day limit
api_key_cycle = cycle(API_KEYS)

def is_daytime():
    now_hour = datetime.now().hour
    return 8 <= now_hour < 20  # only when i'm awake and willing to trade, useless to run at night if i can't do anything about it. you can modify this as needed.

def get_headers():
    api_key = next(api_key_cycle)
    return {"Authorization": f"Bearer {api_key}"}

def has_valid_holders(symbol):
    try:
        headers = get_headers()
        res = requests.get(URL_HOLDERS.format(symbol), headers=headers)
        res.raise_for_status()
        data = res.json()

        total_holders = data.get("totalHolders", 0)
        if total_holders < MIN_HOLDERS:
            return False

        holders = data.get("holders", [])
        if len(holders) < MIN_HOLDERS:
            return False

        quantities = [h["quantity"] for h in holders]

        # if all quantities are equal, the owner probably just gave a bunch of people coins to boost the coins popularity with these sort of programs
        if len(set(quantities)) == 1:
            return False

        return True

    except Exception as e:
        print(f"Error fetching holders for {symbol}: {e}")
        return False

def check_held_coins():
    for symbol in HELD_SYMBOLS:
        try:
            headers = get_headers()
            res = requests.get(URL_HOLDERS.format(symbol), headers=headers)
            res.raise_for_status()
            data = res.json()
            holders = data.get("holders", [])
            if len(holders) < 2:
                continue
            top_holder = holders[0]
            second_holder = holders[1]
            top_pct = top_holder["percentage"]
            second_pct = second_holder["percentage"]
            if top_pct - second_pct > 50:
                continue
            # Alert user: owner has dumped a large portion, possibly rugging coin rn!
            message = (
                f"URGENT: {symbol} may be crashing!\n"
                f"Top holder's share dropped to {top_pct:.2f}%.\n"
                f"2nd holder has {second_pct:.2f}%.\n"
            )
            requests.post(f"https://ntfy.sh/{ntfy_topic}", data=message)
            print("Crash alert for held coin:", symbol)
        except Exception as e:
            print(f"Error checking held coin {symbol}: {e}")

def check_coins_page(page):
    headers = get_headers()
    params = {
        "sortBy": "createdAt",
        "sortOrder": "desc",
        "limit": LIMIT,
        "page": page,
    }
    res = requests.get(URL_MARKET, headers=headers, params=params)
    res.raise_for_status()
    return res.json()

def main_loop():
    while True:
        if is_daytime():
            print(f"Reading market at {datetime.now().isoformat()}")

            check_held_coins()

            for page in range(1, MAX_PAGES + 1):
                try:
                    data = check_coins_page(page)
                except Exception as e:
                    print(f"Error reading market page {page}: {e}")
                    break

                now = datetime.now(timezone.utc)
                found = False

                for coin in data.get("coins", []):
                    try:
                        created_at = datetime.fromisoformat(coin["createdAt"].replace("Z", "+00:00"))
                        age_hours = (now - created_at).total_seconds() / 3600
                        price = coin["currentPrice"]
                        change = coin["change24h"]

                        if age_hours <= MAX_AGE_HOURS and change >= MIN_PERCENT_GAIN and price >= MIN_PRICE:
                            if has_valid_holders(coin["symbol"]):
                                found = True
                                message = (
                                    f"{coin['symbol']} ({coin['name']}) 🚀\n"
                                    f"Price: ${price:.4f}\n"
                                    f"Change 24h: {change:.2f}%\n"
                                    f"Age: {age_hours:.1f} hours\n"
                                    f"Holders: {MIN_HOLDERS}+ with diverse investments"
                                )
                                requests.post(f"https://ntfy.sh/{ntfy_topic}", data=message)
                                print("Sent alert:", coin["symbol"])
                    except Exception as e:
                        print(f"Error processing coin {coin.get('symbol', '?')}: {e}")

                if not found:
                    print(f"No good coins found on page {page}.")

                time.sleep(REQUEST_DELAY)

        else:
            print(f"Outside daytime hours at {datetime.now().isoformat()}, sleeping 1 hour.")
            time.sleep(3600)

if __name__ == "__main__":
    main_loop()