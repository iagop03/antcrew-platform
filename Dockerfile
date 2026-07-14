# Stage 1 — install Python dependencies
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml .
# Install into an isolated prefix so only these files are copied to the runtime stage
RUN pip install --no-cache-dir --prefix=/deps ".[billing]"


# Stage 2 — lean runtime image
FROM python:3.11-slim AS runtime

# asyncpg needs libpq at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN adduser --disabled-password --gecos "" --uid 1000 appuser

COPY --from=builder /deps /usr/local

WORKDIR /app
COPY . .
RUN chown -R appuser:appuser /app

USER appuser

# APP_ENV is baked at build time and can be overridden at container runtime.
# Build: docker build --build-arg APP_ENV=prod .
# Run:   docker run -e APP_ENV=prod ...
ARG APP_ENV=prod
ENV APP_ENV=${APP_ENV} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
