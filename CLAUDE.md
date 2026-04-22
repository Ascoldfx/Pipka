# Pipka — агент поиска работы

Stack: Python 3.12, FastAPI, PostgreSQL 16, asyncpg, APScheduler, Docker Compose.
Repo: github.com/Ascoldfx/Pipka, ветка `main`.
Сервер: Contabo VPS, `root@217.76.61.28`, директория `/opt/pipka`.
SSH ключ: `~/.ssh/id_ed25519`.

## Структура
- `app/` — основной код
  - `api/` — FastAPI роутеры (dashboard, auth, jobs, tracker)
  - `models/` — SQLAlchemy модели (User, UserProfile, Job, JobScore, Application)
  - `scoring/` — pre_filter (rules.py) + Claude AI matcher (matcher.py)
  - `sources/` — Adzuna, JobSpy, Arbeitnow, Remotive + aggregator
  - `services/` — scheduler, user_service, tracker_service
- `app/database.py` — soft migrations (IF NOT EXISTS, без Alembic)
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
- PostgreSQL 16, база `pipka`, user `pipka`
- Для миграций схемы БД используется **Alembic**. Запрещено использовать жесткие soft-миграции в коде (в `app/database.py`).
- Обязательно генерировать и применять миграции (`alembic revision --autogenerate`, `alembic upgrade head`) при любом изменении схемы.

## Obsidian Wiki — ОБЯЗАТЕЛЬНОЕ ПРАВИЛО
После каждого значимого изменения в коде/конфигурации/деплое — **обновить соответствующие узлы wiki**.

Vault: `/Users/antongotskyi/клод джоб/Pipka/graphify-out/pipka-wiki/`

| Узел | Когда обновлять |
|------|----------------|
| `index.md` | Любые архитектурные изменения |
| `1. Architecture/config.md` | Новые env vars, изменение дефолтов |
| `1. Architecture/db.md` | Новые таблицы/колонки, миграции |
| `2. API & Services/models.md` | Изменения SQLAlchemy моделей |
| `2. API & Services/routes.md` | Новые/изменённые API эндпоинты |
| `2. API & Services/services.md` | Изменения бизнес-логики, scheduler |
| `3. Scrapers & Bot/sources.md` | Фильтры, источники, агрегатор |
| `3. Scrapers & Bot/bot.md` | Telegram бот |
| `4. Changelogs/` | Каждое значимое изменение — в changelog |
| `5. Deployment & DevOps/runbook.md` | Изменения деплоя, инфраструктуры |

Это **не опция** — часть рабочего процесса.
