"""User profile + resume upload endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select

from app.api._helpers import VALID_WORK_MODES, get_user
from app.api.stats import invalidate_stats_cache
from app.database import async_session
from app.models.user import UserProfile
from app.scoring.profile_hash import compute_profile_hash
from app.scoring.rules import INVALID_EXCLUSION_VALUES
from app.services.embedding_service import invalidate_profile_embedding

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_RESUME_CHARS = 100_000
MAX_RESUME_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Bounds for list/dict-shaped profile fields. Without these, a malicious or
# accidentally-large profile (10 000 ``excluded_keywords``) blows up
# ``compute_profile_hash`` (sha256 over JSON of every entry), the per-job
# pre_filter loop (O(jobs × keywords)), and the watchlist scanner
# (Adzuna call per company × per country).
MAX_PROFILE_LIST = 50  # target_titles, countries, exclusions, target_companies
MAX_PROFILE_FIELD_LEN = 200  # one entry's max length

# Hard wall on parse time. Parsing happens in a resource-bounded subprocess,
# not a thread: cancelling ``asyncio.to_thread`` would leave hostile parser
# code running in the web process after the request timed out.
RESUME_PARSE_TIMEOUT_SECONDS = 30
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ResumeParseError(ValueError):
    """The isolated parser rejected or could not decode the uploaded file."""


async def _parse_resume_isolated(kind: str, content: bytes) -> str:
    """Run the PDF/DOCX parser without passing application secrets to it."""
    parser_env = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.resume_parser",
        kind,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROJECT_ROOT,
        env=parser_env,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(content),
            timeout=RESUME_PARSE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise

    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise ResumeParseError(error or f"Parser exited with status {process.returncode}")
    return stdout.decode("utf-8", errors="replace")


def _parse_country_codes(raw: str, field_name: str) -> list[str]:
    """Validate, normalise, and de-duplicate two-letter country codes."""
    codes: list[str] = []
    for value in raw.split(","):
        code = value.strip().lower()
        if not code:
            continue
        if not re.fullmatch(r"[a-z]{2}", code):
            raise HTTPException(status_code=400, detail=f"{field_name}: invalid country code '{code}'")
        if code not in codes:
            codes.append(code)
    if len(codes) > MAX_PROFILE_LIST:
        raise HTTPException(status_code=400, detail=f"{field_name}: max {MAX_PROFILE_LIST} entries")
    return codes


def _parse_exclusion_list(raw: str, field_name: str) -> list[str]:
    """Normalise exclusions and discard invalid upstream placeholders."""
    items: list[str] = []
    seen: set[str] = set()
    for raw_value in raw.split(","):
        value = raw_value.strip()[:MAX_PROFILE_FIELD_LEN]
        normalised = " ".join(value.casefold().split())
        if not value or normalised in INVALID_EXCLUSION_VALUES or normalised in seen:
            continue
        items.append(value)
        seen.add(normalised)
    if len(items) > MAX_PROFILE_LIST:
        raise HTTPException(status_code=400, detail=f"{field_name}: max {MAX_PROFILE_LIST} entries")
    return items


@router.get("/api/profile")
async def get_profile(request: Request):
    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            return {"error": "No user"}
        p = user.profile
        if not p:
            return {"profile": None}
        return {
            "profile": {
                "resume_text": p.resume_text or "",
                "target_titles": p.target_titles or [],
                "work_mode": p.work_mode or "any",
                "preferred_countries": p.preferred_countries or [],
                "hidden_countries": p.hidden_countries or [],
                "excluded_keywords": p.excluded_keywords or [],
                "excluded_companies": getattr(p, "excluded_companies", None) or [],
                "english_only": getattr(p, "english_only", False) or False,
                "target_companies": getattr(p, "target_companies", None) or [],
            }
        }


@router.post("/api/profile")
async def update_profile(
    request: Request,
    resume_text: str = Form(None),
    target_titles: str = Form(None),
    work_mode: str = Form(None),
    preferred_countries: str = Form(None),
    hidden_countries: str = Form(None),
    excluded_keywords: str = Form(None),
    excluded_companies: str = Form(None),
    english_only: str = Form(None),
    target_companies: str = Form(None),
):
    # Salary / experience / language preferences are intentionally absent:
    # incomplete listing data made them noise rather than reliable signals.
    if resume_text is not None and len(resume_text) > MAX_RESUME_CHARS:
        raise HTTPException(status_code=400, detail=f"Resume too long (>{MAX_RESUME_CHARS} chars)")
    if work_mode is not None and work_mode not in VALID_WORK_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid work_mode. Must be one of: {', '.join(VALID_WORK_MODES)}")

    async with async_session() as session:
        try:
            user = await get_user(request, session)
            if not user:
                raise HTTPException(status_code=401, detail="Login required")

            p = user.profile
            if not p:
                p = UserProfile(user_id=user.id)
                session.add(p)

            old_profile_hash = compute_profile_hash(p)

            if resume_text is not None:
                p.resume_text = resume_text
            if target_titles is not None:
                items = [t.strip()[:MAX_PROFILE_FIELD_LEN] for t in target_titles.split(",") if t.strip()]
                if len(items) > MAX_PROFILE_LIST:
                    raise HTTPException(status_code=400, detail=f"target_titles: max {MAX_PROFILE_LIST} entries")
                p.target_titles = items
            if work_mode is not None:
                p.work_mode = work_mode
            if preferred_countries is not None:
                p.preferred_countries = _parse_country_codes(preferred_countries, "preferred_countries")
            if hidden_countries is not None:
                p.hidden_countries = _parse_country_codes(hidden_countries, "hidden_countries")
            if excluded_keywords is not None:
                p.excluded_keywords = _parse_exclusion_list(excluded_keywords, "excluded_keywords")
            if excluded_companies is not None:
                p.excluded_companies = _parse_exclusion_list(excluded_companies, "excluded_companies")
            if english_only is not None:
                p.english_only = english_only in ("1", "true", "True", "yes", "on")
            if target_companies is not None:
                items = [c.strip()[:MAX_PROFILE_FIELD_LEN] for c in target_companies.split(",") if c.strip()]
                if len(items) > MAX_PROFILE_LIST:
                    raise HTTPException(status_code=400, detail=f"target_companies: max {MAX_PROFILE_LIST} entries")
                p.target_companies = items

            await session.flush()
            # hidden_countries is presentation-only and deliberately excluded
            # from the profile hash, so toggling it does not trigger embeddings
            # or a costly AI re-score.
            if old_profile_hash != compute_profile_hash(p):
                await invalidate_profile_embedding(session, p.id)
            await session.commit()
            invalidate_stats_cache(user.id)
            return {"ok": True}
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("update_profile failed")
            raise HTTPException(status_code=500, detail="Profile update failed")


@router.post("/api/profile/resume")
async def upload_resume(request: Request, file: Annotated[UploadFile, File()]):
    """Upload resume file and extract text (PDF, DOCX, TXT)."""
    # Authenticate before reading or parsing attacker-controlled content.
    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            raise HTTPException(status_code=401, detail="Login required")
        user_id = user.id

    # Stream the upload chunk-by-chunk so a 1 GB blob doesn't OOM the
    # container before the size check fires. ``await file.read()`` would
    # buffer the whole body first — bad on 5k-user prod where someone WILL
    # try a "stresser" upload sooner or later.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_RESUME_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (>10MB)")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)  # 64 KB
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESUME_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (>10MB)")
        chunks.append(chunk)
    content = b"".join(chunks)

    filename = (file.filename or "").lower()
    text = ""

    # Validate magic bytes to prevent extension spoofing
    _MAGIC = {
        ".pdf": b"%PDF",
        ".docx": b"PK\x03\x04",
    }
    if filename.endswith((".pdf", ".docx")):
        ext = ".pdf" if filename.endswith(".pdf") else ".docx"
        if not content[:4].startswith(_MAGIC[ext]):
            raise HTTPException(status_code=400, detail="File content does not match declared format")

    if filename.endswith((".pdf", ".docx")):
        kind = "pdf" if filename.endswith(".pdf") else "docx"
        try:
            text = await _parse_resume_isolated(kind, content)
        except TimeoutError:
            logger.warning(
                "%s parse timeout (>%ds): filename=%s",
                kind.upper(),
                RESUME_PARSE_TIMEOUT_SECONDS,
                filename,
            )
            raise HTTPException(
                status_code=400,
                detail=f"{kind.upper()} parsing exceeded {RESUME_PARSE_TIMEOUT_SECONDS}s — file too complex",
            )
        except ResumeParseError as exc:
            logger.warning("%s parse rejected: filename=%s error=%s", kind.upper(), filename, exc)
            raise HTTPException(status_code=400, detail=f"Could not parse {kind.upper()}")

    elif filename.endswith(".txt"):
        text = content.decode("utf-8", errors="ignore")

    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use PDF, DOCX, or TXT.")

    text = text.replace("\x00", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Could not extract text from file")
    if len(text) > MAX_RESUME_CHARS:
        text = text[:MAX_RESUME_CHARS]

    async with async_session() as session:
        try:
            p = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
            if not p:
                p = UserProfile(user_id=user_id)
                session.add(p)
            p.resume_text = text
            await session.flush()
            await invalidate_profile_embedding(session, p.id)
            await session.commit()
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("upload_resume DB save failed")
            raise HTTPException(status_code=500, detail="Failed to save resume")
    return {"ok": True}
