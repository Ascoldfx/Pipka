import gzip
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import backup_service


def test_database_url_parser_decodes_credentials_without_leaking_them():
    parsed = backup_service._parse_db_url(
        "postgresql+asyncpg://pipka:p%40ss%3Aword@db:5433/pipka"
    )
    assert parsed == {
        "user": "pipka",
        "password": "p@ss:word",
        "host": "db",
        "port": "5433",
        "dbname": "pipka",
    }

    with pytest.raises(ValueError, match="Cannot parse PostgreSQL DATABASE_URL") as exc:
        backup_service._parse_db_url("not-a-url-with-secret")
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_backup_is_written_atomically_off_the_event_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(
        backup_service.settings,
        "database_url",
        "postgresql+asyncpg://pipka:secret@db:5432/pipka",
    )
    monkeypatch.setattr(backup_service.settings, "b2_key_id", "")
    monkeypatch.setattr(
        backup_service,
        "_assert_postgres_client_matches_server",
        lambda db, env: None,
    )

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=b"-- PostgreSQL database dump\nCREATE TABLE jobs (id integer);\n",
            stderr=b"",
        )

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)

    result = Path(await backup_service.run_backup())

    assert result.exists()
    assert not list(tmp_path.glob("*.tmp"))
    with gzip.open(result, "rb") as stream:
        assert b"CREATE TABLE jobs" in stream.read()


@pytest.mark.asyncio
async def test_latest_backup_is_selected_for_restore_check(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUP_DIR", tmp_path)
    older = tmp_path / "pipka_20260801_010000.sql.gz"
    newest = tmp_path / "pipka_20260802_010000.sql.gz"
    older.write_bytes(b"old")
    newest.write_bytes(b"new")
    checked = []
    monkeypatch.setattr(backup_service, "_verify_backup_restore", checked.append)

    result = await backup_service.verify_latest_backup_restore()

    assert Path(result) == newest
    assert checked == [newest]


def test_backup_rejects_postgres_client_server_major_mismatch(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout=b"pg_dump (PostgreSQL) 17.6 (Debian 17.6-1)\n",
                stderr=b"",
            ),
            SimpleNamespace(returncode=0, stdout=b"160010\n", stderr=b""),
        ]
    )
    monkeypatch.setattr(backup_service.subprocess, "run", lambda *args, **kwargs: next(responses))

    with pytest.raises(RuntimeError, match=r"major mismatch.*\(17 vs 16\)"):
        backup_service._assert_postgres_client_matches_server(
            {"host": "db", "port": "5432", "user": "pipka", "dbname": "pipka"},
            {"PGPASSWORD": "not-logged"},
        )


def test_backup_accepts_matching_postgres_major(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout=b"pg_dump (PostgreSQL) 16.10 (Debian 16.10-1)\n",
                stderr=b"",
            ),
            SimpleNamespace(returncode=0, stdout=b"160010\n", stderr=b""),
        ]
    )
    monkeypatch.setattr(backup_service.subprocess, "run", lambda *args, **kwargs: next(responses))

    backup_service._assert_postgres_client_matches_server(
        {"host": "db", "port": "5432", "user": "pipka", "dbname": "pipka"},
        {"PGPASSWORD": "not-logged"},
    )
