# Stage 2-B — Finance API Docker image
#
# Build context: project root (agentic-ai/)
# Built by docker-compose as the `finance-api` service.
#
# The container mounts:
#   config/settings.toml  (read-only)
#   data/                 (read-write — SQLite DB lives here)
#   output/xls/           (read-only — ALL_TRANSACTIONS.xlsx)

FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY finance/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir \
        rapidfuzz>=3.6.0 \
        openpyxl>=3.1.0 \
        "fastapi>=0.110.0" \
        "uvicorn[standard]>=0.27.0"

# Copy finance package
COPY finance/ /app/finance/

# Copy pre-built PWA (run `npm run build` in pwa/ before docker build)
# If dist/ is absent the API still starts — it just won't serve the PWA.
COPY pwa/dist/ /app/pwa/dist/

EXPOSE 8090

CMD ["python3", "-m", "finance.server"]
