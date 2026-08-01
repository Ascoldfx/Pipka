FROM python:3.12-slim

WORKDIR /app

# System deps for JobSpy (headless Chrome for LinkedIn scraping)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache layer)
COPY pyproject.toml .
RUN python -m pip install --no-cache-dir --upgrade pip==26.1.2
# JobSpy 1.1.82 has an obsolete markdownify<0.14 metadata cap. Its only
# usage is the backwards-compatible markdownify() function; override the cap
# after resolution to pick up the security fixes in 0.14.1.
RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir gunicorn \
    && pip install --no-cache-dir --no-deps markdownify==0.14.1

COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "run.py"]
