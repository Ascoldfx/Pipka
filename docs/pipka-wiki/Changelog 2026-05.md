#changelog

# Changelog — Май 2026

## 1 мая 2026

### URL liveness check (Level 1)

Появилась проблема: `_cleanup_old_jobs` удалял вакансии по возрасту (>45 дней), но не по факту закрытия. Закрытая через неделю болталась в инбоксе ещё месяц.

**Новый сервис `app/services/url_checker.py`** — асинхронный HEAD-классификатор:

- 2xx → `active`
- 404/410 → `closed`
- 3xx с редиректом на корень / `/jobs` / `/search` без id-токена → `closed` (типичный паттерн "вакансия снята")
- 401/403 → `active` (auth-wall, страница есть)
- 405 → fallback на GET, повторная классификация
- 5xx / network → `unreachable`, transient — после `url_check_max_failures` (3) подряд флипается на `unreachable`

**Concurrency**: process-wide `Semaphore(10)` + per-host async lock с pacer'ом `url_check_per_host_delay=1.5s`. Критично для LinkedIn / Indeed которые captcha-блокируют burst HEAD-запросы.

**Миграция `0003_job_url_status.py`** — три колонки на `jobs` (`url_status` VARCHAR(20), `url_checked_at` TIMESTAMP, `url_check_failures` INTEGER) + composite-индекс `ix_jobs_url_status_checked (url_status, url_checked_at)` для picker'а и фильтра "скрыть закрытые".

**Scheduler**: cron 04:00 UTC daily, picks 500 oldest-checked jobs (NULL first). OpsEvent `url_check` с `{checked, active, closed, unreachable, skipped}` в payload.

**API**: `GET /api/jobs?include_closed=0` (default) скрывает `url_status='closed'`. Поля `url_status` / `url_checked_at` добавлены в JSON-ответ.

**UI**: чекбокс "Show closed" в тулбаре фильтров (i18n EN/RU/DE/ES). Закрытые строки получают opacity 0.55 + line-through на title + красный бейдж 🚫. Unreachable — жёлтый ⚠ без затемнения (soft signal).

**Первый прогон** обнаружил **162 закрытых из 500 проверенных (32%)** — масштаб проблемы подтвердился.

См. [[Проверка ссылок]].

---

## 2 мая 2026

### Phase 3 — full-text search + pgvector embeddings

Retrieval-слой перед AI-скорингом. Цель — быстро находить релевантные вакансии в БД и постепенно уменьшать количество дорогих LLM-вызовов.

**Миграция `0004_search_embeddings.py`** (PostgreSQL-only):

- `CREATE EXTENSION IF NOT EXISTS vector` — pgvector. Docker DB image переключён с `postgres:16-alpine` на `pgvector/pgvector:pg16`.
- `jobs.search_vector` — generated `tsvector` поверх title (вес A) + company_name (A) + description (B). GIN-индекс `ix_jobs_search_vector`.
- `jobs.embedding vector(768)` + `embedding_model` + `embedding_updated_at`. HNSW cosine-индекс `ix_jobs_embedding_hnsw`.
- Аналогичные колонки в `user_profiles` + `ix_profiles_embedding_hnsw`.

**Новый сервис `app/services/embedding_service.py`** — Gemini Embedding API (`models/gemini-embedding-001`, dim=768) с RPD-friendly батчингом.

**Scheduler job `embed_index`**: каждые 2 часа + startup +90с. Заполняет до `embedding_jobs_per_run=70` вакансий и `embedding_profiles_per_run=20` профилей за тик. Под 1k RPD дневной лимит free tier'а.

**API**: `GET /api/jobs?semantic=1` использует cosine-similarity между профильным и job-embedding'ами для pre-rank top-`SEMANTIC_SEARCH_LIMIT` (default 500) кандидатов. `?search=…` на PG переехал с ILIKE на tsvector + `websearch_to_tsquery('simple', term)`.

См. [[Поиск и индексация]].

### URL check drain-mode

`_check_job_urls` переписан: вместо одного пасса теперь крутит `run_url_check_pass` в цикле до пустой очереди или до 3-часового wall-clock-бюджета. На steady-state — 1 короткий пасс. После большого импорта (например, 7000 NULL-новеньких) — 4 пасса по 2000 = ~2 часа, заканчивается к 06:00 UTC = 08:00 Berlin до утренней работы пользователя.

OpsEvent payload теперь включает `passes` count.

---

→ [[Changelog 2026-04]] → [[Roadmap]] → [[Архитектура]] → [[Скоринг]] → [[Поиск и индексация]] → [[Проверка ссылок]]
