from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import requests
import os
import sys
import threading

app = Flask(__name__)

# 🔐 Load API key from environment
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET")
GLOBAL_TP_CACHE = {}
GLOBAL_SL_CACHE = {}
FAILSAFE_STATE = {}
# key: BTC-USDT_LONG → {"retry": 0, "closed": False}


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
#HÀM LẤY OPEN ORDERS
def get_open_orders(symbol):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/openOrders"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "timestamp": timestamp
    }

    signature = generate_signature(params, BINGX_API_SECRET)
    query_string = "&".join(f"{k}={params[k]}" for k in sorted(params))
    full_url = f"{url}?{query_string}&signature={signature}"

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    print("🔍 CHECK OPEN ORDERS:", full_url, flush=True)
    r = requests.get(full_url, headers=headers, timeout=5)
    return r.json().get("data", [])
#CHECK TP/SL ĐÚNG CHUẨN BINGX
def check_tp_sl_open_orders(symbol, position_side):
    orders = get_open_orders(symbol)

    has_tp = False
    has_sl = False

    for o in orders:
        if o.get("positionSide") != position_side:
            continue
        if o.get("type") == "TAKE_PROFIT_MARKET":
            has_tp = True
        if o.get("type") == "STOP_MARKET":
            has_sl = True

    print(
        f"🧪 CHECK TP/SL {symbol} {position_side} → TP:{has_tp} | SL:{has_sl}",
        flush=True
    )

    return has_tp, has_sl


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
def wait_for_position_amt(symbol, position_side, timeout=8):
    """
    Chờ position sync xong, trả về positionAmt thực tế
    """
    start = time.time()

    while time.time() - start < timeout:
        pos = get_bingx_position(symbol, position_side)
        if pos.get("exists") and pos.get("positionAmt"):
            try:
                amt = abs(float(pos["positionAmt"]))
                if amt > 0:
                    return amt
            except:
                pass
        time.sleep(0.5)

    return None

def execute_alert_trade(symbol, side, entry, qty, tp, sl, leverage=100, order_type="MARKET"):
    market_sent_time = time.time()

    entry_result = place_bingx_order(symbol, side, entry, qty, leverage, order_type)

    position_side = "LONG" if side.upper() == "BUY" else "SHORT"

    real_qty = wait_for_position_amt(symbol, position_side)

    if real_qty is None:
        print("⚠️ Cannot detect positionAmt → fallback to original qty", flush=True)
        real_qty = qty
    else:
        print(f"✅ Detected real positionAmt: {real_qty}", flush=True)

    GLOBAL_TP_CACHE[symbol] = tp
    GLOBAL_SL_CACHE[symbol] = sl

    tp_sl_result = place_tp_sl_order(
        symbol=symbol,
        side_entry=side,
        qty=real_qty,
        tp=tp,
        sl=sl
    )

    threading.Thread(
        target=failsafe_watch,
        args=(symbol, side, market_sent_time),
        daemon=True
    ).start()

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
# HÀM LẤY POSITION TỪ BINGX
def get_bingx_position(symbol, position_side):
    url = "https://open-api.bingx.com/openApi/swap/v2/user/positions"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "timestamp": timestamp
    }

    signature = generate_signature(params, BINGX_API_SECRET)
    query_string = "&".join(f"{k}={params[k]}" for k in sorted(params))
    full_url = f"{url}?{query_string}&signature={signature}"

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    r = requests.get(full_url, headers=headers, timeout=5)
    data = r.json()

    positions = data.get("data", [])
    for p in positions:
        try:
            amt = float(p.get("positionAmt", 0))
        except:
            amt = 0

        if p.get("positionSide") == position_side and amt != 0:
            return {
                "exists": True,
                "positionAmt": amt,          # 🔥 QUAN TRỌNG
                "tp": p.get("takeProfit"),
                "sl": p.get("stopLoss")
            }

    return {
        "exists": False,
        "positionAmt": 0,
        "tp": None,
        "sl": None
    }

#HÀM ĐÓNG LỆNH MARKET (FAILSAFE CLOSE)
def close_position_market(symbol, side, qty):
    close_side = "SELL" if side.upper() == "BUY" else "BUY"
    position_side = "LONG" if side.upper() == "BUY" else "SHORT"

    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))

    params = {
        "symbol": symbol,
        "side": close_side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": f"{qty:.4f}".rstrip('0').rstrip('.'),
        "timestamp": timestamp
    }

    signature = generate_signature(params, BINGX_API_SECRET)
    query_string = "&".join(f"{k}={params[k]}" for k in sorted(params))
    full_url = f"{url}?{query_string}&signature={signature}"

    headers = {
        "X-BX-APIKEY": BINGX_API_KEY
    }

    print("🔥 FAILSAFE CLOSE MARKET:", full_url, flush=True)
    r = requests.post(full_url, headers=headers)
    print("📥 FAILSAFE CLOSE RESPONSE:", r.text, flush=True)
# FAILSAFE WATCHER


def failsafe_watch(symbol, side, market_time):
    position_side = "LONG" if side.upper() == "BUY" else "SHORT"
    key = f"{symbol}_{position_side}_{int(market_time)}"

    FAILSAFE_STATE[key] = {"retry": 0, "closed": False}

    time.sleep(300)

    has_tp, has_sl = check_tp_sl_open_orders(symbol, position_side)

    if has_tp and has_sl:
        print("✅ FAILSAFE CHECK PASSED – TP/SL OK", flush=True)
        return

    pos = get_bingx_position(symbol, position_side)
    if not pos.get("exists"):
        print("ℹ️ FAILSAFE: No position found", flush=True)
        return

    try:
        real_qty = abs(float(pos["positionAmt"]))
    except:
        print("❌ FAILSAFE: Cannot read real positionAmt", flush=True)
        return

    # ===== STAGE 1 =====
    print("⚠️ FAILSAFE STAGE 1 – Retry TP/SL", flush=True)
    place_tp_sl_order(
        symbol=symbol,
        side_entry=side,
        qty=real_qty,
        tp=GLOBAL_TP_CACHE.get(symbol),
        sl=GLOBAL_SL_CACHE.get(symbol)
    )

    time.sleep(180)

    has_tp2, has_sl2 = check_tp_sl_open_orders(symbol, position_side)
    if has_tp2 and has_sl2:
        print("✅ FAILSAFE RECOVERED – TP/SL OK", flush=True)
        return

    # ===== STAGE 2 =====
    if not FAILSAFE_STATE[key]["closed"]:
        print("🔥 FAILSAFE STAGE 2 – CLOSE MARKET", flush=True)
        close_position_market(symbol, side, real_qty)
        FAILSAFE_STATE[key]["closed"] = True


# ✅ Route test
@app.route('/', methods=['GET'])
def home():
    return "✅ BingX AutoTrade Server is running."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
