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

## 5 мая 2026

### Day-1 security hardening (pre-launch)

Перед prod-релизом для аудитории 5к закрыты 6 блокеров из глубокого аудита (severity 7-9). Все в одном коммите `8bc2093`.

**1. Stale admin role.** `require_admin_async` + `_ROLE_CACHE` (TTL 60s/user) в `app/api/_helpers.py`. Перед фиксом роль читалась только из session-cookie (30 дней stale). Теперь — DB read с кешем, revoked admin теряет доступ за ≤60s. `admin_delete_user` явно дропает кеш через `_drop_role_cache(user_id)`. Sync `require_admin` оставлен deprecated для UI cosmetics.

Wired: `admin.py`, `scan.py`, `ops.py`.

**2. Logout CSRF.** Был `GET /auth/logout`, эксплоится через `<img src=/auth/logout>` в любой странице где залогинен user. Перевели на `POST` + CSRF-токен. `_CSRF_EXEMPT_PREFIXES` сужен с `/auth/` до `/auth/google/` (только OAuth flow). Frontend `app.js` + inline в `dashboard.html` — `fetch('POST').finally(navigate)`.

**3. SecurityHeadersMiddleware.** Новый middleware в `app/main.py`. Заголовки на каждом ответе:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; ...; frame-ancestors 'none'`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()`

Mounted innermost — заголовки попадают на все ответы включая `/static/*`. См. [[Безопасность#day-1-фиксы-перед-prod-релизом]].

**4. Resume upload OOM.** Был `content = await file.read()` — буферилось ВСЁ тело до проверки 10MB. Атакующий шлёт 1GB → OOM контейнера. Теперь:

- Pre-check `request.headers["content-length"]` ≤ `MAX_RESUME_UPLOAD_BYTES`.
- Стрим 64KB-чанками через `file.read(64*1024)` с running total. На превышении — 413 в момент пересечения лимита.

**5. ZIP-bomb defense на DOCX.** 50KB DOCX может декларировать 5GB document.xml. Добавлена проверка `zf.getinfo("word/document.xml").file_size` до `read()`, лимит 8MB uncompressed → 400.

**6. Session fixation.** В `/auth/google/callback` перед записью identity вызывается `request.session.clear()`. Если атакующий до login'а подсунул жертве cookie через subdomain XSS / MITM, plant'нутая сессия теряет identity.

**Verification на проде** (после деплоя):
```
$ curl -sI https://pipka.net/api/me | grep -iE 'hsts|csp|x-frame|x-content|referrer|permissions'
strict-transport-security: max-age=31536000; includeSubDomains; preload
x-frame-options: DENY
x-content-type-options: nosniff
referrer-policy: strict-origin-when-cross-origin
permissions-policy: camera=(), microphone=(), geolocation=(), interest-cohort=()
content-security-policy: default-src 'self'; ...
```

`GET /auth/logout` → 405 Method Not Allowed (только POST).

**Что осталось** (Day-2 + Day-3 из плана аудита):

- Day-2 (high): TrustedHostMiddleware, global per-IP rate-limit, `?search=` length cap, profile-list size limits, Sentry PII filter.
- Day-3 (medium): jsq → htmlEscape combo, PDF/DOCX parse timeout, Telegram Forbidden auto-deactivate, ON DELETE CASCADE на FK, validate job_id existence в actions.

См. [[Roadmap#day-1-security-hardening]] и [[Безопасность#day-1-фиксы-перед-prod-релизом]].

---

## 6 мая 2026

### Day-2 security hardening

Пять high-severity пунктов аудита (severity 6-7), пакетом перед запуском.

**1. TrustedHostMiddleware** — добавлен outermost (первым в `add_middleware`, что у Starlette = wraps last = outermost). Allowed: `pipka.net`, `*.pipka.net`, `localhost`, `127.0.0.1`. Forged `Host:` отбивается до того как `SessionMiddleware` тратит ресурсы на парсинг cookie.

**2. Global per-IP rate-limit** (`RateLimitMiddleware` в `app/api/_ratelimit.py`).

Sliding-window per-IP в том же модуле что и существующий per-user limiter — общая deque-структура и lock. Three buckets:

| Префикс | key | limit | window |
|---------|-----|-------|--------|
| `/auth/google/login`, `/auth/logout` | `auth-write` | 10 | 60s |
| `/api/profile`, `/api/profile/resume` | `profile-write` | 20 | 60s |
| `/api/*` (catch-all) | `api-global` | 300 | 60s |

First-match-wins. Exempt: `/static/*`, `/health`, `/auth/google/callback` (Google повторяет после reCAPTCHA, нельзя rate-limit'ить).

Client IP резолвится через цепочку: `CF-Connecting-IP` → `X-Forwarded-For[0]` → socket-host. Cloudflare перед nginx — это эталонный setup для прода. Mounted между TrustedHost и Session так что bot'ы получают 429 до парсинга cookie.

**3. `?search=` length cap.** В `/api/jobs?search=…` через `Query(None, max_length=200)`. Защита от 1MB substring-attack под `statement_timeout=30s` — pgvector tsvector match быстр, но ILIKE-fallback на sqlite разнёс бы pool.

**4. Profile-list size limits в `/api/profile`:**

| Поле | Max entries | Max chars/entry |
|------|------------|-----------------|
| `target_titles` | 50 | 200 |
| `preferred_countries` | 50 | 200 |
| `excluded_keywords` | 50 | 200 |
| `target_companies` | 50 | 200 |
| `languages` (dict) | 20 keys | 50 key + 50 value |

Защита от `compute_profile_hash` blow-up'а (sha256 поверх 10K entries), `pre_filter` O(jobs × keywords) loop'а, watchlist scanner'а O(companies × countries). Также проверка `isinstance(parsed, dict)` для languages — JSON-bomb path закрыт.

**5. Sentry PII filter.** В `app/main.py:_sentry_before_send`. До этого `attach_stacktrace=True` + `logger.exception("update_profile failed")` отгружал в Sentry весь resume_text как local var в stack frame. `send_default_pii=False` filter'ит только Sentry-auto-PII (cookies, IP), но не frame-locals и breadcrumbs.

Скрабит 13 ключей (`resume_text`, `target_companies`, `excluded_keywords`, `email`, `user_email`, `name`, `user_name`, `avatar_url`, `user_avatar`, `telegram_id`, `google_sub`, `csrf_token`, `session_secret`, `Authorization`, `Cookie`) из:
- `event["exception"]["values"][*]["stacktrace"]["frames"][*]["vars"]`
- `event["breadcrumbs"]["values"][*]["data"]`
- `event["request"]["headers"]` + `["data"]`
- `event["extra"]`

Recursive walk с depth-limit 6. Список PII-ключей в `_SENTRY_PII_KEYS` frozenset'е.

См. [[Безопасность#day-2-фиксы]], [[Rate limiting#per-ip-middleware]].

---

→ [[Changelog 2026-04]] → [[Roadmap]] → [[Архитектура]] → [[Безопасность]] → [[Auth]] → [[API]] → [[Rate limiting]]
