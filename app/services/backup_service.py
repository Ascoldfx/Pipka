"""Daily PostgreSQL backup service.

Runs pg_dump, gzips the output, saves to /app/data/backups/ (kept last 7),
and optionally uploads to Backblaze B2 (set B2_KEY_ID / B2_APP_KEY / B2_BUCKET in .env).
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.config import settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("/app/data/backups")
KEEP_LAST = 7


def _parse_db_url(url: str) -> dict:
    """Extract connection components from postgresql+asyncpg://user:pass@host:port/dbname."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        # Never echo a malformed URL: it may contain a database password.
        raise ValueError("Cannot parse PostgreSQL DATABASE_URL") from None
    if (
        parsed.get_backend_name() != "postgresql"
        or not parsed.username
        or not parsed.host
        or not parsed.database
    ):
        raise ValueError("Cannot parse PostgreSQL DATABASE_URL")
    return {
        "user": parsed.username,
        "password": parsed.password or "",
        "host": parsed.host,
        "port": str(parsed.port or 5432),
        "dbname": parsed.database,
    }


async def run_backup() -> str:
    """Run pg_dump, gzip, save locally, rotate old backups, optionally upload to B2.

    Returns the local backup file path, or empty string if skipped (non-PG DB).
    Raises RuntimeError if pg_dump fails.
    """
    if not settings.database_url.startswith("postgresql"):
        logger.info("Backup skipped — not a PostgreSQL database (%s)", settings.database_url[:30])
        return ""

    backup_path = await asyncio.to_thread(_create_local_backup)

    _rotate_backups()

    # B2 upload is best-effort — a failed upload does NOT fail the backup
    if settings.b2_key_id and settings.b2_app_key and settings.b2_bucket:
        await _upload_b2(backup_path)

    return str(backup_path)


def _create_local_backup() -> Path:
    """Create one atomic local dump outside the event loop."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"pipka_{timestamp}.sql.gz"
    temporary_path = Path(f"{backup_path}.tmp")

    db = _parse_db_url(settings.database_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]
    _assert_postgres_client_matches_server(db, env)

    cmd = [
        "pg_dump",
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "--no-password",
        db["dbname"],
    ]

    logger.info("DB backup starting → %s", backup_path.name)
    result = subprocess.run(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )

    if result.returncode != 0:
        err = result.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {err}")

    try:
        with gzip.open(temporary_path, "wb") as gz:
            gz.write(result.stdout)
        temporary_path.replace(backup_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    size_kb = backup_path.stat().st_size / 1024
    logger.info("DB backup saved: %s (%.1f KB compressed)", backup_path.name, size_kb)

    return backup_path


def _assert_postgres_client_matches_server(db: dict, env: dict[str, str]) -> None:
    """Refuse dumps that cannot be reliably restored into the running server."""
    client = subprocess.run(
        ["pg_dump", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    server = subprocess.run(
        [
            "psql", "-h", db["host"], "-p", db["port"], "-U", db["user"],
            "--no-password", "-At", "-d", db["dbname"], "-c", "SHOW server_version_num;",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    client_match = re.search(rb"PostgreSQL\)\s+(\d+)", client.stdout)
    try:
        server_major = int(server.stdout.strip()) // 10000
    except ValueError:
        server_major = 0

    if client.returncode or server.returncode or not client_match or not server_major:
        raise RuntimeError("Cannot verify PostgreSQL client/server versions before backup")

    client_major = int(client_match.group(1))
    if client_major != server_major:
        raise RuntimeError(
            "PostgreSQL client/server major mismatch before backup "
            f"({client_major} vs {server_major})"
        )


async def verify_latest_backup_restore() -> str:
    """Restore the newest local backup into a disposable PostgreSQL DB."""
    backups = sorted(BACKUP_DIR.glob("pipka_*.sql.gz"))
    if not backups:
        raise RuntimeError("No local backup is available for restore verification")
    path = backups[-1]
    await asyncio.to_thread(_verify_backup_restore, path)
    return str(path)


def _verify_backup_restore(path: Path) -> None:
    """Perform a real restore and validate core tables, then always drop it."""
    db = _parse_db_url(settings.database_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]
    check_db = f"pipka_restorecheck_{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.getpid()}"
    common = ["-h", db["host"], "-p", db["port"], "-U", db["user"], "--no-password"]
    created = False

    try:
        archive_check = subprocess.run(
            ["gzip", "-t", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if archive_check.returncode:
            raise RuntimeError("Backup gzip integrity check failed")

        create = subprocess.run(
            ["createdb", *common, check_db],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if create.returncode:
            raise RuntimeError(
                f"restore-check createdb failed: {create.stderr.decode(errors='replace')[:300]}"
            )
        created = True

        decompressor = subprocess.Popen(
            ["gzip", "-dc", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert decompressor.stdout is not None
        restore = subprocess.Popen(
            ["psql", *common, "-v", "ON_ERROR_STOP=1", "-d", check_db],
            env=env,
            stdin=decompressor.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        decompressor.stdout.close()
        try:
            _, restore_stderr = restore.communicate(timeout=600)
            gzip_stderr = decompressor.stderr.read() if decompressor.stderr else b""
            gzip_code = decompressor.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            restore.kill()
            decompressor.kill()
            restore.wait()
            decompressor.wait()
            raise RuntimeError("Backup restore check timed out") from exc

        if restore.returncode or gzip_code:
            detail = (restore_stderr or gzip_stderr).decode(errors="replace")[:500]
            raise RuntimeError(f"Backup restore check failed: {detail}")

        verify = subprocess.run(
            [
                "psql", *common, "-At", "-d", check_db, "-c",
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name IN ('jobs','users','job_scores');",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if verify.returncode or verify.stdout.strip() != b"3":
            raise RuntimeError("Restored backup is missing one or more core tables")
    finally:
        if created:
            subprocess.run(
                ["dropdb", *common, "--if-exists", "--force", check_db],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )


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
