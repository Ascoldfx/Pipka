# Pipka — AI Job Search Platform

> Автоматический агрегатор вакансий с AI-скорингом для поиска работы уровня Director/VP/Head в DACH+.

## Архитектура и данные

- [[Архитектура]] — общая схема системы, потоки, файловое дерево
- [[База данных]] — все таблицы и связи
- [[API]] — все эндпоинты (8 роутеров)
- [[Сервисы]] — бизнес-логика, scheduler jobs

## Источники и обработка

- [[Источники вакансий]] — 9 источников: Adzuna, JobSpy, Arbeitnow, Remotive, Arbeitsagentur, Xing, BerlinStartupJobs, WTTJ, Jooble + Watchlist
- [[Дедупликация]] — exact (sha256) + fuzzy (title + company subset) + merged_sources
- [[Поиск и индексация]] — tsvector + GIN, pgvector + Gemini embeddings
- [[Скоринг]] — pre-filter rules + 3 AI backend'а (Gemini, Claude, NVIDIA)
- [[Кэш и инвалидация]] — profile_hash + model_version (Phase 2)
- [[Проверка ссылок]] — daily HEAD-ping для скрытия закрытых вакансий

## Интерфейсы

- [[Telegram-бот]] — handlers, keyboards, push-уведомления
- [[Трекер]] — applied/rejected/saved/interviewing/offer + auto-exclude

## Эксплуатация

- [[Деплой]] — сервер, Docker, цикл выкатки
- [[Миграции]] — Alembic, baseline + Phase 2
- [[Бэкапы]] — pg_dump → gzip → local + Backblaze B2
- [[Настройки]] — все env-переменные

## Безопасность и наблюдаемость

- [[Auth]] — Google OAuth + сессии, гостевой режим
- [[Безопасность]] — CSRF, валидация input, DB hardening
- [[Rate limiting]] — sliding-window per user
- [[Observability]] — logging, OpsEvent, Sentry
- [[Ops панель]] — `/api/ops/*` для админа

## Разработка

- [[Тесты]] — pytest + ruff (точечное покрытие)
- [[Roadmap]] — что сделано, что в очереди

## Changelog

- [[Changelog 2026-04]] — последние значимые изменения

## Стек

| Слой | Технология |
|------|-----------|
| Backend | FastAPI + Python 3.12 |
| БД | PostgreSQL 16 (asyncpg, JSONB) |
| ORM | SQLAlchemy 2.0 async |
| Migrations | Alembic |
| AI | Gemini Flash · Claude Sonnet · NVIDIA Gemma |
| Bot | python-telegram-bot 21 |
| Scheduler | APScheduler 3 |
| Auth | Google OAuth2 (authlib) + SessionMiddleware |
| Server | Contabo VPS, Ubuntu 24.04, Docker Compose |
| Domain | pipka.net (Cloudflare proxy) |
| Observability | OpsEvent (DB) + Sentry SDK (опционально) |

## Быстрые ссылки

- Прод: https://pipka.net
- GitHub: https://github.com/Ascoldfx/Pipka
- Сервер: `ssh root@217.76.61.28` (ключ `~/.ssh/id_ed25519`)
- Каталог: `/opt/pipka`
