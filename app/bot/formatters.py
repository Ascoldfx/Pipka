from app.models.job import Job


def format_job_card(job: Job, score: int | None = None, rank: int | None = None) -> str:
    parts = []
    if rank is not None:
        parts.append(f"*#{rank}*")
    if score is not None:
        parts.append(f"{'🟢' if score >= 70 else '🟡' if score >= 40 else '🔴'} *{score}/100*")

    parts.append(f"\n📌 *{_escape_md(job.title)}*")
    parts.append(f"🏢 {_escape_md(job.company_name or 'N/A')}")
    parts.append(f"📍 {_escape_md(job.location or 'N/A')} ({job.country or '?'})")

    if job.salary_min or job.salary_max:
        sal = _salary_str(job.salary_min, job.salary_max, job.salary_currency)
        parts.append(f"💰 {sal}")

    if job.is_remote is True:
        parts.append("🏠 Remote")
    elif job.is_remote is False:
        parts.append("🏢 On-site")

    parts.append(f"📡 {job.source}")

    if job.url:
        parts.append(f"🔗 [Открыть вакансию]({job.url})")

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
    lines = [f"📊 *Pipeline* (всего: {total})\n"]
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


def _escape_md(text: str) -> str:
    for char in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(char, f"\\{char}")
    return text
