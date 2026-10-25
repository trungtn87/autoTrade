"""Reference integration ONLY. Do not paste into a live bot unreviewed.

Call record_combo_async() once AFTER an ENTRY order has been confirmed,
preferably after the existing TP/SL logic has completed. Never send API keys.
"""
import logging
import os
import threading
import requests


def record_combo_async(order_id, combo, symbol, side, signal_id=''):
    url = os.getenv('AUTOTRADE_COLLECTOR_URL', '').rstrip('/')
    token = os.getenv('COLLECTOR_TOKEN', '')
    if not url or not token:
        return

    def worker():
        try:
            response = requests.post(
                url + '/v1/signals',
                json={
                    'entry_order_id': str(order_id),
                    'combo': str(combo),
                    'symbol': str(symbol),
                    'side': str(side).upper(),
                    'signal_id': str(signal_id),
                },
                headers={'X-Collector-Token': token},
                timeout=3,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logging.warning('Combo tag not stored for entry %s: %s', order_id, exc)
            # Recover missing tags from log or CSV. Production should add a durable retry queue.

    threading.Thread(target=worker, daemon=True).start()


# EXAMPLE ONLY:
# entry_order_id = entry_result['data']['order']['orderId']
# record_combo_async(entry_order_id, combo, symbol, side)
