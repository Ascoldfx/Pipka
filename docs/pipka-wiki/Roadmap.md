#roadmap

# Roadmap

Что закрыто, что в работе, что отложено. Для деталей по каждому пункту — следовать ссылкам.

## ✅ Закрыто (апрель 2026)

### Performance
- Bulk upsert в `JobAggregator.search` — 500+ N+1 SELECT'ов → 3 round-trip'а.
- `pg_insert(...).on_conflict_do_update(...)` во всех JobScore writes (Gemini, NVIDIA, prefilter).
- Индексы: `ix_jobs_scraped_at`, `ix_jobs_country_scraped`, `ix_job_scores_user_scored_at`, GIN `ix_jobs_merged_sources`.
- JSON → JSONB для `jobs.raw_data` и `ops_events.payload`.
- `statement_timeout=30s`, `lock_timeout=5s` на коннекте → защита от runaway queries.

### Security
- [[Безопасность#3. CSRF (double-submit)|CSRF middleware]] (double-submit), `secrets.compare_digest`.
- Magic-bytes валидация на resume upload.
- LIKE escape в search.
- [[Rate limiting]] на `/api/jobs/{id}/analyze` (30/час/user).

### Reliability
- Gemini circuit breaker — 3 подряд exhausted → отрубаем до полуночи UTC, fallback на NVIDIA для backfill (см. [[Скоринг]]).
- NVIDIA chat fallback со streaming по одной вакансии (`poolside/laguna-xs-2.1`); idle rescorer остаётся отдельным opt-in.
- Multi-user job pipeline: поисковые планы строятся из `preferred_countries` и целевых ролей каждого активного пользователя; provider queries объединяются для общей дедупликации, scoring/backfill идут round-robin с приоритетом свежих вакансий.
- Embedding index scope объединяет рынки всех активных пользователей; burst worker чаще индексирует очередь, если она выше порога.
- [[Observability#3. Sentry|Sentry SDK]] (опциональный, через `SENTRY_DSN`).

### Schema-as-code
- [[Миграции|Bootstrap Alembic]] — две миграции (`0001_baseline`, `0002_phase2_profile_hash`).
- `init_db()` через `alembic upgrade head` вместо `Base.metadata.create_all()`.

### Phase 2 — кэш-инвалидация
- [[Кэш и инвалидация|profile_hash + model_version]] на JobScore.
- UPSERT `WHERE profile_hash != EXCLUDED.profile_hash` — постепенное переоценивание stale-строк, без штормов AI-квоты.

### URL liveness
- [[Проверка ссылок|Daily HEAD-ping]] (cron 04:00 UTC, drain-loop до пустой очереди).
- Закрытые скрываются из инбокса по `?include_closed=0` дефолту, бейдж 🚫 в карточке.
- На первом проходе (1 мая 2026) выявлено ~32% реально снятых вакансий — подтвердило масштаб проблемы.

### Phase 3 — full-text search + embeddings
- `jobs.search_vector` (generated tsvector + GIN). `?search=` через `websearch_to_tsquery('simple', term)`.
- `pgvector` extension + `jobs.embedding vector(2048)` + `user_profiles.embedding`; HNSW не используется из-за ограничения размерности, поиск — exact cosine.
- NVIDIA Nemotron embeddings для индексации (`embed_index` каждые 2ч, до 70 вакансий; burst каждые 30 мин при очереди >100). Статус/детали — [[Поиск и индексация]].
- `?semantic=1` опция в `/api/jobs` — pre-rank по cosine-similarity к profile-embedding.
- Подробнее — [[Поиск и индексация]].

### Day-1 security hardening (5 мая 2026, перед prod-релизом)

Pre-launch блокеры из глубокого аудита:

- **Stale admin role** — `require_admin_async` + per-user TTL-cache 60s проверяет роль в БД. Sync-вариант помечен deprecated.
- **Logout CSRF** — `GET /auth/logout` → `POST /auth/logout`, требует X-CSRF-Token. Frontend через `fetch('POST')`.
- **Security headers** — HSTS, CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, Permissions-Policy. См. [[Безопасность]].
- **Resume upload OOM** — buffered `await file.read()` → стрим 64KB-чанками с running counter и Content-Length pre-check. 1GB upload больше не сажает контейнер.
- **DOCX zip-bomb** — `ZipInfo.file_size` проверка до `read()`, лимит 8 MB uncompressed.
- **Session fixation** — `request.session.clear()` перед записью identity в OAuth callback.

### Day-2 security hardening (6 мая 2026)

High-severity пункты из аудита (severity 6-7):

- **TrustedHostMiddleware** outermost — отбивает forged `Host:` headers до session-state allocation. Allowed: `pipka.net`, `*.pipka.net`, `localhost`, `127.0.0.1`.
- **Per-IP rate-limit middleware** — sliding-window per IP, three buckets; IP только из sanitized `X-Real-IP` от loopback nginx или socket. См. [[Rate limiting]].
- **`?search=` length cap** — `Query(None, max_length=200)` против 1MB substring-attack под `statement_timeout=30s`.
- **Profile-list size limits** — 50 entries × 200 chars per list (`target_titles`/`preferred_countries`/`excluded_keywords`/`target_companies`); 20-key dict с 50-char key/value для `languages`. JSON-bomb path закрыт.
- **Sentry PII filter** — `_sentry_before_send` рекурсивно scrub'ит 13 ключей (resume_text, email, telegram_id, google_sub, csrf_token, ...) из stack-frame locals, breadcrumb data, request headers/body, extra context. См. [[Observability#3. Sentry]] и [[Безопасность]].

### Day-3 security hardening (7 мая 2026)

Medium-severity (4-5):

- **jsq HTML-escape combo** — `"`, `&`, `<`, `>` плюс старые `\` и `'`. Admin-таблица с именами не XSS-вектор.
- **PDF/DOCX parse timeout 30s** — `asyncio.wait_for(asyncio.to_thread(...), timeout=30)`. Inf-loop'ы в font metrics / XML-bomb отбиваются.
- **Telegram Forbidden auto-deactivate** — блокировка user'а → `telegram_id=None` + OpsEvent. Одна блокировка не роняет весь push-цикл.
- **Inactive user session clear** — `get_session_user` дропает stale cookie если user удалён.
- **ON DELETE CASCADE** на 7 FK (миграция `0005_cascade_fks.py`). Cleanup-job'ы больше не зависят от ручного порядка delete'ов.
- **Validate job_id** в `/api/jobs/{id}/action` — 404 вместо FK-500.

### Refactoring
- `dashboard.py` (750 строк) → 8 файлов по concern'ам (см. [[API]]).
- Per-row `flush+IntegrityError` pattern in a retired scoring backend заменён batch UPSERT.

### Июль 2026 — профиль и AI

- Google SDK `google-generativeai` → `google-genai`; Gemini 3.5 Flash-Lite для массового скоринга, Gemini 3.6 Flash для анализа.
- Structured JSON Schema для Gemini batch scoring.
- Зарплата полностью удалена как preference/scoring signal.
- `hidden_countries`: постоянное скрытие стран из основной ленты без остановки сбора и скоринга.
- Alembic head `0009_geographic_dedup_hash`; semantic hard-reject удалён, geographic dedup применён к production.

## 🟡 В работе / следующий приоритет

### P0 — оставшиеся production-риски

1. Проверить ротацию NVIDIA API key, попавшего на screenshot 22.09.2026; при совпадении немедленно отозвать у provider и заменить production secret. Проверить, нет ли других опубликованных действующих ключей; значения в wiki не сохранять.
2. Production registration сейчас открыта из code default. Подтвердить желаемую политику; если доступ должен быть invite-only — выставить `ALLOW_PUBLIC_REGISTRATION=false` и allowlist.
3. Включить off-site Backblaze B2 с write-only application key; локальный volume не защищает от потери VPS.
4. Удалить с VPS устаревшие `.env.bak*` после ручного подтверждения актуального `.env`.
5. Разделить application/migration/backup DB roles; текущий runtime role не должен быть PostgreSQL superuser.
6. Вынести inline CSS/`style=` из dashboard и убрать оставшийся `style-src 'unsafe-inline'`; JavaScript CSP уже nonce-only, event attributes запрещены.
7. **Запушить `main` на GitHub:** VPS уже синхронизирован с каноническим `main` (23.09.2026), но GitHub отстаёт на 50+ коммитов — токен на Mac без права `workflow` (в коммитах есть `.github/workflows/ci.yml`). Выдать токену `workflow` или добавить SSH-ключ в GitHub, затем `git push origin main`.

### P1 — качество и стоимость pipeline

0. **Главный рычаг скорости — платный Gemini.** Бесплатный 3.6 Flash даёт ~5 пачек/сутки (ретраи на 503 сжигают слоты), NVIDIA-эндпоинт нестабилен. С биллингом на Google-проекте и поднятым `GEMINI_DAILY_REQUEST_LIMIT` (≈30–40/сутки) Gemini один покрывает весь спрос ~370 оценок/сутки пачками по 15. Решение и биллинг — за владельцем.
0.1. Pre-filter пропускает в Tier 1 явно нерелевантные роли (пример 23.09: «Math, Physics & Engineering Graduates» — `(True, 'medium')`), они тратят дефицитную AI-квоту — [[Pre-filter правила]].

1. Добавить golden dataset из 50–100 вручную размеченных вакансий и regression-метрики precision@20 / false-negative rate.
3. Ввести per-backend latency/token/cost counters и вывести их в Ops.
4. Кэшировать detailed analysis по `(user, job, profile_hash, analysis_model)` с TTL; идею из старого `pipka-latest` реализовать заново в текущих роутерах, не переносить устаревший монолит.
5. Разделить AI-квоты real-time и backfill, чтобы массовая очередь не вытесняла пользовательский анализ.
6. JobSpy ротирует окно запросов/стран по `hour // 3`, хотя сканы с 17.08 ежечасные — перейти на часовой слот (втрое больше разнообразия запросов при том же объёме) — [[Источники вакансий]].
7. Тесты биллинга: сценарий списания кредитов в `_score_and_notify` (нулевой баланс → `credits_exhausted`, admin bypass) не покрыт; фидбек — не покрыта Telegram-нотификация — [[Тесты]].

### P1 — frontend/UX

1. Добавить UI-toggle semantic search и показывать, когда скрытые страны применены к ленте.
2. Перейти с offset pagination на keyset при росте таблицы.

### P2 — эксплуатация

1. Retention для `ops_events`.
2. Внешний alert на `backup_restore/error` и stale scan, а не только Ops UI.
3. Distributed scheduler lock и Redis rate limiter — только перед multi-replica.

## ⏳ Отложено (high value, по запросу)

### Production scaling

- **Distributed scheduler lock** — APScheduler in-memory, при `docker compose scale app=2` `_daily_backup` и `_cleanup_old_jobs` выстрелят дважды. Нужно `apscheduler.SQLAlchemyJobStore` + advisory lock в Postgres. Активировать когда пойдём в multi-replica.
- **Redis-backed rate limiter** — текущий [[Rate limiting]] single-process. При multi-replica каждая реплика разрешит свои 30 запросов в час. Миграция: `slowapi` + Redis storage.
- **Read-replica Postgres** — при >1000 пользователей dashboard-запросы (heavy join + sort by score) забьют primary. Stream replication → отдельный engine для читающих ручек.

### AI оптимизация

- **Phase 2c — proactive invalidation** — endpoint "пере-оценить всё для меня прямо сейчас". Сейчас Phase 2b делает это постепенно через 2-часовой backfill.
- **Soft-404 для Indeed/LinkedIn** — некоторые сайты на снятые вакансии возвращают HTTP 200 с body "this position has been filled" вместо 404. Per-source маркеры в `SOFT_404_MARKERS` + GET-проверка для тех источников где HEAD недостаточен.
- **Per-user AI buckets** — `MAX_JOBS_PER_SCORING_BATCH=15` глобально. При 10+ users в одной транзакции дерутся за квоту. Нужны per-user buckets с приоритезацией (платный → first).

### Observability

- **Prometheus `/metrics`** — `http_requests_total{path,status}`, `gemini_calls_total{result}`, `scan_duration_seconds`. Grafana сверху.

### UX / продукт

- **Watchlist scan staggering** — каждые 6 часов все пользователи бомбят источники одновременно. Раскидать по offset'ам.
- **Pre-filter v2 SQL-based** — перенести regex-проверки в `tsvector` + GIN, вместо Python-loop'ов.
- **Email notifications** (помимо Telegram) — для пользователей без TG.

### Cleanup

- **Дроп orphaned profile-колонок** — `industries`, `languages`, `experience_years`, `base_location`, `max_commute_km` удалить отдельной миграцией после проверки, что Telegram/старые клиенты их не читают.
- **Раздробить `Скоринг.md`** на под-страницы (Gemini / NVIDIA / Pre-filter / Recheck) — единая страница уже разрослась.
- Удалить мёртвый `deduct_user_credits()` или перевести на него инлайн-списание в шедулере (он атомарный) — [[Биллинг и кредиты]].
- Убрать неиспользуемые `DASHBOARD_*` / `GUEST_*` из `config.py` и production `.env` — [[Настройки]].

## Принципы приоритизации

1. **Production blockers > scaling > polish.** Если упало — чиним сразу. Скейлинг — когда подопрёт. Polish — между делом.
2. **Quota-friendly migrations.** Любая инвалидация AI-кэша должна быть постепенной (как Phase 2b), не штормовой.
3. **Observability first.** Перед добавлением новой фичи — убедиться, что её метрики ловятся (хотя бы в [[Ops панель|ops_events]]).

→ [[index]] → [[Архитектура]] → [[Скоринг]] → [[Кэш и инвалидация]]
