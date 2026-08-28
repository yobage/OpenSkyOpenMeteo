FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Healthy as long as a poll/publish cycle succeeded within the last 90s
# (comfortably more than one POLL_INTERVAL_SECONDS default of 12s).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,time; p='/tmp/healthy'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 90 else 1)"

CMD ["python", "-m", "ingestion.main"]
