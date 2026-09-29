FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .

RUN python3 -m pip install --no-cache-dir \
    Flask \
    psycopg2-binary \
    python-dotenv \
    playwright==1.48.0

COPY worker.py .

RUN mkdir -p /app/whatsapp_browser \
    && chown -R pwuser:pwuser /app

VOLUME ["/app/whatsapp_browser"]

EXPOSE 8080

USER pwuser

CMD ["xvfb-run", "-a", "python3", "worker.py"]