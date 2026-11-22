FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 kyrx

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && mkdir -p /app/var \
    && chown -R kyrx:kyrx /app

USER kyrx

ENV KYRX_DATA_DIR=/app/var \
    KYRX_TRANSPORT=http \
    KYRX_HOST=0.0.0.0 \
    KYRX_PORT=8765

EXPOSE 8765

ENTRYPOINT ["kyrx", "serve"]
