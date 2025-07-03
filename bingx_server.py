from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os
import sys

app = Flask(__name__)

# 🔐 Load API key from environment
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")

if not BINGX_API_KEY or not BINGX_API_SECRET:
    print("❌ Thiếu API KEY hoặc SECRET", file=sys.stderr)

# ✅ Generate signature
def generate_signature(params, secret):
    query_string = "&".join(f"{key}={params[key]}" for key in sorted(params))
    print("🔍 QUERY STRING:", query_string, flush=True)

    signature = hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    print("✅ SIGNATURE:", signature, flush=True)
    return signature

# ✅ Gửi lệnh entry MARKET
def place_bingx_order(symbol, side, price=None, qty=0.01, leverage=100, order_type="MARKET"):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "side": side.upper(),
        "quantity": f"{qty:.4f}".rstrip('0').rstrip('.'),
        "leverage": str(leverage),
        "timestamp": timestamp,
        "type": order_type.upper(),
        "positionSide": "LONG" if side.upper() == "BUY" else "SHORT"
    }

    if order_type.upper() == "LIMIT" and price:
        params["price"] = f"{price:.2f}".rstrip('0').rstrip('.')

    query_string = "&".join(f"{key}={params[key]}" for key in sorted(params))
    signature = generate_signature(params, BINGX_API_SECRET)
    full_url = f"{url}?{query_string}&signature={signature}"

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    print("📤 Sending ENTRY order to BingX:", full_url, flush=True)
    response = requests.post(full_url, headers=headers)
    print("📥 Phản hồi từ BingX (ENTRY):", response.text, flush=True)
    return response.json()

# ✅ Gửi TP và SL
def place_tp_sl_order(symbol, side_entry, qty, tp, sl):
    opposite_side = "SELL" if side_entry.upper() == "BUY" else "BUY"
    position_side = "LONG" if side_entry.upper() == "BUY" else "SHORT"

    timestamp = str(int(time.time() * 1000))
    results = []

    for label, price, order_type in [("TP", tp, "TAKE_PROFIT_MARKET"), ("SL", sl, "STOP_MARKET")]:
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "positionSide": position_side,
            "type": order_type,
            "stopPrice": str(price),
            "quantity": f"{qty:.4f}".rstrip('0').rstrip('.'),
            "timestamp": timestamp
        }

        query_string = "&".join(f"{key}={params[key]}" for key in sorted(params))
        signature = generate_signature(params, BINGX_API_SECRET)
        full_url = f"https://open-api.bingx.com/openApi/swap/v2/trade/order?{query_string}&signature={signature}"

        headers = {
            "X-BX-APIKEY": BINGX_API_KEY
        }

        print(f"📤 Sending {label} to BingX:", full_url, flush=True)
        response = requests.post(full_url, headers=headers)
        print(f"📥 Phản hồi từ BingX ({label}):", response.text, flush=True)
        results.append(response.json())

    return results

# ✅ Gộp lệnh entry + TP/SL
def execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage=100, order_type="MARKET"):
    entry_result = place_bingx_order(symbol, side, entry, qty, leverage, order_type)

    # Kiểm tra nếu cần đợi khớp
    status = entry_result.get("result", {}).get("data", {}).get("order", {}).get("status", "")
    if status != "FILLED":
        print("⏳ Lệnh chưa FILLED. Chờ 1.5s rồi gửi TP/SL...")
        time.sleep(1.5)

    tp_sl_result = place_tp_sl_order(symbol, side, qty, tp, sl)

    return {
        "entry": entry_result,
        "tp_sl": tp_sl_result
    }

# ✅ Route chính để nhận lệnh
@app.route('/api/bingx_order', methods=['POST'])
def handle_bingx_order():
    try:
        data = request.get_json()
        print("📥 Dữ liệu nhận:", data, flush=True)

        symbol = data.get("symbol", "BTC-USDT")
        side = data.get("side", "BUY")
        entry = float(data.get("entry", 0))
        qty = float(data.get("qty", 0.01))
        leverage = int(data.get("leverage", 100))
        tp = float(data.get("tp", 0))
        sl = float(data.get("sl", 0))
        order_type = data.get("order_type", "MARKET").upper()

        result = execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage, order_type)
        return jsonify({"status": "success", "result": result})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ✅ Route test
@app.route('/', methods=['GET'])
def home():
    return "✅ BingX AutoTrade Server is running."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
