FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# MAX platform-api2 uses the Russian Trusted CA chain. Install the Debian
# trust-store tooling, then add the official RSA root + current 2024 sub CA
# downloaded from the gosuslugi.ru/crt static host (gu-st.ru).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY certs/russian_trusted_root_ca_pem.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
COPY certs/russian_trusted_sub_ca_2024_pem.crt /usr/local/share/ca-certificates/russian_trusted_sub_ca_2024.crt
RUN update-ca-certificates

# Install dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and static menu assets.
COPY app ./app
COPY assets ./assets
COPY tests ./tests
COPY .env.example ./.env.example
COPY README.md ./README.md

# Data + logs are mounted as volumes; ensure dirs exist.
RUN mkdir -p /app/data /app/logs

EXPOSE 8080

# Healthcheck: the FastAPI /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
