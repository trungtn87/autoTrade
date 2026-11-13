FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Identical self-contained dependency manifest for Docker and Railpack.
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --disable-pip-version-check -r /app/requirements.txt

COPY unified_app.py /app/unified_app.py
# V3 live engine (five layers). The offline V3 research lab is not copied.
COPY autotrade_v3/__init__.py /app/autotrade_v3/__init__.py
COPY autotrade_v3/live/ /app/autotrade_v3/live/
# V2 modules reused by V3 unchanged (BingX transport, kline stream, 20FINAL
# strategy, hourly archive) plus the AI page. V2's engine is not started.
COPY autotrade_v2/app/ /app/autotrade_v2/app/
COPY autotrade_v2/analytics/ /app/autotrade_v2/analytics/
COPY dashboard/autotrade_live/ /app/dashboard/autotrade_live/

RUN useradd --system --uid 10001 --create-home service && chown -R service:service /app
USER service
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn unified_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
