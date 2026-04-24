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

→ [[Источники вакансий]] → [[Скоринг]] → [[Сервисы]] → [[API]]
