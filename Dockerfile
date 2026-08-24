FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOULSCRIBE_CONFIG_DIR=/config \
    SOULSCRIBE_PORT=8793

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8793
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('SOULSCRIBE_PORT','8793')+'/health')" || exit 1

CMD ["soulscribe"]
