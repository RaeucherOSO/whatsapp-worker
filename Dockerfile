FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py .

RUN mkdir -p /app/whatsapp_browser

EXPOSE 8080

CMD ["xvfb-run", "-a", "python", "worker.py"]