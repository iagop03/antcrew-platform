FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn[standard] sqlmodel aiosqlite \
    python-multipart httpx

COPY . .

RUN pip install --no-cache-dir -e ../antcrew 2>/dev/null || \
    pip install --no-cache-dir "antcrew>=0.14.3"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
