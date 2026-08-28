FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Healthy as long as the periodic heartbeat (every 30s, independent of
# message flow) landed within the last 90s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,time; p='/tmp/healthy'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 90 else 1)"

CMD ["python", "-m", "consumer.main"]
