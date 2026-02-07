from flask import Flask, request, jsonify
import time, hmac, hashlib, requests, os, threading

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

    return 3

def round_qty(symbol, qty):
    return round(qty, get_qty_precision(symbol))

# ================= POSITION =================
def get_position(symbol, pos_side):
    r = api("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol})
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

# ================= TP / SL =================
def validate_tp_sl(side, entry, tp, sl):
    return (tp > entry and sl < entry) if side == "BUY" else (tp < entry and sl > entry)

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
            "closePosition": True,
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

    resp = api("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
    orders = resp.get("data", [])

    if isinstance(orders, dict):
        orders = orders.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    has_tp = False
    has_sl = False

    for o in orders:
        if not isinstance(o, dict):
            continue
        t = o.get("type", "")
        if "TAKE_PROFIT" in t:
            has_tp = True
        if "STOP" in t:
            has_sl = True

    if has_tp and has_sl:
        print("✅ FAILSAFE OK", flush=True)
        return

    print("❌ FAILSAFE FORCE CLOSE", flush=True)
    api("POST", "/openApi/swap/v2/trade/order", {
        "symbol": symbol,
        "side": "SELL" if side == "BUY" else "BUY",
        "type": "MARKET",
        "positionSide": pos_side,
        "closePosition": True
    })

# ================= MAIN =================
def execute_trade(symbol, side, usdt, tp, sl, leverage):
    lock = TRADE_LOCK.setdefault(symbol, threading.Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("Trade đang chạy")

    try:
        qty = round_qty(symbol, usdt / max(tp, sl))
        if qty <= 0:
            raise ValueError("Quantity = 0")

        set_leverage(symbol, side, leverage)
        place_market(symbol, side, qty)

        real_qty, entry = wait_position(symbol, side)
        if real_qty <= 0:
            raise RuntimeError("Không khớp lệnh")

        if not validate_tp_sl(side, entry, tp, sl):
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
        int(d.get("leverage", 100))
    )
    return jsonify({"status": "ok"})

@app.route("/")
def home():
    return "✅ BingX AutoTrade SAFE v2.1 running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
