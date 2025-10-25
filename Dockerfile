# Small, reliable base
FROM python:3.11-slim

# Quiet Python + unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg (with libass) + a reliable font
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu \
  && rm -rf /var/lib/apt/lists/*

# App dir
WORKDIR /app

# Install deps first (better Docker cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY app ./app

# Ensure package-style imports work (app.*)
ENV PYTHONPATH=/app

# Documented port (Render autodetects)
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
