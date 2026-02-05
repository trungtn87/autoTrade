from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os
import sys
import threading

app = Flask(__name__)

# ===== CONFIG =====
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")

GLOBAL_TP_CACHE = {}
GLOBAL_SL_CACHE = {}

if not BINGX_API_KEY or not BINGX_API_SECRET:
    print("❌ Thiếu API KEY hoặc SECRET", file=sys.stderr)

# ===== SIGNATURE =====
def generate_signature(params, secret):
    query_string = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

# ===== ENTRY ORDER =====
def place_bingx_order(symbol, side, price, qty, leverage, order_type):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "side": side.upper(),
        "type": order_type,
        "quantity": f"{qty:.4f}".rstrip('0').rstrip('.'),
        "leverage": leverage,
        "positionSide": "LONG" if side.upper() == "BUY" else "SHORT",
        "timestamp": timestamp
    }

    if order_type == "LIMIT" and price > 0:
        params["price"] = f"{price:.2f}".rstrip('0').rstrip('.')

    signature = generate_signature(params, BINGX_API_SECRET)
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    full_url = f"{url}?{query}&signature={signature}"

    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = requests.post(full_url, headers=headers, timeout=10)
    print("📥 ENTRY:", r.text, flush=True)
    return r.json()

# ===== GET POSITION =====
def get_bingx_position(symbol, position_side):
    url = "https://open-api.bingx.com/openApi/swap/v2/user/positions"
    timestamp = str(int(time.time() * 1000))

    params = {"symbol": symbol, "timestamp": timestamp}
    signature = generate_signature(params, BINGX_API_SECRET)
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    full_url = f"{url}?{query}&signature={signature}"

    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = requests.get(full_url, headers=headers, timeout=10).json()

    for p in r.get("data", []):
        if p.get("positionSide") == position_side and float(p.get("positionAmt", 0)) != 0:
            return {"exists": True}

    return {"exists": False}

# ===== OPEN ORDERS =====
def get_open_orders(symbol):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/openOrders"
    timestamp = str(int(time.time() * 1000))

    params = {"symbol": symbol, "timestamp": timestamp}
    signature = generate_signature(params, BINGX_API_SECRET)
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    full_url = f"{url}?{query}&signature={signature}"

    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = requests.get(full_url, headers=headers, timeout=10)
    return r.json().get("data", [])

def check_tp_sl_open_orders(symbol, position_side):
    orders = get_open_orders(symbol)
    has_tp = has_sl = False

    for o in orders:
        if o.get("positionSide") != position_side:
            continue
        if o.get("type") == "TAKE_PROFIT_MARKET":
            has_tp = True
        if o.get("type") == "STOP_MARKET":
            has_sl = True

    print(f"🧪 TP:{has_tp} | SL:{has_sl}", flush=True)
    return has_tp, has_sl

# ===== TP / SL =====
def place_tp_sl_order(symbol, side_entry, tp, sl):
    opposite = "SELL" if side_entry.upper() == "BUY" else "BUY"
    position_side = "LONG" if side_entry.upper() == "BUY" else "SHORT"
    timestamp = str(int(time.time() * 1000))

    for label, price, t in [
        ("TP", tp, "TAKE_PROFIT_MARKET"),
        ("SL", sl, "STOP_MARKET")
    ]:
        if price <= 0:
            continue

        params = {
            "symbol": symbol,
            "side": opposite,
            "positionSide": position_side,
            "type": t,
            "stopPrice": price,
            "timestamp": timestamp
        }

        signature = generate_signature(params, BINGX_API_SECRET)
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        full_url = f"https://open-api.bingx.com/openApi/swap/v2/trade/order?{query}&signature={signature}"

        headers = {"X-BX-APIKEY": BINGX_API_KEY}
        r = requests.post(full_url, headers=headers, timeout=10)
        print(f"📥 {label}:", r.text, flush=True)

# ===== FAILSAFE =====
def failsafe_watch(symbol, side):
    time.sleep(300)
    position_side = "LONG" if side.upper() == "BUY" else "SHORT"

    has_tp, has_sl = check_tp_sl_open_orders(symbol, position_side)
    if has_tp and has_sl:
        print("✅ FAILSAFE OK", flush=True)
        return

    print("⚠️ FAILSAFE RETRY TP/SL", flush=True)
    place_tp_sl_order(
        symbol,
        side,
        GLOBAL_TP_CACHE.get(symbol, 0),
        GLOBAL_SL_CACHE.get(symbol, 0)
    )

# ===== MAIN EXECUTION =====
def execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage, order_type):
    entry_res = place_bingx_order(symbol, side, entry, qty, leverage, order_type)

    position_side = "LONG" if side.upper() == "BUY" else "SHORT"

    # wait for position
    start = time.time()
    while time.time() - start < 8:
        if get_bingx_position(symbol, position_side)["exists"]:
            break
        time.sleep(0.5)

    has_tp, has_sl = check_tp_sl_open_orders(symbol, position_side)

    if not (has_tp and has_sl):
        GLOBAL_TP_CACHE[symbol] = tp
        GLOBAL_SL_CACHE[symbol] = sl
        place_tp_sl_order(symbol, side, tp, sl)

    threading.Thread(
        target=failsafe_watch,
        args=(symbol, side),
        daemon=True
    ).start()

    return entry_res

# ===== API =====
@app.route("/api/bingx_order", methods=["POST"])
def handle_order():
    try:
        d = request.get_json()

        symbol = d.get("symbol", "BTC-USDT")
        side = d.get("side", "BUY")
        entry = float(d.get("entry", 0))
        tp = float(d.get("tp", 0))
        sl = float(d.get("sl", 0))
        leverage = int(d.get("leverage", 100))
        order_type = d.get("order_type", "MARKET").upper()

        usdt = float(d.get("usdt_amount", 50))
        qty = round(usdt / entry, 4)

        res = execute_alert_trade(
            symbol, side, entry, qty, tp, sl, leverage, order_type
        )

        return jsonify({"status": "ok", "result": res})

    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route("/")
def home():
    return "✅ BingX AutoTrade Server running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
