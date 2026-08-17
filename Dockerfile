FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/app.db \
    PORT=8000

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create volume directory for persistent SQLite database storage
RUN mkdir -p /data

# Copy application source code
COPY app /app/app

EXPOSE 8000

# Run Uvicorn web server (Lifespan automatically starts worker and reconciler background tasks)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
