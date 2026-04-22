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

→ [[Источники вакансий]] → [[Скоринг]] → [[Сервисы]] → [[API]]
