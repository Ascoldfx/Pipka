# Pipka — агент поиска работы

Stack: Python 3.12, FastAPI, PostgreSQL 16, asyncpg, APScheduler, Docker Compose.
Repo: github.com/Ascoldfx/Pipka, ветка `main`.
Сервер: Contabo VPS, `root@217.76.61.28`, директория `/opt/pipka`.
SSH ключ: `~/.ssh/id_ed25519`.

## Структура
- `app/` — основной код
  - `api/` — FastAPI роутеры (auth, dashboard, jobs, tracker)
  - `bot/` — Telegram бот (handlers, keyboards, formatters)
  - `models/` — SQLAlchemy модели (User, UserProfile, Job, JobScore, Application, OpsEvent)
  - `schemas/` — Pydantic-схемы
  - `scoring/` — `rules.py` (pre_filter) + `matcher.py` (Claude) + `gemini_matcher.py` (Gemini Flash, backfill)
  - `sources/` — Adzuna, JobSpy, Arbeitnow, Remotive, Arbeitsagentur, Xing, BerlinStartupJobs, WTTJ, Jooble + aggregator
  - `services/` — scheduler, user_service, tracker_service, ops_service, backup_service, job_service
  - `static/` — dashboard.html, infographic.html, js/app.js, css/styles.css
- `alembic/` — миграции (единственный способ менять схему БД)
- `docs/pipka-wiki/` — Obsidian wiki (хранится **в репозитории**)
- `app/config.py` — конфиг из env vars
- `docker-compose.yml` — app + db контейнеры
- `run.py` — точка входа

## Деплой
```bash
# Локально: коммит + пуш
git add <files> && git commit -m "..." && git push origin main

# На сервере:
ssh root@217.76.61.28 -i ~/.ssh/id_ed25519
cd /opt/pipka && git pull && docker compose up -d --build
```
Всегда `--build` — без него Docker использует старый image.

## БД
- PostgreSQL 16, база `pipka`, user `pipka`.
- Миграции **только через Alembic**: `alembic revision --autogenerate -m "..."` → `alembic upgrade head`.
- Запрещено добавлять soft-миграции (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) в `app/database.py` или куда-либо ещё в рантайме.

## Скоринг — текущий backend
- **Реальное время** (`_score_and_notify` → Telegram push): Claude (`claude-sonnet-4-20250514`). Gemini тут НЕ используется — откатили 23.04.2026 из-за 429 Rate Limit.
- **Backfill** (APScheduler, каждые 2ч): Gemini Flash если `GEMINI_API_KEY` задан в `.env`, иначе Claude.
- **Детальный анализ** (`analyze_single_job`, кнопка «AI-анализ»): Gemini Flash если `GEMINI_API_KEY` задан, иначе Claude.

Источник истины по скорингу: [[docs/pipka-wiki/Скоринг.md]].

## Obsidian Wiki — ОБЯЗАТЕЛЬНОЕ ПРАВИЛО

Wiki живёт **в репозитории** по пути `docs/pipka-wiki/` (ранее был внешний vault — упразднён).
После каждого значимого изменения в коде/конфигурации/деплое — обновить соответствующие узлы.

| Файл | Когда обновлять |
|------|----------------|
| `index.md` | Архитектурные изменения, новые разделы |
| `Архитектура.md` | Схема системы, дерево каталогов, стек |
| `База данных.md` | Новые таблицы/колонки (после Alembic-миграции) |
| `Сервисы.md` | Scheduler jobs, tracker, backup, user service, агрегатор |
| `API.md` | Новые/изменённые эндпоинты FastAPI |
| `Источники вакансий.md` | Новый источник, изменение фильтров агрегатора |
| `Скоринг.md` | Pre-filter правила, AI-backend, промпты, бакеты |
| `Настройки.md` | Новые env vars, изменение дефолтов |
| `Changelog YYYY-MM.md` | Каждое значимое изменение — одним блоком под датой |

Правила ведения changelog:
- Один файл на месяц (`Changelog 2026-04.md`, `Changelog 2026-05.md`, …).
- Порядок записей — от ранних к поздним (сверху — старое, снизу — свежее).
- Формат блока: `## DD месяца YYYY` → `### Заголовок фичи` → bullet-list изменений с путями файлов.
- В конце файла — строка перекрёстных ссылок `→ [[…]] → [[…]]`.

Это **не опция** — часть рабочего процесса.
