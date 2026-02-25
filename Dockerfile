FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ARR_SEARCH_COOLDOWN_HOURS=24

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash cron ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY search_not_found_arr.py /app/search_not_found_arr.py
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
COPY docker/run_server.sh /app/docker/run_server.sh

RUN chmod +x /app/docker/entrypoint.sh /app/docker/run_server.sh

ENTRYPOINT ["/app/docker/entrypoint.sh"]
