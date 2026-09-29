FROM mcr.microsoft.com/playwright/python:v1.63.0-jammy

ENV PLAYWRIGHT_BROWSERS_PATH=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .

RUN python3 -m pip install --no-cache-dir -r requirements.txt \
    && python3 -c "import playwright; print('PLAYWRIGHT OK')"

COPY worker.py .

RUN mkdir -p /app/whatsapp_browser \
    && chown -R 1000:1000 /app

VOLUME ["/app/whatsapp_browser"]

EXPOSE 8080

USER pwuser

CMD ["xvfb-run", "-a", "python", "worker.py"]