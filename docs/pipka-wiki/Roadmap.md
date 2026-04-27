#roadmap

# Roadmap

Что закрыто, что в работе, что отложено. Для деталей по каждому пункту — следовать ссылкам.

## ✅ Закрыто (апрель 2026)

### Performance
- Bulk upsert в `JobAggregator.search` — 500+ N+1 SELECT'ов → 3 round-trip'а.
- `pg_insert(...).on_conflict_do_update(...)` во всех JobScore writes (Gemini, Claude, NVIDIA, prefilter).
- Индексы: `ix_jobs_scraped_at`, `ix_jobs_country_scraped`, `ix_job_scores_user_scored_at`, GIN `ix_jobs_merged_sources`.
- JSON → JSONB для `jobs.raw_data` и `ops_events.payload`.
- `statement_timeout=30s`, `lock_timeout=5s` на коннекте → защита от runaway queries.

### Security
- [[Безопасность#3-csrf-double-submit|CSRF middleware]] (double-submit), `secrets.compare_digest`.
- Magic-bytes валидация на resume upload.
- LIKE escape в search.
- [[Rate limiting]] на `/api/jobs/{id}/analyze` (30/час/user).

### Reliability
- Gemini circuit breaker — 3 подряд exhausted → отрубаем до полуночи UTC, fallback на NVIDIA для backfill (см. [[Скоринг#circuit-breaker]]).
- NVIDIA Build как 3-й AI backend (`google/gemma-4-31b-it`), idle rescorer для DE.
- [[Observability#3-sentry|Sentry SDK]] (опциональный, через `SENTRY_DSN`).

### Schema-as-code
- [[Миграции|Bootstrap Alembic]] — две миграции (`0001_baseline`, `0002_phase2_profile_hash`).
- `init_db()` через `alembic upgrade head` вместо `Base.metadata.create_all()`.

### Phase 2 — кэш-инвалидация
- [[Кэш и инвалидация|profile_hash + model_version]] на JobScore.
- UPSERT `WHERE profile_hash != EXCLUDED.profile_hash` — постепенное переоценивание stale-строк, без штормов AI-квоты.

### Refactoring
- `dashboard.py` (750 строк) → 8 файлов по concern'ам (см. [[API]]).
- Per-row `flush+IntegrityError` антипаттерн в Claude `_score_batch` → batch UPSERT.

## 🟡 В работе / следующий приоритет

Ничего не висит. Можно брать что угодно из ⏳.

## ⏳ Отложено (high value, по запросу)

### Production scaling

- **Distributed scheduler lock** — APScheduler in-memory, при `docker compose scale app=2` `_daily_backup` и `_cleanup_old_jobs` выстрелят дважды. Нужно `apscheduler.SQLAlchemyJobStore` + advisory lock в Postgres. Активировать когда пойдём в multi-replica.
- **Redis-backed rate limiter** — текущий [[Rate limiting]] single-process. При multi-replica каждая реплика разрешит свои 30 запросов в час. Миграция: `slowapi` + Redis storage.
- **Read-replica Postgres** — при >1000 пользователей dashboard-запросы (heavy join + sort by score) забьют primary. Stream replication → отдельный engine для читающих ручек.

### AI оптимизация

- **Phase 3 — embeddings + pgvector** — `sentence-transformers` (multilingual-e5-small, бесплатно, локально) → эмбеддинг резюме и вакансий → pre-rank до Gemini → AI получает только top-N кандидатов. Сэкономит 80% AI-вызовов.
- **Phase 2c — proactive invalidation** — endpoint "пере-оценить всё для меня прямо сейчас". Сейчас Phase 2b делает это постепенно через 2-часовой backfill.
- **Per-user AI buckets** — `MAX_JOBS_PER_SCORING_BATCH=8` глобально. При 10+ users в одной транзакции дерутся за квоту. Нужны per-user buckets с приоритезацией (платный → first).

### Observability

- **Prometheus `/metrics`** — `http_requests_total{path,status}`, `gemini_calls_total{result}`, `scan_duration_seconds`. Grafana сверху.
- **Deep healthcheck** — `/health` сейчас всегда 200. Добавить `db_ping`, `last_scan_age_seconds`, `scheduler_alive`.
- **`ops_events` retention** — таблица растёт ~50KB/день, нет ротации. Добавить cron-truncate >30 дней.

### Robustness

- **Telegram `Forbidden` обработка** — если user заблокировал бота, `_score_and_notify` падает. Ловить, ставить `User.is_active=False`.
- **Backup integrity test** — раз в неделю auto-restore последний `*.sql.gz` в throwaway-контейнер, проверка `pg_restore --schema-only`.
- **CI pipeline** — `.github/workflows/ci.yml` с pytest + ruff на каждый push. Сейчас тесты только локально.

### UX / продукт

- **Watchlist scan staggering** — каждые 6 часов все пользователи бомбят источники одновременно. Раскидать по offset'ам.
- **Pre-filter v2 SQL-based** — перенести regex-проверки в `tsvector` + GIN, вместо Python-loop'ов.
- **Email notifications** (помимо Telegram) — для пользователей без TG.

### Cleanup

- **Дроп `user_profiles.industries`** колонки — Industries-фильтр удалили из UI/API (24.04), сама колонка осталась orphan. Migration → drop column.
- **Раздробить `Скоринг.md`** на под-страницы (Gemini / Claude / NVIDIA / Pre-filter / Recheck) — единая страница уже разрослась.
- **`scripts/2026_04_add_hot_path_indexes.sql`** — устаревший, индексы создаются через [[Миграции]].

## Принципы приоритизации

1. **Production blockers > scaling > polish.** Если упало — чиним сразу. Скейлинг — когда подопрёт. Polish — между делом.
2. **Quota-friendly migrations.** Любая инвалидация AI-кэша должна быть постепенной (как Phase 2b), не штормовой.
3. **Observability first.** Перед добавлением новой фичи — убедиться, что её метрики ловятся (хотя бы в [[Ops панель|ops_events]]).

→ [[index]] → [[Архитектура]] → [[Скоринг]] → [[Кэш и инвалидация]]
