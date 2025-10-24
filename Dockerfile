# Small, reliable base
FROM python:3.11-slim

# Keep Python quiet + unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg is handy for media work (keeps POC future-proof)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
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

# Render will map the port; EXPOSE is just documentation
EXPOSE 8000

# Start FastAPI with Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
