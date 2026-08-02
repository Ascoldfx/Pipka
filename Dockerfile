FROM python:3.12-slim-bookworm

ARG PG_MAJOR=16

WORKDIR /app

# System deps for JobSpy (headless Chrome for LinkedIn scraping).
# Keep pg_dump/psql on the same major as the pgvector:pg16 database: dumps
# produced by PostgreSQL 17 contain settings that PostgreSQL 16 cannot restore.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /tmp/postgresql-signing-key.asc \
    && test "$(gpg --show-keys --with-colons /tmp/postgresql-signing-key.asc \
       | awk -F: '$1 == "fpr" {print $10; exit}')" \
       = "B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8" \
    && install -d -m 0755 /usr/share/postgresql-common/pgdg \
    && gpg --dearmor --output /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg \
       /tmp/postgresql-signing-key.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver postgresql-client-${PG_MAJOR} \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/* \
       /tmp/postgresql-signing-key.asc \
    && groupadd --gid 10001 pipka \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin pipka

# Install Python deps first (cache layer)
COPY pyproject.toml .
RUN python -m pip install --no-cache-dir --upgrade pip==26.1.2
# JobSpy 1.1.82 has an obsolete markdownify<0.14 metadata cap. Its only
# usage is the backwards-compatible markdownify() function; override the cap
# after resolution to pick up the security fixes in 0.14.1.
RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir gunicorn \
    && pip install --no-cache-dir --no-deps markdownify==0.14.1

COPY --chown=10001:10001 . .
RUN mkdir -p /app/data && chown -R 10001:10001 /app/data

USER 10001:10001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "run.py"]
