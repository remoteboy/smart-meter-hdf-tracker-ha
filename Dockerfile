FROM python:3.11-slim-bookworm

# Install system dependencies for Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    ca-certificates \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium headless shell only (smaller than full browser)
RUN playwright install chromium --with-deps 2>/dev/null || playwright install chromium

# Copy application code
COPY api/       ./api/
COPY scraper/   ./scraper/
COPY dashboard/ ./dashboard/

# Create data directory (will be overridden by volume mount)
RUN mkdir -p /data

# Default config — override by mounting /app/config.json or setting env vars
COPY config.example.json ./config.json

EXPOSE 8000

# Entrypoint runs both the API and a cron-like scheduler in one process
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

CMD ["./docker-entrypoint.sh"]
