# Vera bot — production image (Render, Railway, Fly.io or any Docker host).
#
# Small, fast-starting image: python:3.11-slim + 6 pinned dependencies.
# The server starts in ~1-2 s, so the only cold-start risk is the HOST putting
# the service to sleep — see docs/DEPLOYMENT.md (use an always-on instance).

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Dependencies first, so code changes don't re-install them (faster rebuilds).
COPY requirements.txt .
RUN pip install -r requirements.txt

# Only what the server needs at runtime — tests, starter pack and scripts are
# excluded by .dockerignore. No secrets are copied: API keys come from the
# platform's environment variables.
COPY app ./app
COPY bot.py .

# Run as a non-root user; the LLM cache lives in a writable folder it owns.
RUN useradd --create-home vera && mkdir -p /app/cache && chown -R vera /app
USER vera

EXPOSE 8080

# The judge polls /v1/healthz; the container reports its own health the same way.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/v1/healthz', timeout=2)"

# ONE worker on purpose: context + conversation state live in memory in this process.
# Shell form so $PORT (set by Render/Railway/Fly) is expanded at start-up.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --log-level ${LOG_LEVEL:-info}
