from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os
import sys

app = Flask(__name__)

# 🔐 Đọc API key từ biến môi trường
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")

if not BINGX_API_KEY or not BINGX_API_SECRET:
    print("❌ Thiếu API KEY hoặc SECRET", file=sys.stderr)

# 🛠️ Tạo chữ ký theo chuẩn BingX
def generate_signature(params, secret):
    if not secret:
        raise ValueError("❌ BINGX_API_SECRET is None hoặc rỗng")

    sorted_params = sorted((k, str(v)) for k, v in params.items())
    query_string = "&".join(f"{k}={v}" for k, v in sorted_params)

    print("🔍 QUERY STRING:", query_string, flush=True)

    signature = hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    print("✅ SIGNATURE:", signature, flush=True)
    return signature

# 🔁 Gửi lệnh thực tế
def place_bingx_order(symbol, side, price=None, qty=0.01, leverage=100, order_type="LIMIT"):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "side": side.upper(),
        "volume": f"{qty:.4f}".rstrip('0').rstrip('.'),
        "leverage": str(leverage),
        "timestamp": timestamp,
        "type": order_type.upper()  # "LIMIT" hoặc "MARKET"
    }

    if order_type.upper() == "LIMIT" and price:
        params["price"] = f"{price:.2f}".rstrip('0').rstrip('.')

    signature = generate_signature(params, BINGX_API_SECRET)
    params["signature"] = signature

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    response = requests.post(url, headers=headers, data=params)
    return response.json()

# ✅ Nhận yêu cầu từ Google Script
@app.route('/api/bingx_order', methods=['POST'])
def handle_bingx_order():
    try:
        data = request.get_json()

        symbol = data.get("symbol", "BTCUSDT").replace("USDT", "-USDT")  # chuyển BTCUSDT → BTC-USDT
        side = data.get("side", "BUY")
        entry = float(data.get("entry", 0))
        qty = float(data.get("qty", 0.01))
        leverage = int(data.get("leverage", 100))
        order_type = data.get("order_type", "LIMIT")

        result = place_bingx_order(symbol, side, entry, qty, leverage, order_type)
        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def test():
    return "✅ BingX AutoTrade Server đang chạy."

# ✅ Khởi động
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
