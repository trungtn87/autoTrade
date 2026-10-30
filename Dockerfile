# Isolated hosting diagnostic. No trading application or network calls.
FROM python:3.12-alpine
WORKDIR /site
COPY index.html /site/index.html
COPY health.txt /site/health.txt
EXPOSE 8000
CMD ["python", "-m", "http.server", "8000", "--bind", "0.0.0.0", "--directory", "/site"]
