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
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1489294178658554078/5PplpHNFKnR2_IMoL32D7dnUPE8C1y1TuDX1mkfQ_6l2bRg9JKc-e9jSmjEbdCRaTnzd"

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

    results = []

    # 👉 chia volume
    tp_qty = round(qty * 0.5, 4)
    sl_qty = qty

    for label, price, order_type, q in [
        ("TP", tp, "TAKE_PROFIT_MARKET", tp_qty),
        ("SL", sl, "STOP_MARKET", sl_qty)
    ]:
        params = {
            "symbol": symbol,
            "side": opposite_side,
            "positionSide": position_side,
            "type": order_type,
            "stopPrice": str(price),
            "quantity": f"{q:.4f}".rstrip('0').rstrip('.'),
            "timestamp": str(int(time.time() * 1000))
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
# ✅ 2. THÊM HÀM TRAILING
def place_trailing_order(symbol, side_entry, qty, activation_price, callback_rate):
    opposite_side = "SELL" if side_entry.upper() == "BUY" else "BUY"
    position_side = "LONG" if side_entry.upper() == "BUY" else "SHORT"

    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"

    params = {
        "symbol": symbol,
        "side": opposite_side,
        "positionSide": position_side,
        "type": "TRAILING_STOP_MARKET",
        "quantity": f"{qty:.4f}".rstrip('0').rstrip('.'),
        "activationPrice": str(round(activation_price, 2)),
        "priceRate": str(callback_rate),
        "timestamp": str(int(time.time() * 1000))
    }

    query_string = "&".join(f"{key}={params[key]}" for key in sorted(params))
    signature = generate_signature(params, BINGX_API_SECRET)

    full_url = f"{url}?{query_string}&signature={signature}"

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    print("📤 Sending TRAILING:", full_url, flush=True)
    response = requests.post(full_url, headers=headers)
    print("📥 Trailing response:", response.text, flush=True)

    return response.json()
# ✅ Gộp lệnh entry + TP/SL
def execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage=100, order_type="MARKET"):

    # ===== 1. PLACE ENTRY ORDER =====
    entry_result = place_bingx_order(symbol, side, entry, qty, leverage, order_type)

    order_id = entry_result.get("data", {}).get("order", {}).get("orderId")

    if not order_id:
        raise RuntimeError("❌ Không lấy được orderId từ lệnh ENTRY")

    order = {}
    executed_qty = 0
    avg_price = 0
    status = ""

    # ===== 2. CHỜ ORDER FILL =====
    for i in range(5):
        order_detail = get_order_detail(symbol, order_id)

        order = order_detail.get("data", {}).get("order", {})

        executed_qty = float(order.get("executedQty", 0))
        avg_price = float(order.get("avgPrice", 0))
        status = order.get("status", "")

        print(f"🔎 Check order {i+1}/5 | status={status} qty={executed_qty} avg={avg_price}", flush=True)

        if executed_qty > 0 and avg_price > 0:
            break

        time.sleep(1)

    if executed_qty <= 0 or avg_price <= 0:
        send_discord(f"❌ Lỗi đặt lệnh\n{symbol} {side}")
        raise RuntimeError("❌ Không lấy được executedQty hoặc avgPrice")
       

    print("📊 Executed Qty:", executed_qty)
    print("📊 Avg Price:", avg_price)
    print("📊 TP:", tp)
    print("📊 SL:", sl)
    send_discord(
            f"✅ Đặt lệnh \n"
            f"{symbol} {side}\n\n"
            f"💰 Entry: {round(avg_price, 2)}"
        )

    # ===== 3. CHECK TP SL RANGE =====
    valid_trade = False

    if side.upper() == "BUY":
        if sl < avg_price and tp > avg_price:
            valid_trade = True

    elif side.upper() == "SELL":
        if tp < avg_price and sl > avg_price:
            valid_trade = True

    # ===== 4. IF VALID → SET TP SL =====
    # ===== 4. IF VALID → SET TP SL + TRAILING =====
    if valid_trade:

        print("✅ Giá entry hợp lệ → đặt TP/SL + TRAILING")

        tp_sl_result = place_tp_sl_order(
            symbol,
            side,
            executed_qty,
            tp,
            sl
        )
        send_discord(
            f"✅ Đặt  \n"
            f"{symbol} {side}\n\n"
            f"💰 Entry: {round(avg_price, 2)}"
            f"TP : {tp} SL : {sl} /n"
        )

    # ===== THÊM TRAILING =====
        risk = abs(avg_price - sl)

        if side.upper() == "BUY":
            activation_price = avg_price + risk * 0.5
        else:
            activation_price = avg_price - risk * 0.5

        trailing_result = place_trailing_order(
            symbol,
            side,
            executed_qty * 0.5,
            activation_price,
            0.005
        )

        return {
            "entry": entry_result,
            "tp_sl": tp_sl_result,
            "trailing": trailing_result
        }

    # ===== 5. IF INVALID → CLOSE MARKET =====
    else:

        print("⚠️ Giá entry nằm ngoài TP/SL → đóng lệnh MARKET")
        send_discord("⚠️ Giá entry nằm ngoài TP/SL → đóng lệnh MARKET")
        close_side = "SELL" if side.upper() == "BUY" else "BUY"

        close_result = place_bingx_order(
            symbol,
            close_side,
            qty=executed_qty,
            leverage=leverage,
            order_type="MARKET"
        )

        return {
            "entry": entry_result,
            "close_market": close_result
        }
def get_order_detail(symbol, order_id):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"

    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "orderId": order_id,
        "timestamp": timestamp
    }

    query_string = "&".join(f"{key}={params[key]}" for key in sorted(params))
    signature = generate_signature(params, BINGX_API_SECRET)

    full_url = f"{url}?{query_string}&signature={signature}"

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    response = requests.get(full_url, headers=headers)
    print("📥 Order Detail:", response.text, flush=True)

    return response.json()

# ✅ Route chính để nhận lệnh
@app.route('/api/bingx_order', methods=['POST'])
def handle_bingx_order():
    try:
        data = request.get_json()
        print("📥 Dữ liệu nhận:", data, flush=True)

        symbol = data.get("symbol", "BTC-USDT")
        side = data.get("side", "BUY")
        entry = float(data.get("entry", 0))
        leverage = int(data.get("leverage", 100))
        tp = float(data.get("tp", 0))
        sl = float(data.get("sl", 0))
        order_type = data.get("order_type", "MARKET").upper()

        # ⚡ Giá trị USDT muốn giao dịch (trước khi nhân leverage)
        usdt_amount = float(data.get("usdt_amount", 50))  # ví dụ mặc định 50 USDT

        # ✅ Tính khối lượng = số USDT / giá Entry
        qty = round(usdt_amount / entry, 4)  # làm tròn 4 chữ số thập phân

        result = execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage, order_type)
        return jsonify({"status": "success", "result": result})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def send_discord(message):
    try:
        res = requests.post(DISCORD_WEBHOOK, json={"content": message})

        if res.status_code != 204:
            print("⚠️ Discord response:", res.text, flush=True)

    except Exception as e:
        print("❌ Discord error:", str(e), flush=True)

# ✅ Route test
@app.route('/', methods=['GET'])
def home():
    return "✅ BingX AutoTrade Server is running."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
