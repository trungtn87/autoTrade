FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-web.txt /app/requirements.txt
RUN python -m pip install --disable-pip-version-check -r /app/requirements.txt
COPY autotrade_live/ /app/autotrade_live/
RUN useradd --system --uid 10001 --create-home service && chown -R service:service /app
USER service
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn autotrade_live.mobile:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
