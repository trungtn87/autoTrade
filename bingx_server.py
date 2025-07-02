from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os

app = Flask(__name__)

# 🔐 Đọc API key từ biến môi trường Render hoặc file .env
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")

# 🛠️ Hàm ký dữ liệu theo chuẩn BingX
def generate_signature(params, secret):
    query_string = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

# 🔁 Hàm gửi lệnh thực tế qua BingX
def place_bingx_order(symbol, side, price, qty, leverage=10):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,               # Ví dụ: "BTC-USDT"
        "side": side.upper(),           # "BUY" hoặc "SELL"
        "price": str(price),            # Giá Entry
        "volume": str(qty),             # Khối lượng muốn đặt
        "leverage": "100",              # Đòn bẩy (chuỗi)
        "timestamp": timestamp
    }


    signature = generate_signature(params, BINGX_API_SECRET)
    params["signature"] = signature

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    response = requests.post(url, headers=headers, data=params)
    return response.json()

# ✅ API route để nhận lệnh từ Google Script
@app.route('/api/bingx_order', methods=['POST'])
def handle_bingx_order():
    try:
        data = request.get_json()
        symbol = data.get("symbol", "BTC-USDT")
        side = data.get("side", "BUY")
        entry = float(data.get("entry"))
        qty = float(data.get("qty", 0.001))  # khối lượng mặc định

        result = place_bingx_order(symbol, side, entry, qty)

        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ✅ Khởi động Flask server
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
