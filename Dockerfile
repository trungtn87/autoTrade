# Vibe Hosting: deploy ONLY the read-only Autotrade Live Dashboard.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY dashboard/requirements-web.txt /app/requirements.txt
RUN python -m pip install --disable-pip-version-check -r /app/requirements.txt
COPY dashboard/autotrade_live/ /app/autotrade_live/
RUN useradd --system --uid 10001 --create-home dashboard && chown -R dashboard:dashboard /app
USER dashboard
EXPOSE 8000
# One worker: only one background BingX GET-only archive scheduler.
CMD ["sh", "-c", "test -n \"$DATABASE_URL\" || { echo 'FATAL: DATABASE_URL must point to Supabase'; exit 1; }; exec uvicorn autotrade_live.mobile:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
