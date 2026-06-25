# ── Base image ────────────────────────────────────────────────────────────────
# Slim Debian-based Python 3.12 — smaller than full, still has build tools
FROM python:3.12-slim

# ── Metadata ──────────────────────────────────────────────────────────────────
LABEL maintainer="tg-forwarder" \
      description="Telegram userbot forwarder with Pyrogram + Koyeb health-check"

# ── System dependencies ───────────────────────────────────────────────────────
# gcc / libssl-dev needed by TgCrypto (C extension) and motor's bson C layer
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
# Copy requirements first so Docker layer-caches the pip install step
# and only re-runs it when requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Copy application source ───────────────────────────────────────────────────
COPY . .

# ── Runtime environment ───────────────────────────────────────────────────────
# PORT is injected at runtime by Koyeb; 8080 is the local-dev fallback.
# All other secrets (API_ID, BOT_TOKEN, MONGO_URI …) must be set as
# environment variables in the Koyeb service settings — never baked in here.
ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── Expose health-check port ──────────────────────────────────────────────────
EXPOSE 8080

# ── Entry point ───────────────────────────────────────────────────────────────
CMD ["python", "main.py"]
