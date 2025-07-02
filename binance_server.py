from flask import Flask, request, jsonify
from binance.client import Client
from binance.enums import *
import os

app = Flask(__name__)

# ✅ Thay bằng API KEY thực tế
API_KEY = "YOUR_BINANCE_API_KEY"
API_SECRET = "YOUR_BINANCE_API_SECRET"

client = Client(API_KEY, API_SECRET)

@app.route('/api/binance_order', methods=['POST'])
def handle_order():
    try:
        data = request.get_json()
        symbol = data.get("symbol", "BTCUSDT")
        side = data.get("side", "BUY").upper()
        entry = float(data.get("entry"))
        combo = data.get("combo", "Combo X")
        timeframe = data.get("timeframe", "unknown")

        # 🔢 Khối lượng đặt lệnh (tuỳ chỉnh)
        qty = 0.001

        # 🔁 Gửi lệnh thị trường
        order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY if side == "BUY" else SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )

        print(f"✅ Đã đặt lệnh {side} {qty} {symbol} tại Entry {entry} - {combo} ({timeframe})")

        return jsonify({
            "status": "success",
            "symbol": symbol,
            "order_id": order["orderId"],
            "side": side,
            "combo": combo
        })

    except Exception as e:
        print("❌ Lỗi:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
