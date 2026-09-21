.PHONY: env test data research

env:
	python -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

test:
	python selftest.py

data:
	python bootstrap_data.py --symbols BTC-USDT,ETH-USDT --intervals 15m,1h,4h,6h --start 2021-01-01 --out-dir data

research:
	python research_pipeline.py --symbol BTCUSDT --data15 data/BTCUSDT_15m.pkl --data1h data/BTCUSDT_1h.pkl --data4h data/BTCUSDT_4h.pkl --data6h data/BTCUSDT_6h.pkl --out-dir results/BTCUSDT
	python research_pipeline.py --symbol ETHUSDT --data15 data/ETHUSDT_15m.pkl --data1h data/ETHUSDT_1h.pkl --data4h data/ETHUSDT_4h.pkl --data6h data/ETHUSDT_6h.pkl --out-dir results/ETHUSDT
