#changelog

# Changelog — Апрель 2026

## 18 апреля 2026

### Новые источники вакансий

**BerlinStartupJobs** (`app/sources/berlinstartupjobs.py`)
- RSS-парсер для berlinstartupjobs.com (операции + финансы)
- Keyword-фильтр по заголовку, fetch полной страницы для описания
- `country=DE`, `location=Berlin, Germany`

**WTTJ / Welcome to the Jungle** (`app/sources/wttj.py`)
- Ранее Otta.com (куплен WTTJ январь 2024)
- Использует публичный Algolia API (`CSEKHVMS53`)
- Фильтр по стране через `facetFilters`
- Реальные данные о зарплатах (редкость среди источников)
- Итого источников: **8**

### Fuzzy-дедупликация (app/sources/base.py, aggregator.py)

- `is_fuzzy_duplicate()`: title match + company token-subset + location guard
- `_are_same_company()`: frozenset токенов ≥4 символа; fallback для коротких имён (BMW)
- `_locations_conflict()`: асимметрия → не объединять; разные города → не объединять
- `raw_data["merged_sources"]` — список всех источников объединённой вакансии
- В дашборде: тег `SOURCE +1` с тултипом для объединённых вакансий
- В Ops stats: счётчик `⊕ слито N`

### Ops — панель дедупликации

- Новый эндпоинт `GET /api/ops/dedup` — вакансии с `merged_sources > 1`
- Панель в Ops tab: таблица объединённых вакансий с source-пиллами
- Загружается автоматически при открытии Ops

### Двухуровневый скоринг (app/scoring/rules.py, scheduler_service.py)

- `plain manager + domain` → бакет `manager_tier2` вместо hard reject
- Backfill Tier 1: director/VP/head → Claude, до 500/запуск
- Backfill Tier 2: manager-level → Claude, только когда Tier 1 пуст
- `low` bucket → `JobScore(score=0)` мгновенно (без API, дренаж очереди)
- Интервал backfill: 6ч → **2ч**
- Лимит AI-скоринга: 120 → **500** за запуск

### UI улучшения (dashboard.html)

- Фильтр «Источник» — динамически из `/api/stats` с счётчиком вакансий
- Новые источники автоматически появляются в списке без правок фронтенда

---

## 19 апреля 2026

### Gemini Flash — бесплатный backend для backfill скоринга (app/scoring/gemini_matcher.py)

**Проблема:** backfill скоринг использовал Claude (платно). При тысячах необработанных вакансий расход API мог быть значительным.

**Решение:** добавлен опциональный Gemini Flash backend.
- `GEMINI_API_KEY` в `.env` → backfill автоматически переключается на Gemini
- Реальное время (`_score_and_notify` → Telegram) остаётся на Claude
- Модель: `gemini-2.0-flash-lite` (30 RPM, 1500 RPD, бесплатно)
- Задержка 4с между батчами → не превышает лимит
- Ключ: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- Новые параметры: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BATCH_DELAY`

---

### Recheck pre-filter rejects (app/scoring/gemini_matcher.py, scheduler_service.py)

- `recheck_zero_scores(user, session, limit=500)` — повторная проверка `score=0/ai_analysis=NULL` через Gemini
- Запускается автоматически, когда оба бакета (Tier1 + Tier2) пусты
- При score=0 после повторной проверки → `ai_analysis='✓ confirmed'` (не повторяется)
- Возвращает `(checked, upgraded)` tuple

### Dot «качество данных» на карточке вакансии (dashboard.html, api/dashboard.py)

- 🟢 зелёный кружок: описание ≥ 300 символов (полные данные)
- 🟡 жёлтый кружок: описание < 300 символов (неполные данные)
- Поле `data_quality: "full" | "partial"` в `/api/jobs`
- Tooltip: «Полные данные» / «Неполные данные»

### Jooble как 9-й источник (app/sources/jooble.py)

- POST API `https://jooble.org/api/{key}`, 8 фиксированных запросов × 1 страница = 8 req/скан
- `JOOBLE_API_KEY` в `.env`
- `_auth_failed` — class-level флаг, останавливает повторы после 403
- `_last_request_count` → `source_stats.api_requests` в агрегаторе
- Ops: раздел Jooble Budget `{requests_total, budget: 500, remaining, pct_used}`
- Предупреждение в UI при ≥80% бюджета

### Таймаут источников (app/sources/aggregator.py)

- `SOURCE_TIMEOUT = 120` секунд на каждый источник
- `_search_source()` оборачивает `asyncio.wait_for(source.search(params), timeout=120)`
- Зависший источник не блокирует весь скан

### Убраны ограничения по зарплате (scoring/rules.py, matcher.py)

- Removed salary floor check из pre_filter (зарплата редко указана)
- Removed hard-cap по зарплате из SCORING_PROMPT
- AI упоминает зарплату в verdict, но не снижает score из-за её отсутствия

### Структурированное резюме в скоринг-промпте (scoring/matcher.py)

- `build_profile_text()` разбит на секции: Resume / Background, Preferences, CRITICAL EXCLUSIONS, Language requirement
- Резюме обрезается до 2500 символов (`RESUME_MAX_CHARS = 2500`)
- AI использует ОБА источника: резюме кандидата + preferences

---

## 19 апреля 2026 (продолжение)

### Обратная связь в скоринг: автоисключение компании (tracker_service.py, dashboard.py)

**Логика:**
- После каждого reject: `check_auto_exclude_company(user_id, job_id, session)`
- Подсчёт rejected-записей для данной компании (case-insensitive по `company_name`)
- При `count >= 5` → добавить компанию в `profile.excluded_keywords`
- Обнулить (`score=0, ai_analysis='✗ auto-excluded (company blocked)'`) все незатронутые `JobScore` для этой компании
- Не трогать `applied`/`rejected` статусы

**Frontend:**
- Toast-уведомление: `🚫 Компания «...» добавлена в исключения (5 отклонений)`
- `auto_excluded` поле в ответе `/api/jobs/{id}/action`

**Параметр:** `AUTO_EXCLUDE_THRESHOLD = 5` в `tracker_service.py`

### Ежедневные бэкапы БД (app/services/backup_service.py)

**Как работает:**
- APScheduler cron `02:30 UTC` → `_daily_backup()` → `run_backup()`
- `pg_dump` (нужен `postgresql-client` в Docker) → gzip → `/app/data/backups/pipka_YYYYMMDD_HHMMSS.sql.gz`
- Хранятся последние **7** файлов (ротация автоматически)
- OpsEvent `backup/success|error` записывается в БД

**Backblaze B2 (опционально):**
```
B2_KEY_ID=...
B2_APP_KEY=...
B2_BUCKET=pipka-backups
B2_ENDPOINT=https://s3.us-west-004.backblazeb2.com   # при необходимости изменить
```
- Если B2 не настроен → только локальный бэкап
- B2 upload best-effort: сбой загрузки не отменяет локальный бэкап

**Изменения:**
- `Dockerfile`: добавлен `postgresql-client`
- `pyproject.toml`: добавлен `boto3>=1.34`
- `app/config.py`: `b2_key_id`, `b2_app_key`, `b2_bucket`, `b2_endpoint`
- `app/services/backup_service.py`: новый файл

---

## 20 апреля 2026

### Scan funnel + pre-filter rejected KPI (ops_service.py, dashboard.html)

- В `/api/ops/overview` добавлено поле `kpis.prefilter_rejected` — количество `JobScore(score=0, ai_analysis IS NULL)` по пользователю.
- В Ops-дашборде отрисован funnel `Собрано → После pre-filter → Оценено AI → score ≥ 70`.
- Fix: UTC-таймстемпы в `ops_service` + локально-независимый формат времени в UI.

### Exclusions chip UI (dashboard.html)

- Чипы-исключения (`excluded_keywords`) в Settings tab — клик по X удаляет.
- Search counter показывает количество активных фильтров.

---

## 22 апреля 2026

### Публичная инфографика (`/infographic`, `GET /api/public/stats`)

- Отдельный HTML-дашборд `infographic.html` — воронка с нарративом «1 пользователь», SMM-friendly.
- Публичный эндпоинт `/api/public/stats`: `{total_jobs_processed, ai_analyses_performed, jobs_last_24h, active_sources, system_status}` — без авторизации.
- Кнопка «SMM Infographic» в Ops cockpit открывает дашборд.
- `app/main.py`: отключен кэш для корневого HTML — чтобы dashboard всегда был свежим.
- CSS: height-limit и scrollbar для Ops events list.

### Admin-функции в Ops cockpit (app/api/dashboard.py, ops_service.py)

**Новые эндпоинты (admin-only):**
- `GET  /api/admin/user/{user_id}/profile` — возвращает профиль пользователя (resume_text, target_titles, languages, preferences) + агрегаты по его JobScore.
- `DELETE /api/admin/user/{user_id}` — удаление пользователя (каскадно: profile, scores, applications).

**UI в Ops → Users:**
- Кнопка 👁 View — открывает модалку с профилем через существующий `jobModal` (а не несуществующий `openModal()`).
- Кнопка 🗑 Delete — с confirm-диалогом, после удаления reload страницы.
- Плюс: dynamic plural для label «Active User(s)».

### Pre-filter: расширение для crisis / turnaround / CRO / interim / growth (rules.py)

**`DIRECTOR_KEYWORDS` — добавлены:**
- `cro` (Chief Restructuring / Revenue Officer)
- `interim manager`, `interim director`, `interim head`
- `crisis manager`, `crisis director`, `krisenmanager` (DE)
- `turnaround manager`, `turnaround director`
- `restructuring`
- `growth director`

**`DOMAIN_KEYWORDS` — добавлены:**
- `crisis management`, `turnaround`, `transformation`
- `restructuring`, `interim management`, `business continuity`
- `operational excellence`, `continuous improvement`
- `growth`

**Эффект:** вакансии типа «Interim Head of Operations», «Turnaround Director», «CRO» больше не отсекаются pre-filter'ом, идут на AI-скоринг.

### Temporary switch всех AI-операций на Gemini Flash (matcher.py, scheduler_service.py)

Коммит `623fdd6`: и `_score_and_notify` (реальное время), и `analyze_single_job` временно переведены на Gemini Flash — чтобы снизить Claude spend.

---

## 23 апреля 2026

### Revert: real-time скоринг обратно на Claude (scheduler_service.py)

**Причина:** Gemini Flash free tier (30 RPM / 1500 RPD) словил 429 Rate Limit при реально-временном скоринге — новые вакансии не оценивались, топ-push в Telegram пропадал.

**Что осталось на Gemini:**
- `_backfill_score` (каждые 2ч) — Gemini Flash остаётся backend'ом, если `GEMINI_API_KEY` задан. Здесь 4-секундная задержка между батчами держит нас в лимите.
- `analyze_single_job` (кнопка «AI-анализ» в боте) — Gemini Flash, если ключ задан.
- `recheck_zero_scores` — Gemini Flash, проверка pre-filter rejects.

**Что вернулось на Claude:**
- `_score_and_notify` — реальное время, сразу после скана, до 80 новых вакансий на пользователя.

Актуальная таблица backend'ов по типам операций — в [[Скоринг]].

---

## 24 апреля 2026

### Gemini model → `1.5-flash` (config.py, коммит b2e5164)

- `gemini_model: "gemini-2.0-flash-lite"` → `"gemini-1.5-flash"`.
- **Причина:** у `2.0-flash-lite` на free tier нулевая квота.
- **Цена:** лимиты у `1.5-flash` вдвое строже — **15 RPM** (было 30) / 1500 RPD (без изменений).

### Hotfix: Gemini model → `2.5-flash-lite` (config.py)

**Проблема:** на проде все запросы к Gemini отдавали **404** —
`models/gemini-1.5-flash is not found for API version v1beta`. Вся серия `gemini-1.5-*` официально retired. В результате очередь скоринга стояла (369 вакансий висело часами).

**Диагностика:** `genai.list_models()` на сервере показала живые модели:
- `gemini-2.0-flash`, `gemini-2.0-flash-lite` → 429 (квота выбрана у обеих)
- `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-flash-latest` → работают

**Решение:** переключились на `gemini-2.5-flash-lite` — lite-вариант новейшего поколения:
- 15 RPM / 250 K TPM / **1000 RPD** free tier
- Ближе всего к изначальной архитектуре (flash-lite класс)
- `GEMINI_BATCH_DELAY` остаётся 4с (15 RPM → 1 req/4с = 15 RPM ровно)

### Real-time скоринг снова на Gemini (scheduler_service.py, коммит 72a6dcf)

- В `_score_and_notify` добавлен выбор: если `GEMINI_API_KEY` задан → `score_jobs_gemini`, иначе `score_jobs` (Claude).
- Откат ранее введённого hard-code «Claude-только» (23.04.2026). Новый hotfix на лимиты — switch by env var.
- Теперь **все** AI-операции (real-time + backfill + analyze + recheck) унифицированно идут через Gemini, если ключ задан.

### Backfill: cap 500 → 1000 за запуск (scheduler_service.py)

- `_backfill_score`: Tier 1 и Tier 2 cap подняты с **500** до **1000** за запуск.
- Интервал **2 часа** не меняем.
- **Математика под 1.5-flash (15 RPM / 1500 RPD):**
  - 1000 вакансий / 8 per batch = **125 батчей**, 125 × 4с задержки = 500с (~8 мин на прогон)
  - 125 req / 8 мин = ~**15.6 RPM** — на границе лимита, retry разрулит короткие 429
  - Суточная нагрузка при типичной очереди ≤500 ваканий/запуск: 12 × 60 = 720 req/день на backfill
  - Плюс real-time: 8 runs × 10 батчей = 80 req/день
  - Итого ~**800 req/день** — комфортно в 1500 RPD
- Если будет стабильно срабатывать 429 — поднять `GEMINI_BATCH_DELAY` с 4с до 6с в `.env`.
- Обновлено в [[Сервисы]] и [[Скоринг]].

---

## 24 апреля 2026 (вечер)

### Переход на `gemini-3.1-flash-lite-preview`

- `gemini-2.5-flash-lite` выбрала дневной лимит за ~5 запросов (free tier 20 RPD на этой модели).
- На free tier `ascoldfx@gmail` живой остаётся только `gemini-3.1-flash-lite-preview` (**15 RPM / 500 RPD**).
- Обновлён `app/config.py`: `gemini_model = "gemini-3.1-flash-lite-preview"` (коммит `ae2aa42`).
- **Claude временно отключён — нет баланса.** Все AI-операции идут через Gemini.

### Фаза 1 рефакторинга скоринга: retry + pacer + semaphore

**Файл:** `app/scoring/gemini_matcher.py`

- Добавлен `tenacity>=9.0` в `pyproject.toml`.
- `_generate_with_retry()` — оборачивает `generate_content_async`:
  - `AsyncRetrying` на 5 попыток, exp backoff `wait_exponential(multiplier=5, min=5, max=80)` + ±2с jitter.
  - `retry_if_exception(_is_retryable)` ловит только `ResourceExhausted` (429), `ServiceUnavailable` (503), `DeadlineExceeded`, `InternalServerError`, `Aborted`. Прочие ошибки пробрасываются без retry.
- **Семафор `asyncio.Semaphore(1)`** — единственный in-flight запрос к Gemini на процесс. Без него несколько пользователей сожгут RPM мгновенно.
- **Глобальный pacer** (`asyncio.Lock` + `last_call_monotonic`) — минимум **4.5с** между любыми вызовами Gemini (15 RPM с запасом).
- При 429 пишется `OpsEvent(event_type="gemini_429", status="retry")` — счётчик виден в Ops Cockpit.
- При исчерпании ретраев пишется `OpsEvent(event_type="gemini_exhausted", status="error")`.

**Эффект:** один прогон backfill на 399+ вакансий больше не теряет батчи из-за 429 — каждый запрос получает до 5 попыток с backoff до 80с.

---

## 24 апреля 2026 (вечер, позже)

### Удалён фильтр Industries из профиля

По запросу пользователя — поле не давало практической пользы и создавало лишний шум в промпте.

**Удалено:**
- UI-секция Industries из `app/static/dashboard.html` (включая `.ind-grid`/`.ind-pill` CSS, `INDUSTRIES` массив, `toggleInd()`, `_loadIndustries()`, i18n-ключи).
- `Form industries` в POST `/api/profile` и ключ в GET `/api/profile` + admin endpoint (`app/api/dashboard.py`).
- Строка `Industries: ...` из scoring-промпта `build_profile_text()` (`app/scoring/matcher.py`).
- Кнопка «🏭 Индустрии» и обработчики в Telegram-боте (`app/bot/keyboards.py`, `app/bot/handlers/settings.py`).
- Поле `s-industries` из `app/static/js/app.js` (legacy).
- Seed-значение в `fill_profile.py`.

**Сохранено:** колонка `user_profiles.industries` (JSON) в БД — миграция не запускалась, старые значения игнорируются. Можно дропнуть в следующем релизе.

---

## 24 апреля 2026 (ночь)

### NVIDIA Build как idle-rescorer для Германии

Добавлен третий AI-backend — NVIDIA Build (модель `google/gemma-4-31b-it` через OpenAI-совместимый endpoint `https://integrate.api.nvidia.com/v1/chat/completions`). Не заменяет Gemini, работает только в простое.

**Новые файлы / правки:**

- `app/scoring/nvidia_matcher.py` — `idle_rescore_for_user(user, session)`. Полный цикл retry/pacer/semaphore по образу `gemini_matcher.py`: `Semaphore(1)`, pacer `nvidia_batch_delay` (default 2с), `tenacity` на 429/5xx/таймауты. На 429 → `OpsEvent("nvidia_429")`, на exhausted → `OpsEvent("nvidia_exhausted")`.
- `app/services/scheduler_service.py` — `_nvidia_idle_rescore()`, запускается каждые **30 минут**. Гард на вход: для каждого пользователя считает кол-во вакансий DE за 45 дней без `JobScore`; если > 0 → skip (Gemini ещё не дренировал очередь).
- `app/config.py` — новые env-настройки: `nvidia_api_key`, `nvidia_model`, `nvidia_base_url`, `nvidia_batch_delay`, `nvidia_max_per_run` (default 300), `nvidia_country="de"`, `nvidia_rescore_stale_days=7`.

**Две фазы в `idle_rescore_for_user`:**

1. **Priority (a):** pre-filter rejects (`score=0 AND ai_analysis IS NULL`) с `country=de, scraped_at ≥ now-45d`. Если Gemma поднимает score > 0 — вакансия появляется в inbox.
2. **Priority (b):** stale successful scores (`score > 0 AND scored_at < now - 7d`) — освежение старых оценок тем же промптом.

**Активация:** `NVIDIA_API_KEY=nvapi-...` в `/opt/pipka/.env` → рестарт. Пусто → job работает, но сразу выходит.

---

## 25 апреля 2026

### Gemini circuit breaker + NVIDIA fallback для backfill

**Проблема.** Free-tier `gemini-3.1-flash-lite-preview` = 500 RPD. После выработки дневного лимита `_backfill_score` не выходил из livelock-а: tenacity делал 5 попыток × 80с backoff на каждый батч, все 5 ловили 429, батч умирал, через 2ч следующий тик подбирал те же job_id и всё повторялось. За сутки 119 ретраев в `ops_events` — без полезной работы.

**Решение — process-local circuit breaker в `app/scoring/gemini_matcher.py`:**

- Константа `_BREAKER_TRIP_THRESHOLD = 3` подряд-exhausted батчей. На 3-м `gemini_disabled_until = next UTC midnight`.
- `is_gemini_available()` — новая публичная функция. Возвращает `True` если breaker закрыт ИЛИ дедлайн прошёл (тогда автосброс).
- `_record_exhaust(reason)` / `_record_success()` — ведут счётчик в `_breaker_lock`. На trip пишут `OpsEvent("gemini_breaker_open", "warning")`.
- `_call_gemini_raw`, `score_jobs_gemini`, `recheck_zero_scores` — все ранний выход если breaker открыт.

**Перераспределение нагрузки — `app/scoring/nvidia_matcher.py`:**

- Новая функция `score_jobs_nvidia(jobs, user, session)` — drop-in mirror `score_jobs_gemini`. Записывает новые `JobScore` через `pg_insert(...).on_conflict_do_nothing(...)`. Country-фильтр **не** применяется (это только для idle rescorer).

**Цепочка выбора в `_backfill_score_fn` (app/services/scheduler_service.py):**

1. **Gemini** — если ключ задан И `is_gemini_available()`.
2. **NVIDIA** — если Gemini exhausted/нет ключа И `nvidia_api_key` задан.
3. **Claude** — последний fallback.

После полуночи UTC breaker автоматически сбрасывается на ближайшем тике, Gemini снова становится приоритетом.

**Эффект на ops:** ушёл шум `gemini_429 retry` в холостую. Появился `gemini_breaker_open warning` (1 событие за день при триппинге) и `nvidia_rescore success` чаще, т.к. NVIDIA забирает backfill.

---

## 26 апреля 2026

### Performance + security: bulk upsert, CSRF, statement_timeout, новые индексы

Три параллельных правки по итогам аудита.

**1. Bulk upsert в `app/sources/aggregator.py`.** Удалён N+1 паттерн (per-row `SELECT WHERE dedup_hash = ?`), который генерировал по 500–800 запросов на каждый скан. По `pg_stat_user_indexes` это давало 186 000 сканов `ix_jobs_dedup_hash` за день. Теперь один `SELECT WHERE dedup_hash IN (...)` для существующих + один `pg_insert(...).on_conflict_do_nothing(index_elements=["dedup_hash"])` для новых + один `SELECT` для возврата ID. Итого 3 запроса независимо от размера батча.

**2. CSRF middleware (`app/main.py`).** Double-submit pattern: `CSRFMiddleware` лениво генерирует токен в session при первом GET, ставит JS-readable cookie `csrf_token`, требует совпадения header `X-CSRF-Token` на POST/PUT/PATCH/DELETE. Исключения: `/auth/*` (Google callback), `/health`. JS-обёртка fetch в `app/static/js/app.js` подмешивает заголовок автоматически — call-сайты не меняются. `/api/me` теперь возвращает `csrf_token` в ответе как fallback. SameSite=lax уже стоял на session-cookie, но не защищал от form-сабмитов — теперь защищает.

**3. Connection-level guards + новые индексы (`app/database.py`, `app/models/job.py`).**

`statement_timeout=10s, lock_timeout=3s, idle_in_transaction_session_timeout=60s, application_name=pipka` через `connect_args.server_settings` для PostgreSQL. Pool: 5+10 → 10+20.

Новые индексы (созданы на проде через `CREATE INDEX CONCURRENTLY` без блокировки таблиц, добавлены в модели для новых установок):

- `ix_jobs_scraped_at (scraped_at)` — backfill scorer + cleanup ходят по `Job.scraped_at >= cutoff`. Раньше seq scan.
- `ix_jobs_country_scraped (country, scraped_at)` — NVIDIA rescore + dashboard country-фильтры. Composite убирает второй проход.
- `ix_job_scores_user_scored_at (user_id, scored_at)` — NVIDIA priority (b) ходит по `WHERE user_id AND scored_at < stale_cutoff ORDER BY scored_at`.

**Аудит-список (что осталось):** Sentry/Prometheus, разнос `dashboard.py` по файлам, Alembic bootstrap (нужен для Phase 2 profile_hash), `raw_data` JSON→JSONB, rate-limit на `/api/jobs/{id}/analyze`, distributed lock для scheduler при горизонтальном масштабировании.

---

## 26 апреля 2026 (вечер)

### Sentry, rate-limit на AI-анализ, JSONB-миграция

Прошли пункты #1–#3 из аудит-листа.

**1. Sentry SDK (`app/main.py`, `app/config.py`, `pyproject.toml`).** `sentry-sdk[fastapi]>=2.18` добавлен в зависимости. Инициализация в `main.py` ДО создания `FastAPI()` — иначе ASGI-хуки SDK не повесятся. Конфиг через env: `SENTRY_DSN` (пусто → SDK не инициализируется), `SENTRY_ENVIRONMENT=production`, `SENTRY_TRACES_SAMPLE_RATE=0.05`, `SENTRY_PROFILES_SAMPLE_RATE=0.05`. Включены интеграции `AsyncioIntegration` + `SqlalchemyIntegration`. PII не отправляется (`send_default_pii=False`).

**Активация:** взять DSN на sentry.io (бесплатный тариф 5 000 событий/мес), добавить `SENTRY_DSN=https://...@o0.ingest.sentry.io/0` в `/opt/pipka/.env`, рестарт. Без DSN — нулевая нагрузка.

**2. In-process rate-limit (`app/api/_ratelimit.py`).** Sliding-window per (user_id, key) на `collections.deque[float]` под `threading.Lock`. Хук в `analyze_job` — **30 запросов в час на пользователя**. На 31-й клик возвращается `429` + заголовок `Retry-After`. Каждый клик жжёт один запрос к Gemini/Claude — без ограничения юзер мог click-spam'ом высадить дневной RPD за минуту.

Single-process решение (живёт в памяти контейнера). При горизонтальном масштабировании — заменить на `slowapi` + Redis.

**3. JSON → JSONB (`app/models/job.py`, `app/models/ops_event.py`, `app/api/dashboard.py`).** Колонки `jobs.raw_data` и `ops_events.payload` мигрированы:

```sql
ALTER TABLE jobs ALTER COLUMN raw_data TYPE jsonb USING raw_data::jsonb;
ALTER TABLE ops_events ALTER COLUMN payload TYPE jsonb USING payload::jsonb;
CREATE INDEX CONCURRENTLY ix_jobs_merged_sources ON jobs USING gin ((raw_data->'merged_sources'));
```

Модели используют `JSON().with_variant(JSONB(), "postgresql")` — сохраняем совместимость с sqlite (dev/тесты). `dashboard.py:get_dedup_jobs` переключён на `func.jsonb_array_length` (json_array_length не работает с jsonb-аргументом в PG).

GIN-индекс ускоряет `/api/ops/dedup` и любые будущие запросы по подкомпонентам `raw_data`.

---

## 26 апреля 2026 (поздний вечер)

### Bootstrap Alembic + Phase 2 (profile_hash + model_version)

**1. Alembic как источник истины схемы.** `app/database.py:init_db()` теперь не вызывает `Base.metadata.create_all()` напрямую, а запускает `command.upgrade(cfg, "head")` через `run_in_executor` (alembic — sync). Прежний путь оставался формально нелегальным (CLAUDE.md → "только Alembic", но `create_all` использовался) — теперь приведён в соответствие.

Появились две миграции:

- **`0001_baseline.py`** — идемпотентный `Base.metadata.create_all()` под `op.get_bind()`. На fresh dev DB разворачивает всю схему. На проде (где все 8 таблиц + индексы уже существуют) — no-op, потому что `create_all` пропускает существующие таблицы. Downgrade рейзит ошибку (защита от случайного DROP всего).
- **`0002_phase2_profile_hash.py`** — добавляет колонки `profile_hash VARCHAR(64) NULL` и `model_version VARCHAR(64) NULL` в `job_scores`, plus composite index `ix_job_scores_user_profile_model (user_id, profile_hash, model_version)`.

При первом старте контейнера на проде Alembic создаст таблицу `alembic_version`, отметит обе миграции как применённые (baseline — no-op, Phase 2 — `ALTER TABLE` за <50мс на 7700 строк).

**2. `app/scoring/profile_hash.py`** — новый модуль:

- `compute_profile_hash(profile)` — sha256 поверх стабильного JSON из 10 scoring-relevant полей (`resume_text`, `target_titles`, `languages`, `work_mode`, `preferred_countries`, `excluded_keywords`, `english_only`, `target_companies`, `min_salary`, `experience_years`). Whitespace стрипается, списки сортируются — тривиальные правки ("Sales Manager " → "Sales Manager") не инвалидируют кеш.
- `MODEL_GEMINI()`, `MODEL_CLAUDE()`, `MODEL_NVIDIA()` — фабрики идентификатора бэкенда, читают `settings.{gemini,claude,nvidia}_model` (env-only смена модели автоматически инвалидирует кеш).

**3. Все JobScore-write пути проставляют оба поля** (`app/scoring/{matcher,gemini_matcher,nvidia_matcher}.py`, `app/services/scheduler_service.py`):

- Gemini real-time + backfill + recheck → `model_version="gemini:<model>"`.
- Claude (fallback) → `model_version="claude:<model>"`.
- NVIDIA score_jobs + idle_rescore (priority a + b) → `model_version="nvidia:<model>"`.
- Pre-filter rejects (`score=0`) в `_backfill_score` → `model_version="prefilter"`. Маркер позволяет AI rescore-путям отличать rule-based нули от настоящих AI-нулей.

**Текущее поведение чтения не изменено.** Колонки только заполняются; инвалидация (Phase 2b) — следующим коммитом, чтобы избежать одновременной массовой переоценки. Существующие 7700 строк остаются с `profile_hash=NULL, model_version=NULL` — они в применении не отличаются от свежих.

---

## 26 апреля 2026 (ночь)

### Phase 2b — read-side инвалидация по profile_hash

Используем поля Phase 2a для постепенного освежения скоринга при смене профиля. Без destructive deletes, без массового удара по AI-квоте при коммите.

**Read filter** в `_backfill_score`, `_score_and_notify` и кеше `score_jobs`:

```python
WHERE user_id = X
  AND (profile_hash IS NULL                  -- legacy, не считаем устаревшим
       OR profile_hash = current_profile_hash)
```

Mismatched-строки вылетают из множества "уже оценено" и попадают в очередь backfill'а. Legacy NULL — остаются как есть (защита от шторма апгрейда).

**Write path — UPSERT** во всех сайтах JobScore-INSERT:

```python
INSERT ... ON CONFLICT (job_id, user_id) DO UPDATE
  SET score=EXCLUDED.score, ai_analysis=EXCLUDED.ai_analysis,
      profile_hash=EXCLUDED.profile_hash, model_version=EXCLUDED.model_version,
      scored_at=now()
  WHERE job_scores.profile_hash != EXCLUDED.profile_hash
```

`NULL != X` в Postgres = unknown, не TRUE → legacy строки UPDATE'ом не задеваются. Свежий скоринг с тем же hash-ем — no-op (без churn'а scored_at).

**Затронуто:**

- `app/services/scheduler_service.py` — `_score_and_notify` и `_backfill_score`. Pre-filter rejects (model_version=`prefilter`) теперь тоже UPSERT — смена `excluded_keywords` вызовет их пере-оценку.
- `app/scoring/gemini_matcher.py:score_jobs_gemini` — UPSERT.
- `app/scoring/nvidia_matcher.py:score_jobs_nvidia` — UPSERT.
- `app/scoring/matcher.py:_score_batch` — заодно убрал старый per-row `flush+IntegrityError+rollback`, заменён единым batch UPSERT (drop-in совместимый contract — возвращает list[JobScore]).
- `app/scoring/matcher.py:score_jobs` — кеш-проверка тоже учитывает `profile_hash`.

**Ожидаемое поведение:** пользователь правит профиль → следующий 2-часовой backfill подбирает stale-строки и перезаписывает (cap 1000/тик × 2 backend'а = ~12k/сутки). Реальное-время `_score_and_notify` обновляет mismatched-строки тут же при следующем скане.

Никакого глобального DELETE/UPDATE на endpoint'е смены профиля — quota-friendly.

---

→ [[Источники вакансий]] → [[Скоринг]] → [[Сервисы]] → [[API]] → [[Настройки]] → [[База данных]]
