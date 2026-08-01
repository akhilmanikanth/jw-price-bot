# Long-running bot mode (/check + weekly APScheduler job).
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Australia/Sydney

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

VOLUME ["/app/data", "/app/logs"]

CMD ["python", "main.py", "bot"]
