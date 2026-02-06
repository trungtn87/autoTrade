from flask import Flask, request, jsonify
import time, hmac, hashlib, requests, os, sys, threading

app = Flask(__name__)

BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")

GLOBAL_TP_CACHE = {}
GLOBAL_SL_CACHE = {}

# ================= SIGNATURE =================
def generate_signature(params, secret):
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

# ================= ENTRY =================
def place_bingx_order(symbol, side, price, qty, leverage, order_type):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    ts = int(time.time() * 1000)

    params = {
        "symbol": symbol,
        "side": side.upper(),
        "type": order_type,
        "quantity": f"{qty:.4f}".rstrip("0").rstrip("."),
        "leverage": leverage,
        "positionSide": "LONG" if side.upper() == "BUY" else "SHORT",
        "timestamp": ts
    }

    if order_type == "LIMIT" and price > 0:
        params["price"] = f"{price:.2f}"

    sig = generate_signature(params, BINGX_API_SECRET)
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    r = requests.post(f"{url}?{query}&signature={sig}",
                      headers={"X-BX-APIKEY": BINGX_API_KEY},
                      timeout=10)

    print("📥 ENTRY:", r.text, flush=True)
    return r.json()

# ================= POSITION =================
def get_bingx_position(symbol, position_side):
    url = "https://open-api.bingx.com/openApi/swap/v2/user/positions"
    ts = int(time.time() * 1000)

    params = {"symbol": symbol, "timestamp": ts}
    sig = generate_signature(params, BINGX_API_SECRET)
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))

    r = requests.get(f"{url}?{query}&signature={sig}",
                     headers={"X-BX-APIKEY": BINGX_API_KEY},
                     timeout=10).json()

    for p in r.get("data", []):
        try:
            amt = float(p.get("positionAmt", 0))
        except:
            amt = 0

        if p.get("positionSide") == position_side and amt != 0:
            return {"exists": True, "qty": abs(amt)}

    return {"exists": False, "qty": 0}

def wait_for_position_amt(symbol, position_side, timeout=12):
    start = time.time()
    while time.time() - start < timeout:
        pos = get_bingx_position(symbol, position_side)
        if pos["exists"] and pos["qty"] > 0:
            print(f"✅ Position synced qty={pos['qty']}", flush=True)
            return pos["qty"]
        time.sleep(0.5)
    return None

# ================= TP / SL =================
def place_tp_sl_order(symbol, side_entry, qty, tp, sl):
    opposite = "SELL" if side_entry.upper() == "BUY" else "BUY"
    position_side = "LONG" if side_entry.upper() == "BUY" else "SHORT"
    ts = int(time.time() * 1000)

    for label, price, typ in [
        ("TP", tp, "TAKE_PROFIT_MARKET"),
        ("SL", sl, "STOP_MARKET")
    ]:
        if price <= 0:
            continue

        params = {
            "symbol": symbol,
            "side": opposite,
            "positionSide": position_side,
            "type": typ,
            "stopPrice": price,
            "quantity": f"{qty:.4f}".rstrip("0").rstrip("."),
            "timestamp": ts
        }

        sig = generate_signature(params, BINGX_API_SECRET)
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        r = requests.post(
            f"https://open-api.bingx.com/openApi/swap/v2/trade/order?{query}&signature={sig}",
            headers={"X-BX-APIKEY": BINGX_API_KEY},
            timeout=10
        )
        print(f"📥 {label}:", r.text, flush=True)

# ================= FAILSAFE =================
def failsafe_watch(symbol, side):
    time.sleep(300)
    position_side = "LONG" if side.upper() == "BUY" else "SHORT"

    orders = get_open_orders(symbol)
    has_tp = has_sl = False
    for o in orders:
        if o.get("positionSide") != position_side:
            continue
        if o.get("type") == "TAKE_PROFIT_MARKET":
            has_tp = True
        if o.get("type") == "STOP_MARKET":
            has_sl = True

    if has_tp and has_sl:
        print("✅ FAILSAFE OK", flush=True)
        return

    print("⚠️ FAILSAFE RETRY TP/SL", flush=True)
    pos = get_bingx_position(symbol, position_side)
    if pos["exists"]:
        place_tp_sl_order(
            symbol, side, pos["qty"],
            GLOBAL_TP_CACHE.get(symbol, 0),
            GLOBAL_SL_CACHE.get(symbol, 0)
        )

# ================= MAIN =================
def execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage, order_type):
    place_bingx_order(symbol, side, entry, qty, leverage, order_type)

    position_side = "LONG" if side.upper() == "BUY" else "SHORT"
    real_qty = wait_for_position_amt(symbol, position_side) or qty

    GLOBAL_TP_CACHE[symbol] = tp
    GLOBAL_SL_CACHE[symbol] = sl

    place_tp_sl_order(symbol, side, real_qty, tp, sl)

    threading.Thread(target=failsafe_watch, args=(symbol, side), daemon=True).start()

# ================= API =================
@app.route("/api/bingx_order", methods=["POST"])
def handle_order():
    try:
        d = request.get_json()
        symbol = d["symbol"]
        side = d["side"]
        entry = float(d["entry"])
        tp = float(d["tp"])
        sl = float(d["sl"])
        leverage = int(d.get("leverage", 100))
        usdt = float(d.get("usdt_amount", 50))
        qty = round(usdt / entry, 4)

        execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage, "MARKET")
        return jsonify({"status": "ok"})

    except Exception as e:
        print("🔥 SERVER ERROR:", e, flush=True)
        return jsonify({"status": "error", "message": str(e)}), 200

@app.route("/")
def home():
    return "✅ BingX AutoTrade Server running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
