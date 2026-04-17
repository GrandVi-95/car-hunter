FROM python:3.11-slim

# ─── System deps required by headless Chromium ───────────────────
# Keep this list minimal — Playwright's `install-deps` also runs below
# as a belt-and-braces step for any distro package the browser needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        gnupg \
        ca-certificates \
        fonts-liberation \
        fonts-noto-color-emoji \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libexpat1 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
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
        tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Jerusalem \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + any remaining OS-level deps Playwright knows about.
RUN python -m playwright install chromium \
 && python -m playwright install-deps chromium

COPY . .

CMD ["python", "main.py"]
