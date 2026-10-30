FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY autotrade_v2/requirements.txt /app/autotrade_v2/requirements.txt
COPY dashboard/requirements-web.txt /app/dashboard/requirements-web.txt
RUN python -m pip install --disable-pip-version-check -r /app/requirements.txt

COPY unified_app.py /app/unified_app.py
COPY autotrade_v2/app/ /app/autotrade_v2/app/
COPY dashboard/autotrade_live/ /app/dashboard/autotrade_live/

RUN useradd --system --uid 10001 --create-home service && chown -R service:service /app
USER service
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn unified_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
