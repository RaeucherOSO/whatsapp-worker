FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .

RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY worker.py .

RUN mkdir -p /app/whatsapp_browser
RUN chown -R pwuser:pwuser /app

VOLUME ["/app/whatsapp_browser"]

EXPOSE 8080

USER pwuser

CMD ["xvfb-run", "-a", "python3", "worker.py"]
