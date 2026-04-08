FROM python:3.12-slim

WORKDIR /app

# System deps for JobSpy (headless Chrome for LinkedIn scraping)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir . && pip install gunicorn

COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "run.py"]
