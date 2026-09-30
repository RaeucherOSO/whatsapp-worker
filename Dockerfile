FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .

RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Google Chrome for Testing 154
USER root
RUN apt-get update -qq \
    && apt-get install -y -qq wget unzip ca-certificates \
    && wget -q https://storage.googleapis.com/chrome-for-testing-public/154.0.8037.92/linux64/chrome-linux64.zip -O /tmp/chrome.zip \
    && unzip -q /tmp/chrome.zip -d /opt \
    && rm /tmp/chrome.zip \
    && /opt/chrome-linux64/chrome --version

COPY worker.py .

RUN mkdir -p /app/whatsapp_browser
RUN chown -R pwuser:pwuser /app

VOLUME ["/app/whatsapp_browser"]

EXPOSE 8080

USER pwuser

CMD ["xvfb-run", "-a", "python3", "worker.py"]