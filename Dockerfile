FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir kerykeion google-auth google-api-python-client google-auth-httplib2 requests cryptography

COPY . .

CMD ["python", "monitor_loop.py"]
