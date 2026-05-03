FROM python:3.11-slim

WORKDIR /app

# System packages:
#   - gcc            : needed by some python packages with C extensions
#   - the rest       : runtime libraries Chromium needs (Playwright OBS scraper)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        wget \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libwayland-client0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser. We do NOT use `--with-deps` here because
# we already installed the system libraries above explicitly (smaller and more
# reproducible than letting Playwright apt-get them).
RUN python -m playwright install chromium

COPY . .

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
