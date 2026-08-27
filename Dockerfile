FROM python:3.12-slim
WORKDIR /app

# Set timezone to Asia/Taipei so log timestamps match host system time
ENV TZ=Asia/Taipei
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY pyproject.toml .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
RUN pip install --no-cache-dir -e .
RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -d /app -s /bin/bash appuser
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh
RUN mkdir -p /app/data /tmp/numba_cache && chown -R appuser:appuser /app
ENV NUMBA_CACHE_DIR=/tmp/numba_cache
ENTRYPOINT ["/app/docker-entrypoint.sh"]
