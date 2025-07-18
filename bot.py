import discord
import requests
from datetime import datetime, timedelta, timezone
import json

DISCORD_TOKEN = ""
HEADERS = {
    "Authorization": "Bearer "
}
GROQ_API_KEY = ""
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

MIN_PRICE = 0.0001
MIN_GAIN = 0.5

def is_under_1week(coin):
    created_at = datetime.fromisoformat(coin["createdAt"].replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - created_at < timedelta(days=7)

def has_min_price(coin, min_price=MIN_PRICE):
    return coin.get("currentPrice", 0) >= min_price

def has_strong_1h_increase(symbol, min_gain=MIN_GAIN):
    try:
        url = f"https://rugplay.com/api/v1/coin/{symbol}?timeframe=1h"
        res = requests.get(url, headers=HEADERS)
        data = res.json()
        candles = data.get("candlestickData", [])
        if not candles:
            print(f"{symbol}: No candlestick data")
            return False
        open_price = candles[0]["open"]
        close_price = candles[-1]["close"]
        low_price = min(c["low"] for c in candles)
        gain = (close_price - open_price) / open_price
        meets_criteria = low_price >= open_price and gain >= min_gain
        if not meets_criteria:
            print(f"{symbol}: 1h gain check failed (gain={gain:.2f}, low_price={low_price}, open_price={open_price})")
        return meets_criteria
    except Exception as e:
        print(f"Error checking 1h trend for {symbol}: {e}")
        return False

def get_holders_data(symbol):
    try:
        url = f"https://rugplay.com/api/v1/holders/{symbol}?limit=50"
        res = requests.get(url, headers=HEADERS)
        data = res.json()
        return data
    except Exception as e:
        print(f"Error fetching holders for {symbol}: {e}")
        return None

def passes_holder_filters(symbol):
    data = get_holders_data(symbol)
    if not data:
        print(f"No holder data for {symbol}, including by default")
        return True

    total_holders = data.get("totalHolders", 0)
    if total_holders < 5:
        print(f"{symbol} rejected: only {total_holders} holders (<5)")
        return False

    holders = data.get("holders", [])
    top_holder_pct = holders[0].get("percentage", 100) if holders else 100
    if top_holder_pct > 80:
        print(f"{symbol} rejected: top holder holds {top_holder_pct:.2f}% (>80%)")
        return False

    print(f"{symbol} passed holder filters: {total_holders} holders, top holder {top_holder_pct:.2f}%")
    return True

def get_candidate_coins(min_price=MIN_PRICE, min_gain=MIN_GAIN, limit=100):
    url = f"https://rugplay.com/api/v1/market?limit={limit}&sortBy=createdAt&sortOrder=desc"
    res = requests.get(url, headers=HEADERS).json()
    coins = res.get("coins", [])
    filtered = []
    print(f"Fetched {len(coins)} coins from market.")
    for coin in coins:
        symbol = coin["symbol"]

        if not is_under_1week(coin):
            print(f"Skipping {symbol}: Older than 7 days")
            continue

        if not has_min_price(coin, min_price):
            print(f"Skipping {symbol}: Price {coin.get('currentPrice')} < min_price {min_price}")
            continue

        if not has_strong_1h_increase(symbol, min_gain):
            print(f"Skipping {symbol}: Does not meet 1h gain criteria {min_gain}")
            continue

        if not passes_holder_filters(symbol):
            print(f"Skipping {symbol}: Holder filters failed")
            continue

        print(f"Adding {symbol} to candidates")
        filtered.append(coin)
    print(f"Total candidates after filtering: {len(filtered)}")
    return filtered

def prepare_ai_payload(coins):
    payload = []
    for coin in coins:
        symbol = coin["symbol"]
        try:
            coin_data = requests.get(f"https://rugplay.com/api/v1/coin/{symbol}?timeframe=1h", headers=HEADERS).json()
            holders_data = get_holders_data(symbol)
            holders = holders_data.get("holders", []) if holders_data else []
            quantities = [h.get("quantity") for h in holders]
            quantity_counts = {}
            for q in quantities:
                quantity_counts[q] = quantity_counts.get(q, 0) + 1

            payload.append({
                "symbol": symbol,
                "name": coin["name"],
                "currentPrice": coin["currentPrice"],
                "marketCap": coin.get("marketCap"),
                "priceHistory": coin_data.get("candlestickData", []),
                "totalHolders": holders_data.get("totalHolders") if holders_data else None,
                "topHolderPercentage": holders[0].get("percentage") if holders else None,
                "holdersQuantityDistribution": quantity_counts
            })
        except Exception as e:
            print(f"Failed to fetch data for {symbol}: {e}")
    return payload

def send_to_groq_ai(payload):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [
        {
            "role": "system",
            "content": (
                "You are a crypto analyst. Given a list of coins with price history, metadata, and holder stats, "
                "return ONLY a JSON object with a key called 'rankedCoins'. This should be a list of coin objects, "
                "each containing exactly these fields: symbol (string), name (string), and investmentPotential (number). "
                "Do NOT include any other fields, price history, or detailed holder info. "
                "Return no extra text or explanation."
            )
        },
        {
            "role": "user",
            "content": f"Rank these coins:\n{payload}"
        }
    ]

    try:
        res = requests.post(GROQ_API_URL, json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "temperature": 0.3
        }, headers=headers)

        result = res.json()
        print("AI response:", result)

        content = result["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            print(f"Parsed AI response JSON successfully.")
            return parsed
        except json.JSONDecodeError:
            print("JSON decode error, returning raw content")
            return {"raw_response": content}
    except Exception as e:
        print(f"Error contacting Groq AI: {e}")
        return None

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Bot connected as {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.lower() == "!help":
        help_text = (
            "**Bot Commands:**\n"
            "`!scan` — Scan Rugplay for strong coins and rank them using AI\n"
            "`!help` — Show this help message\n\n"
            "Optional parameters:\n"
            "- minprice: minimum price (e.g. 0.0002)\n"
            "- mingain: minimum 1-hour % gain (e.g. 0.5 for 50%)\n"
            "- numscans: how many coins to analyze (default 100, max 100)\n"
            "Example: !scan minprice=0.001 mingain=0.3 numscans=75"
        )
        await message.channel.send(help_text)
        return

    if message.content.lower().startswith("!scan"):
        min_price = MIN_PRICE
        min_gain = MIN_GAIN
        num_scans = 100

        parts = message.content.split()
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.lower()
                try:
                    if key == "numscans":
                        val = int(value)
                    else:
                        val = float(value)
                except ValueError:
                    await message.channel.send(f"Invalid value for {key}: {value}. Please provide a valid number.")
                    return
                if key == "minprice":
                    min_price = val
                elif key == "mingain":
                    min_gain = val
                elif key == "numscans":
                    num_scans = val
                else:
                    await message.channel.send(f"Unknown parameter: {key}. Supported parameters are minprice, mingain, numscans.")
                    return

        await message.channel.send(f"scanning Rugplay with teh following daata:  minprice={min_price}, mingain={min_gain}, numscans={num_scans}, please stand by")

        candidates = get_candidate_coins(min_price=min_price, min_gain=min_gain, limit=num_scans)
        if not candidates:
            await message.channel.send("No coins met the criteria.")
            return

        ai_payload = prepare_ai_payload(candidates)
        ai_results = send_to_groq_ai(ai_payload)

        if not ai_results:
            await message.channel.send("No response from AI or an error occurred.")
            return

        if not isinstance(ai_results, dict) or "rankedCoins" not in ai_results:
            raw_resp = ai_results.get("raw_response") if isinstance(ai_results, dict) else str(ai_results)
            await message.channel.send(f"Unexpected AI response format. Raw response:\n{raw_resp}")
            return

        response_lines = ["**AI-Ranked Coins:**"]
        for i, coin in enumerate(ai_results["rankedCoins"], 1):
            fields = [f"`{coin.get('symbol', 'N/A')}` - {coin.get('name', 'N/A')}"]
            for key, val in coin.items():
                if key in ('symbol', 'name'):
                    continue
                if isinstance(val, float):
                    val_str = f"{val:.6g}"
                else:
                    val_str = str(val)
                fields.append(f"{key}: {val_str}")
            line = f"{i}. " + " | ".join(fields)
            response_lines.append(line)

        await message.channel.send("\n".join(response_lines[:10]))

client.run(DISCORD_TOKEN)
