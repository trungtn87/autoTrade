from flask import Flask, request, jsonify
import time, hmac, hashlib, requests, os, threading, math

app = Flask(__name__)

BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")
BASE_URL = "https://open-api.bingx.com"

SYMBOL_CACHE = {}
TRADE_LOCK = {}

# ================= UTIL =================
def now_ms():
    return int(time.time() * 1000)

def sign(params):
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(
        BINGX_API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()

def api(method, path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = now_ms()
    sig = sign(params)
    url = BASE_URL + path + "?" + "&".join(
        f"{k}={params[k]}" for k in sorted(params)
    ) + f"&signature={sig}"

    r = requests.request(
        method,
        url,
        headers={"X-BX-APIKEY": BINGX_API_KEY},
        timeout=10
    )
    return r.json()

# ================= SYMBOL INFO =================
def get_qty_precision(symbol):
    if symbol in SYMBOL_CACHE:
        return SYMBOL_CACHE[symbol]

    r = requests.get(
        f"{BASE_URL}/openApi/swap/v2/quote/contracts",
        timeout=10
    ).json()

    for s in r.get("data", []):
        if s["symbol"] == symbol:
            p = int(s["quantityPrecision"])
            SYMBOL_CACHE[symbol] = p
            return p

    return 3  # fallback

def round_qty(symbol, qty):
    p = get_qty_precision(symbol)
    return round(qty, p)

# ================= POSITION =================
def get_position(symbol, pos_side):
    r = api("GET", "/openApi/swap/v2/user/positions", {
        "symbol": symbol
    })

    for p in r.get("data", []):
        if p["positionSide"] == pos_side:
            amt = float(p["positionAmt"])
            if abs(amt) > 0:
                return abs(amt), float(p["avgPrice"])
    return 0, 0

def wait_position(symbol, side, timeout=20):
    pos_side = "LONG" if side == "BUY" else "SHORT"
    start = time.time()
    while time.time() - start < timeout:
        qty, price = get_position(symbol, pos_side)
        if qty > 0:
            return qty, price
        time.sleep(0.5)
    return 0, 0

# ================= LEVERAGE =================
def set_leverage(symbol, side, leverage):
    r = api("POST", "/openApi/swap/v2/trade/leverage", {
        "symbol": symbol,
        "side": "LONG" if side == "BUY" else "SHORT",
        "leverage": leverage
    })
    print("⚙️ LEVERAGE:", r, flush=True)

# ================= ENTRY =================
def place_market(symbol, side, qty):
    r = api("POST", "/openApi/swap/v2/trade/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
        "positionSide": "LONG" if side == "BUY" else "SHORT"
    })
    print("📥 ENTRY:", r, flush=True)
    return r

# ================= TP / SL =================
def validate_tp_sl(side, entry, tp, sl):
    if side == "BUY":
        return tp > entry and sl < entry
    return tp < entry and sl > entry

def place_tp_sl(symbol, side, tp, sl):
    pos_side = "LONG" if side == "BUY" else "SHORT"
    close_side = "SELL" if side == "BUY" else "BUY"

    for typ, price in [("TAKE_PROFIT_MARKET", tp), ("STOP_MARKET", sl)]:
        r = api("POST", "/openApi/swap/v2/trade/order", {
            "symbol": symbol,
            "side": close_side,
            "type": typ,
            "stopPrice": price,
            "positionSide": pos_side,
            "reduceOnly": True,
            "priceProtect": True
        })
        print(f"📤 {typ}:", r, flush=True)

# ================= FAILSAFE =================
def failsafe(symbol, side, wait=300):
    time.sleep(wait)
    pos_side = "LONG" if side == "BUY" else "SHORT"
    qty, _ = get_position(symbol, pos_side)

    if qty == 0:
        print("✅ FAILSAFE: no position", flush=True)
        return

    orders = api("GET", "/openApi/swap/v2/trade/openOrders", {
        "symbol": symbol
    }).get("data", [])

    has_tp = any("TAKE_PROFIT" in o.get("type", "") for o in orders)
    has_sl = any("STOP" in o.get("type", "") for o in orders)

    if has_tp and has_sl:
        print("✅ FAILSAFE OK", flush=True)
        return

    print("❌ FAILSAFE FORCE CLOSE", flush=True)
    api("POST", "/openApi/swap/v2/trade/order", {
        "symbol": symbol,
        "side": "SELL" if side == "BUY" else "BUY",
        "type": "MARKET",
        "positionSide": pos_side,
        "reduceOnly": True
    })

# ================= MAIN =================
def execute_trade(symbol, side, usdt, tp, sl, leverage):
    lock = TRADE_LOCK.setdefault(symbol, threading.Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("Trade đang chạy")

    try:
        raw_qty = usdt / max(tp, sl)
        qty = round_qty(symbol, raw_qty)
        if qty <= 0:
            raise ValueError("Quantity = 0")

        set_leverage(symbol, side, leverage)
        place_market(symbol, side, qty)

        real_qty, entry_price = wait_position(symbol, side)
        if real_qty <= 0:
            raise RuntimeError("Không khớp lệnh")

        if not validate_tp_sl(side, entry_price, tp, sl):
            raise ValueError("TP / SL sai theo giá khớp")

        place_tp_sl(symbol, side, tp, sl)

        threading.Thread(
            target=failsafe,
            args=(symbol, side),
            daemon=True
        ).start()

    finally:
        lock.release()

# ================= API =================
@app.route("/api/bingx_order", methods=["POST"])
def handle():
    d = request.get_json()
    execute_trade(
        d["symbol"],
        d["side"].upper(),
        float(d["usdt_amount"]),
        float(d["tp"]),
        float(d["sl"]),
        int(d.get("leverage", 50))
    )
    return jsonify({"status": "ok"})

@app.route("/")
def home():
    return "✅ BingX AutoTrade SAFE v2 running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
