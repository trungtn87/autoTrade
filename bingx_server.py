from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os

app = Flask(__name__)

# 🔐 Đọc API key từ biến môi trường Render
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")

# 🧪 Kiểm tra API key/secret có được load chưa
print("DEBUG - BINGX_API_KEY:", "Loaded" if BINGX_API_KEY else "❌ MISSING")
print("DEBUG - BINGX_API_SECRET:", "Loaded" if BINGX_API_SECRET else "❌ MISSING")

# 🛠️ Hàm ký dữ liệu theo chuẩn BingX
def generate_signature(params, secret):
    if secret is None:
        raise ValueError("❌ BINGX_API_SECRET is None – kiểm tra biến môi trường trên Render.")
    query_string = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

# 🔁 Hàm gửi lệnh thực tế qua BingX
def place_bingx_order(symbol, side, price, qty=0.01, leverage=100):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "side": side.upper(),
        "price": str(price),
        "volume": str(qty),
        "leverage": str(leverage),
        "timestamp": timestamp
    }

    # Tạo chữ ký
    signature = generate_signature(params, BINGX_API_SECRET)
    params["signature"] = signature

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    response = requests.post(url, headers=headers, data=params)
    return response.json()

# ✅ API nhận dữ liệu từ Google Script
@app.route('/api/bingx_order', methods=['POST'])
def handle_bingx_order():
    try:
        data = request.get_json()

        symbol = data.get("symbol", "BTC-USDT")
        side = data.get("side", "BUY")
        entry = float(data.get("entry", 0))
        qty = float(data.get("qty", 0.01))              # ✅ Mặc định 0.01 BTC
        leverage = int(data.get("leverage", 100))       # ✅ Mặc định 100x

        result = place_bingx_order(symbol, side, entry, qty, leverage)

        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ✅ Khởi động Flask server
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
