"""Daily PostgreSQL backup service.

Runs pg_dump, gzips the output, saves to /app/data/backups/ (kept last 7),
and optionally uploads to Backblaze B2 (set B2_KEY_ID / B2_APP_KEY / B2_BUCKET in .env).
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("/app/data/backups")
KEEP_LAST = 7


def _parse_db_url(url: str) -> dict:
    """Extract connection components from postgresql+asyncpg://user:pass@host:port/dbname."""
    # Strip SQLAlchemy driver prefix
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    m = re.match(
        r"postgresql://([^:@]+)(?::([^@]*))?@([^:/]+)(?::(\d+))?/(\w+)",
        url,
    )
    if not m:
        raise ValueError(f"Cannot parse DATABASE_URL: {url!r}")
    return {
        "user": m.group(1),
        "password": m.group(2) or "",
        "host": m.group(3),
        "port": m.group(4) or "5432",
        "dbname": m.group(5),
    }


async def run_backup() -> str:
    """Run pg_dump, gzip, save locally, rotate old backups, optionally upload to B2.

    Returns the local backup file path, or empty string if skipped (non-PG DB).
    Raises RuntimeError if pg_dump fails.
    """
    if not settings.database_url.startswith("postgresql"):
        logger.info("Backup skipped — not a PostgreSQL database (%s)", settings.database_url[:30])
        return ""

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"pipka_{timestamp}.sql.gz"

    db = _parse_db_url(settings.database_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]

    cmd = [
        "pg_dump",
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "--no-password",
        db["dbname"],
    ]

    logger.info("DB backup starting → %s", backup_path.name)
    result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if result.returncode != 0:
        err = result.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {err}")

    with gzip.open(backup_path, "wb") as gz:
        gz.write(result.stdout)

    size_kb = backup_path.stat().st_size / 1024
    logger.info("DB backup saved: %s (%.1f KB compressed)", backup_path.name, size_kb)

    _rotate_backups()

    # B2 upload is best-effort — a failed upload does NOT fail the backup
    if settings.b2_key_id and settings.b2_app_key and settings.b2_bucket:
        await _upload_b2(backup_path)

    return str(backup_path)


def _rotate_backups() -> None:
    """Delete oldest backups, keep only KEEP_LAST files."""
    backups = sorted(BACKUP_DIR.glob("pipka_*.sql.gz"))
    to_delete = backups[:-KEEP_LAST] if len(backups) > KEEP_LAST else []
    for old in to_delete:
        old.unlink(missing_ok=True)
        logger.info("Rotated old backup: %s", old.name)


async def _upload_b2(path: Path) -> None:
    """Upload backup file to Backblaze B2 using S3-compatible API."""
    try:
        import asyncio

        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.b2_endpoint,
            aws_access_key_id=settings.b2_key_id,
            aws_secret_access_key=settings.b2_app_key,
            config=Config(signature_version="s3v4"),
        )
        key = f"db-backups/{path.name}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: s3.upload_file(str(path), settings.b2_bucket, key),
        )
        logger.info("Backup uploaded to B2: s3://%s/%s", settings.b2_bucket, key)
    except ImportError:
        logger.warning("boto3 not installed — B2 upload skipped. Add boto3 to pyproject.toml.")
    except Exception as e:
        logger.error("B2 upload failed (local backup is intact): %s", e)
