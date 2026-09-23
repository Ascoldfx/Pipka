# Pipka — агент поиска работы

Stack: Python 3.12, FastAPI, PostgreSQL 16, asyncpg, APScheduler, Docker Compose.
Repo: github.com/Ascoldfx/Pipka, ветка `main`.
Сервер: Contabo VPS, `pipkaops@217.76.61.28` (sudo), директория `/opt/pipka`.
SSH ключ: `~/.ssh/id_ed25519`.

**Единственная локальная копия проекта: `~/клод джоб/Pipka`** (она же vault Obsidian, вики — `docs/pipka-wiki/`). Других клонов, снимков сервера и `sync`-скриптов нет — не создавать. Сервер `/opt/pipka` = коммит `main` без локальных правок; не деплоить копированием файлов.

## Структура
- `app/` — основной код
  - `api/` — 11 FastAPI роутеров (auth, health, pages, jobs, stats, profile, scan, ops, admin, billing, feedback)
  - `bot/` — Telegram бот (handlers, keyboards, formatters)
  - `models/` — SQLAlchemy модели (User, UserProfile, PaymentTransaction, UserFeedback, Job, JobScore, Application, ApplicationHistory, SearchSubscription, OpsEvent)
  - `schemas/` — Pydantic-схемы
  - `scoring/` — `rules.py` (pre_filter) + `matcher.py` (промпт + маршрутизатор) + `gemini_matcher.py` (Gemini) + `gemini_client.py` (Google GenAI SDK) + `nvidia_matcher.py` (NVIDIA fallback) + `nvidia_embedding_client.py` + `profile_hash.py`
  - `sources/` — Adzuna, JobSpy, Arbeitnow, Remotive, Arbeitsagentur, Xing, BerlinStartupJobs, WTTJ, Jooble, BuiltIn, Gupy + Watchlist + aggregator
  - `services/` — scheduler, user_scope_service (мульти-юзер очереди), billing_service, embedding_service, resume_parser, url_checker, user_service, tracker_service, ops_service, backup_service, job_service
  - `static/` — dashboard.html, infographic.html, js/security.js, js/events.js, css/styles.css
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
ssh pipkaops@217.76.61.28 -i ~/.ssh/id_ed25519
cd /opt/pipka && sudo git pull && sudo docker compose up -d --build
```
Всегда `--build` — без него Docker использует старый image.

Перед пушем прогнать то же, что CI: `.venv/bin/ruff check --isolated --select F,E9 app tests alembic`, `.venv/bin/python -m pytest -q`, `python scripts/check_secrets.py`, `node scripts/check_inline_js.mjs`. Локальный `.venv` — Python 3.12, собирается как в Dockerfile:
`python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pip install --no-deps markdownify==0.14.1` (JobSpy тянет уязвимую 0.13.x).

Если GitHub не принимает пуш, доставить коммиты на сервер напрямую:
```bash
git push --receive-pack="sudo -n git-receive-pack" ssh://pipkaops@217.76.61.28/opt/pipka main:refs/heads/sync-YYYYMMDD
# на сервере: sudo git reset --hard sync-YYYYMMDD && sudo git branch -D sync-YYYYMMDD && sudo docker compose up -d --build
```

## БД
- PostgreSQL 16, база `pipka`, user `pipka`.
- Миграции **только через Alembic**: `alembic revision --autogenerate -m "..."` → `alembic upgrade head`.
- Запрещено добавлять soft-миграции (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) в `app/database.py` или куда-либо ещё в рантайме.

## Скоринг — текущий backend
- **Реальное время** (`_score_and_notify` → Telegram push): Gemini `gemini-3.6-flash` (лимит `GEMINI_DAILY_REQUEST_LIMIT=20`/сутки); при breaker/исчерпании квоты → NVIDIA `poolside/laguna-xs-2.1` (streaming, по 1 вакансии). Списывает 1 кредит за AI-оценку (админы бесплатно).
- **Backfill** (APScheduler, каждые 2ч): Gemini `gemini-3.6-flash` → NVIDIA при открытом breaker. Пользователи обходятся round-robin. Claude полностью удалён из кода 07.08.2026.
- **Детальный анализ** (`analyze_single_job`, кнопка «AI-анализ»): Gemini `gemini-3.6-flash` только при `GEMINI_DETAILED_ANALYSIS_ENABLED=true` (по умолчанию выключено ради квоты); иначе NVIDIA.
- **Embeddings:** NVIDIA `nvidia/nemotron-3-embed-1b`, 2048-мерные, pgvector.
- Google SDK: только `google-genai`; legacy `google-generativeai` не использовать.

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
