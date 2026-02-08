# Python FastAPI Backend for Checked (Chess Kenya)
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy Alembic migrations
COPY alembic/ ./alembic/
COPY alembic.ini .

# Create data directory for SQLite (will be mounted as volume)
RUN mkdir -p /data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Start the application (init_db creates tables, alembic runs only if DB exists)
CMD ["sh", "-c", "alembic upgrade head 2>&1 || true; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
