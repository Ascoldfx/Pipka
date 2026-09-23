#index

# Pipka — AI Job Search Platform

> Текущий production-снимок, логика очередей и реестр доступов: [[Текущее состояние и доступы]] (проверено 23.09.2026).

> Мульти-пользовательский агрегатор вакансий с AI-скорингом для поиска работы уровня Director/VP/Head: Европа (DACH+), а также opt-in регионы Gulf, Океания/ЮВА и Бразилия. Монетизация — кредиты за AI-оценки.

## Архитектура и данные

- [[Архитектура]] — общая схема системы, потоки, файловое дерево
- [[База данных]] — все таблицы и связи
- [[API]] — все эндпоинты (11 роутеров)
- [[Сервисы]] — бизнес-логика, scheduler jobs

## Источники и обработка

- [[Источники вакансий]] — 11 источников: Adzuna, JobSpy, Arbeitnow, Remotive, Arbeitsagentur, Xing, BerlinStartupJobs, WTTJ, Jooble, BuiltIn, Gupy (Бразилия) + Watchlist
- [[Мульти-юзер очереди]] — справедливое round-robin слияние поиска и скоринга между пользователями
- [[Дедупликация]] — exact (sha256) + fuzzy (title + company subset) + merged_sources
- [[Поиск и индексация]] — tsvector + GIN, pgvector + NVIDIA Nemotron embeddings
- [[Watchlist]] — точечный 6-часовой scan по `target_companies`
- [[Скоринг]] — pre-filter rules + 2 AI backend'а (Gemini, NVIDIA)
- [[Pre-filter правила]] — keyword lists, buckets, language detection
- [[Кэш и инвалидация]] — profile_hash + model_version (Phase 2)
- [[Проверка ссылок]] — daily HEAD-ping для скрытия закрытых вакансий

## Интерфейсы

- [[Frontend]] — SPA структура, fetch wrapper, i18n EN/RU/DE/ES, dark mode
- [[Telegram-бот]] — handlers, keyboards, push-уведомления
- [[Трекер]] — applied/rejected/saved/interviewing/offer + auto-exclude
- [[Resume parsing]] — загрузка резюме PDF/DOCX/TXT в изолированном процессе
- [[Онбординг и обратная связь]] — welcome-модалка, форма отзывов с уведомлением в Telegram

## Монетизация

- [[Биллинг и кредиты]] — 1 кредит = 1 AI-оценка, пакеты starter/pro, оплата Cryptomus

## Эксплуатация

- [[Деплой]] — сервер, Docker, цикл выкатки
- [[Миграции]] — Alembic, production head `0013_application_identity`
- [[Бэкапы]] — pg_dump → gzip → local + Backblaze B2
- [[Настройки]] — все env-переменные

## Безопасность и наблюдаемость

- [[Auth]] — Google OAuth + сессии, гостевой режим
- [[Безопасность]] — CSRF, валидация input, DB hardening
- [[Rate limiting]] — sliding-window per user
- [[Observability]] — logging, OpsEvent, Sentry
- [[Ops панель]] — `/api/ops/*` для админа

## Разработка

- [[Тесты]] — pytest + ruff (unit/integration, с отмеченными пробелами)
- [[Roadmap]] — что сделано, что в очереди

## Changelog

- [[Changelog 2026-09]] — NVIDIA streaming fallback, модель scoring, Ops security-probe filtering и состояние production
- [[Changelog 2026-08]] — security audit, фильтры, AI-cache, географический dedup, backup restore-test
- [[Changelog 2026-07]] — Gemini 3.5/3.6, скрытые страны, удаление salary-фильтра, регионы Gulf/APAC, Бразилия
- [[Changelog 2026-06]] — фикс падения скана (tz-aware даты)
- [[Changelog 2026-05]] — URL liveness, full-text search, pgvector embeddings
- [[Changelog 2026-04]] — wiki expansion, Phase 2 (profile_hash), CSRF, Sentry, dashboard refactor, JSONB

## Стек

| Слой | Технология |
|------|-----------|
| Backend | FastAPI + Python 3.12 |
| БД | PostgreSQL 16 (asyncpg, JSONB) |
| ORM | SQLAlchemy 2.0 async |
| Migrations | Alembic |
| AI | Gemini 3.6 Flash · NVIDIA Poolside Laguna XS fallback · Nemotron embeddings |
| Bot | python-telegram-bot 21 |
| Scheduler | APScheduler 3 |
| Auth | Google OAuth2 (authlib) + SessionMiddleware |
| Server | Contabo VPS, Ubuntu 24.04, Docker Compose |
| Domain | pipka.net (Cloudflare proxy) |
| Observability | OpsEvent (DB) + Sentry SDK (опционально) |

## Быстрые ссылки

- Прод: https://pipka.net
- GitHub: https://github.com/Ascoldfx/Pipka
- Сервер: `ssh pipkaops@217.76.61.28` (ключ `~/.ssh/id_ed25519`, далее `sudo`)
- Каталог: `/opt/pipka`
