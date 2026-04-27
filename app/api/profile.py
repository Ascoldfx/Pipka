"""User profile + resume upload endpoints."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.api._helpers import VALID_WORK_MODES, get_user
from app.api.stats import invalidate_stats_cache
from app.database import async_session
from app.models.user import UserProfile

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_RESUME_CHARS = 100_000
MAX_RESUME_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("/api/profile")
async def get_profile(request: Request):
    async with async_session() as session:
        user = await get_user(request, session)
        if not user:
            return {"error": "No user"}
        p = user.profile
        if not p:
            return {"profile": None}
        return {"profile": {
            "resume_text": p.resume_text or "",
            "target_titles": p.target_titles or [],
            "min_salary": p.min_salary,
            "max_commute_km": p.max_commute_km,
            "languages": p.languages or {},
            "experience_years": p.experience_years,
            "work_mode": p.work_mode or "any",
            "preferred_countries": p.preferred_countries or [],
            "excluded_keywords": p.excluded_keywords or [],
            "english_only": getattr(p, "english_only", False) or False,
            "target_companies": getattr(p, "target_companies", None) or [],
        }}


@router.post("/api/profile")
async def update_profile(
    request: Request,
    resume_text: str = Form(None),
    target_titles: str = Form(None),
    min_salary: int = Form(None),
    languages: str = Form(None),
    experience_years: int = Form(None),
    work_mode: str = Form(None),
    preferred_countries: str = Form(None),
    excluded_keywords: str = Form(None),
    english_only: str = Form(None),
    target_companies: str = Form(None),
):
    if resume_text is not None and len(resume_text) > MAX_RESUME_CHARS:
        raise HTTPException(status_code=400, detail=f"Resume too long (>{MAX_RESUME_CHARS} chars)")
    if min_salary is not None and not (0 <= min_salary <= 1_000_000):
        raise HTTPException(status_code=400, detail="min_salary out of range")
    if experience_years is not None and not (0 <= experience_years <= 80):
        raise HTTPException(status_code=400, detail="experience_years out of range")
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

            if resume_text is not None:
                p.resume_text = resume_text
            if target_titles is not None:
                p.target_titles = [t.strip() for t in target_titles.split(",") if t.strip()]
            if min_salary is not None:
                p.min_salary = min_salary
            if languages is not None:
                # Accept either JSON ({"en":"C1","de":"B1"}) or shorthand "en:C1,de:B1".
                try:
                    p.languages = json.loads(languages)
                except json.JSONDecodeError:
                    langs: dict[str, str] = {}
                    for part in languages.split(","):
                        if ":" in part:
                            k, v = part.split(":", 1)
                            langs[k.strip()] = v.strip()
                    p.languages = langs
            if experience_years is not None:
                p.experience_years = experience_years
            if work_mode is not None:
                p.work_mode = work_mode
            if preferred_countries is not None:
                p.preferred_countries = [c.strip().lower() for c in preferred_countries.split(",") if c.strip()]
            if excluded_keywords is not None:
                p.excluded_keywords = [k.strip() for k in excluded_keywords.split(",") if k.strip()]
            if english_only is not None:
                p.english_only = english_only in ("1", "true", "True", "yes", "on")
            if target_companies is not None:
                p.target_companies = [c.strip() for c in target_companies.split(",") if c.strip()]

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
async def upload_resume(request: Request, file: UploadFile = File(...)):
    """Upload resume file and extract text (PDF, DOCX, TXT)."""
    content = await file.read()
    if len(content) > MAX_RESUME_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (>10MB)")

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

    if filename.endswith(".pdf"):
        try:
            import io
            from pdfminer.high_level import extract_text as pdf_extract
            text = pdf_extract(io.BytesIO(content))
        except Exception:
            logger.exception("PDF parse failed: filename=%s", filename)
            raise HTTPException(status_code=400, detail="Could not parse PDF")

    elif filename.endswith(".docx"):
        try:
            import io
            import zipfile
            import xml.etree.ElementTree as ET
            zf = zipfile.ZipFile(io.BytesIO(content))
            xml_content = zf.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                texts = [t.text for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if t.text]
                if texts:
                    paragraphs.append("".join(texts))
            text = "\n".join(paragraphs)
        except Exception:
            logger.exception("DOCX parse failed: filename=%s", filename)
            raise HTTPException(status_code=400, detail="Could not parse DOCX")

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
            user = await get_user(request, session)
            if not user:
                raise HTTPException(status_code=401, detail="Login required")
            p = user.profile
            if not p:
                p = UserProfile(user_id=user.id)
                session.add(p)
            p.resume_text = text
            await session.commit()
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("upload_resume DB save failed")
            raise HTTPException(status_code=500, detail="Failed to save resume")
    return {"ok": True}
