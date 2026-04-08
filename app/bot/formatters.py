from app.models.job import Job


def format_job_card(job: Job, score: int | None = None, rank: int | None = None) -> str:
    """Format job card as plain text (no Markdown to avoid escaping issues)."""
    parts = []
    if rank is not None:
        parts.append(f"#{rank}")
    if score is not None:
        emoji = '🟢' if score >= 70 else '🟡' if score >= 40 else '🔴'
        parts.append(f"{emoji} {score}/100")

    parts.append(f"\n📌 {job.title}")
    parts.append(f"🏢 {job.company_name or 'N/A'}")
    parts.append(f"📍 {job.location or 'N/A'} ({job.country or '?'})")

    if job.salary_min or job.salary_max:
        sal = _salary_str(job.salary_min, job.salary_max, job.salary_currency)
        parts.append(f"💰 {sal}")

    if job.is_remote is True:
        parts.append("🏠 Remote")
    elif job.is_remote is False:
        parts.append("🏢 On-site")

    parts.append(f"📡 {job.source}")

    if job.url:
        parts.append(f"🔗 {job.url}")

    return "\n".join(parts)


def format_stats(stats: dict[str, int]) -> str:
    icons = {
        "saved": "💾",
        "applied": "📝",
        "interviewing": "🗣",
        "offer": "🎉",
        "rejected": "❌",
        "withdrawn": "🚫",
    }
    total = sum(stats.values())
    lines = [f"📊 Pipeline (всего: {total})\n"]
    for status, count in stats.items():
        icon = icons.get(status, "•")
        lines.append(f"{icon} {status.capitalize()}: {count}")
    return "\n".join(lines)


def _salary_str(min_sal: float | None, max_sal: float | None, currency: str | None) -> str:
    cur = currency or "EUR"
    if min_sal and max_sal:
        return f"{int(min_sal):,}–{int(max_sal):,} {cur}"
    if min_sal:
        return f"от {int(min_sal):,} {cur}"
    if max_sal:
        return f"до {int(max_sal):,} {cur}"
    return "не указана"
