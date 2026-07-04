FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "antcrew>=0.14.3" \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "sqlmodel>=0.0.21" \
    "aiosqlite>=0.20" \
    "python-multipart>=0.0.9" \
    "httpx>=0.27"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
