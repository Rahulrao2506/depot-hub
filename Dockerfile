FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEPOT_DATA=/data \
    DEPOT_PUBLIC=1 \
    DEPOT_PROXY=1

WORKDIR /app
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY server.py ./
COPY web ./web

RUN mkdir -p /data
EXPOSE 8080

# ONE worker on purpose: live push (SSE) and the spam guard live in memory.
# Many threads, because every open phone holds one thread for its live stream.
CMD ["sh", "-c", "exec gunicorn server:app --worker-class gthread --workers 1 --threads 200 --bind 0.0.0.0:${PORT:-8080} --timeout 120 --graceful-timeout 20 --access-logfile -"]
