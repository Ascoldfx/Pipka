#changelog

# Changelog июль 2026

## 4 июля 2026

### «Отклонённые вакансии возвращаются» — скрыты из дефолтного списка

Пользователь отклоняет вакансию (✖) в «Все вакансии» → `loadJobs()` перерисовывает список → строка остаётся на месте (лишь подсвечен ✖). Ощущалось как «вакансия вернулась». Причина: фильтр `status` применялся только на вкладках Inbox/Applied/Rejected, дефолтный список показывал все 667 rejected вперемешку с активными. Дубликаты-«возвращенцы» через пересбор с новым job_id исключены проверкой (0 совпадений по dedup_hash).

- `app/api/jobs.py` — `GET /api/jobs` без `status` теперь добавляет `Application.status IS NULL OR != 'rejected'`. Отклонённые остаются доступны на вкладке Rejected.

### NVIDIA: reraise=True терял nvidia_exhausted ивенты

В логах копились `NVIDIA call failed (batch=15):` с пустым сообщением — это `httpx.ReadTimeout` (пустой `str()`). Из-за `reraise=True` tenacity пробрасывал исходное исключение вместо `RetryError`, ветка `except RetryError` (пишущая `nvidia_exhausted` в Ops) никогда не срабатывала. Тот же класс бага, что чинили в Gemini-брейкере 27.05.2026.

- `app/scoring/nvidia_matcher.py` — `reraise=False` в `AsyncRetrying`; в generic-except добавлено имя типа исключения (httpx-таймауты строкифицируются в пустоту).

См. [[Скоринг]], [[API]], [[Ops панель]].

### Аудит покрытия поиска: большинство target_titles не искались

Пользователь заметил «мало вакансий попадают под парсинг». Аудит подтвердил: из 29 титулов профиля до реального поиска доходила малая часть, причём произвольная.

Корень: `_background_scan` собирал запросы в `set()` → порядок произвольный (hash-randomization, перетасовка при каждом рестарте контейнера). Капы источников выбирали «первые N» из этого лотерейного порядка:

- **JobSpy/Indeed:** только 8 из 29 титулов, состав случайный.
- **Adzuna:** country-мажорный цикл + cap 40 → вся квота на первую страну (29 комбо) + огрызок второй; **7 из 9 стран не искались вообще** (комментарий в коде утверждал обратное).
- **Jooble:** профиль игнорировался полностью — искался хардкод-список из 8 запросов (interim/crisis/AI-титулы никогда).
- Xing и fetch-all источники (Arbeitnow/Remotive/BSJ/WTTJ) — были ок.

Фиксы:
- `app/services/scheduler_service.py` — ordered dedupe вместо `set()`: порядок профиля = приоритет пользователя; нормализация двойных пробелов («Growth  director»), кейс-инсенситив дедуп («crisis manager» ×2).
- `app/sources/adzuna.py` — query-мажорный цикл (топ-титулы × все страны) + cap 40 → 80 (160 req × pace 0.4s ≈ 70s, в пределах 180s таймаута).
- `app/sources/jobspy_source.py` — ротация окна по 3ч-слоту (`hour // 3`): за 8 сканов/сутки все титулы проходят через Indeed ≥2 раза.
- `app/sources/jooble.py` — ищет топ-8 титулов профиля; `JOOBLE_QUERIES` остался фолбэком при пустом профиле.

См. [[Источники вакансий#покрытие-запросов-04072026]], [[Сервисы#scheduler]].

## 10 июля 2026

### Фаза 1: Gulf / Океания / Индонезия в поиске

Расширение за пределы Европы без новых скраперов — разблокированы существующие источники. Раньше система была зашита под Европу в 4 местах: `ALLOWED_COUNTRIES` (белый список), чёрный список городов (dubai/sydney/jakarta… явно блокировались), маппинги JobSpy и Jooble.

- `app/sources/aggregator.py` — `REGION_MARKERS` (ae/sa/qa/au/nz/id/sg): opt-in через preferred_countries. Для активных стран маркеры работают как DACH_MARKERS, их города исключаются из blacklist. Европа-only пользователи не затронуты.
- `app/sources/jobspy_source.py` — `COUNTRY_NAME` + 7 стран (Indeed работает во всех); `JOBSPY_MAX_COUNTRIES=3` — ротация стран по 3ч-слоту (6+ стран последовательно убили бы 240s-таймаут и весь результат источника).
- `app/sources/adzuna.py` — `ADZUNA_SUPPORTED`: белый список рабочих эндпоинтов (ae/id у Adzuna нет — пропуск без 404).
- `app/sources/jooble.py` — локации 7 новых стран + ротация страны по слоту (1/скан, ~64 req/день из лимита 500).
- `app/static/dashboard.html` + `js/app.js` — плитки стран в Settings (🇦🇪🇸🇦🇶🇦🇦🇺🇳🇿🇮🇩🇸🇬) + имена в фильтре списка.
- Профиль user 1: preferred_countries → `["de","ae","au","nz","id"]` (смена profile_hash → плановый пере-скоринг через NVIDIA backfill).

Фаза 2 (отложена до оценки объёма): нативные борды Bayt (Gulf), JobStreet (Индонезия).

Контрольный скан после деплоя: **+988 AU, +171 NZ, +100 ID** за один прогон (341s). AE придёт со слотами ротации JobSpy (Adzuna в Gulf не работает).

Пост-аудит нашёл и закрыл два пробела:
- `app/sources/watchlist.py` — сканер компаний бил Adzuna по всем странам профиля без белого списка → 404 на каждую компанию × ae/id каждые 6ч. Теперь фильтр по `ADZUNA_SUPPORTED`.
- `app/services/scheduler_service.py` — `_semantic_skip_filter` проверял только наличие embedding профиля, но не свежесть: устаревший вектор (страны сменились, embed_index ещё не пере-индексировал) мог зря обнулять вакансии новых регионов. Теперь требуется `embedding_profile_hash = текущий profile_hash`, иначе всё идёт в AI без skip.

См. [[Источники вакансий#регионы-за-пределами-европы-gulf--океания--юва--10072026]].

## 26 июля 2026

### Gemini 3.5/3.6 и новый Google GenAI SDK

- `pyproject.toml` — legacy `google-generativeai` заменён на поддерживаемый `google-genai>=2,<3`.
- `app/scoring/gemini_client.py` — единый async client для генерации и embeddings, корректное закрытие transport в lifespan.
- `gemini-3.5-flash-lite` используется для массового real-time/backfill скоринга; `gemini-3.6-flash` — для подробного анализа одной вакансии.
- Batch-скоринг переведён на structured JSON Schema. Deprecated для 3.5/3.6 sampling controls (`temperature`, `top_p`, `top_k`) не передаются.
- Retry-классификатор понимает HTTP-коды и исключения старого/нового SDK.

### Зарплата полностью исключена из фильтрации и скоринга

- `user_profiles.min_salary` удаляется миграцией `0006_profile_feed_preferences`.
- Salary-setting удалён из Telegram-профиля.
- Salary context удалён из Claude/Gemini/NVIDIA prompts; `pre_filter` игнорирует salary при любом значении.
- `jobs.salary_min/max/currency` сохранены для отображения исходных данных, если источник их отдаёт.

### Скрытие стран из основной ленты

- `user_profiles.hidden_countries JSON` — отдельная presentation-настройка.
- Settings UI: красные country pills с i18n EN/RU/DE/ES.
- `/api/jobs` скрывает эти страны из All Jobs/Inbox по умолчанию; явный country-filter временно переопределяет скрытие; Applied/Rejected сохраняют полную историю.
- Сбор и AI-скоринг скрытых стран продолжаются. Изменение настройки не меняет `profile_hash`, не инвалидирует embedding и не вызывает лишний re-score.

### AI-промпт следует целевым должностям профиля

- Удалён старый хардкод «только Director+ в Supply Chain / Procurement / Operations / Logistics».
- `Target roles` из профиля стали источником истины для Gemini, Claude и NVIDIA; допустимы несколько направлений одновременно, включая transformation, restructuring, growth и AI strategy.
- Убраны зашитые в общий промпт данные конкретного кандидата: уровень немецкого, фиксированный набор отраслей и обязательность международной компании.
- Явная целевая должность остаётся предпочтением, а не доказательством квалификации: модель должна подтвердить соответствие опытом из резюме.
- Добавлена регрессия для `Chief Restructuring Officer` и `Director of AI Strategy`.

### Миграции и тесты

- Head Alembic: `0006_profile_feed_preferences`.
- Старые `0002/0003` сделаны кросс-БД: fresh SQLite больше не падает на PostgreSQL-only `ADD COLUMN IF NOT EXISTS`.
- Цепочка проверена с нуля и как upgrade `0005 → 0006`; данные профиля сохраняются.
- Добавлены тесты нормализации стран, profile-hash semantics, Gemini-конфига и инварианта «зарплата игнорируется».
- `pyproject.toml` получил явный setuptools package discovery (`app*`): `pip install .` снова воспроизводим и не падает на конфликте top-level `app`/`alembic`.
- Закрыт старый ruff backlog (19 замечаний); полный `ruff check .` проходит.
- `.gitignore` скрывает AppleDouble `._*`, `graphify-out/` и `.env.*` (кроме tracked `.env.example`), чтобы generated/backup-файлы сервера не засоряли deploy-status.

### English-only проверяет название вакансии

- При `english_only=True` pre-filter теперь отдельно распознаёт явно немецкие названия должностей: `Geschäftsführer`, `Einkaufsleiter`, `Leiter Logistik`, `Bereichsleiter`, `Produktmanager`, `Krisenmanager` и близкие формы.
- Явно немецкое название получает hard reject до AI, даже если описание содержит ложноположительный маркер `remote`, `global`, `international` или англоязычный footer job-board.
- `(m/w/d)`, немецкий город и страна сами по себе не являются причиной reject. Двуязычное название передаётся на существующую проверку описания.
- Подсказка настройки обновлена на EN/RU/DE/ES; добавлены регрессии на реальные заголовки из ленты.

### Commercial/Retail Operations исключены из рекомендаций

- `Operations` больше не считается достаточным функциональным совпадением: роли в `commercial`, `retail`, `sales`, `revenue` и `store operations` получают hard reject до AI.
- Исключение защищено от слишком широкого срабатывания: `Director of Retail Supply Chain`, `Commercial Procurement Director` и другие titles с явным `supply chain/procurement/sourcing/purchasing/logistics` остаются допустимыми.
- Добавлена регрессия на production-вакансию `Director of Retail and Commercial Operations`, ранее ошибочно получившую score 78.
- Закрыт обход hard reject через `recheck_zero_scores`: повторная Gemini-проверка сначала применяет актуальный профиль и больше не может повысить явно исключённую вакансию.
- Добавлен protected target-title match: точная целевая роль имеет приоритет над общими category/domain/seniority правилами. Нормализация учитывает `of` и `(m/w/d)`, но не удаляет функциональные модификаторы, поэтому широкая commercial-вакансия не маскируется под `Director Operations`.
- JobSpy/Indeed теперь сохраняет исходный поисковый запрос в `raw_data.query`, чтобы следующие false-positive/false-negative случаи можно было связать с конкретным retrieval-запросом, а не восстанавливать его предположительно.

См. [[Скоринг]], [[Настройки]], [[API]], [[Frontend]], [[Миграции]], [[База данных]].

## 27 июля 2026

### Исправление ложных отсечений Gulf-вакансий

Аудит UAE / Saudi Arabia / Qatar подтвердил, что сбор работал: вакансии поступали из Indeed, но старый pre-filter скрывал часть релевантных позиций до AI-скоринга. Главная причина — строка `nan` в пользовательских исключениях: substring-проверка находила её внутри слов вроде `financial`. Дополнительно названия автоматически исключённых компаний лежали в общем `excluded_keywords`, поэтому, например, упоминание SAP или Amazon в описании другого работодателя тоже блокировало вакансию.

- В профиле user 1 `english_only=False`; `nan` удалён из исключений.
- Миграция `0007_excluded_companies` добавляет отдельный `excluded_companies` и переносит туда реальные названия работодателей.
- Контентные `excluded_keywords` и компании теперь проверяются раздельно: фраза — по title/description, компания — только exact match по `company_name`.
- Auto-exclude после пяти отказов пишет только в `excluded_companies`.
- JobSpy больше не сохраняет pandas `NaN` как строковое имя компании.
- Settings/API/Admin показывают контентные фразы и заблокированные компании отдельными полями.

### Безопасный English-only

Старое правило требовало встретить маркер `English/global/remote`, поэтому полностью английская вакансия без одного из этих слов могла получить hard reject. Теперь, если настройка включена, `detect_description_language()` консервативно определяет EN/DE/FR/NL/ES/IT по служебным словам:

- уверенно неанглийские title/description блокируются;
- уверенно английский текст проходит даже без marker-слов;
- короткий и неоднозначный текст передаётся AI вместо ложного reject.

Для текущего профиля настройка отключена, но исправленная логика защищает будущие включения.

### Версионирование правил и полная инвалидация старых оценок

- `SCORING_RULES_VERSION` входит в `profile_hash`: изменение детерминированных правил теперь автоматически делает старые score stale.
- `excluded_companies` также входит в hash.
- Legacy `JobScore.profile_hash IS NULL` больше не считается вечным cache-hit.
- Все AI/prefilter UPSERT обновляют и legacy NULL-строки; одинаковый текущий hash остаётся no-op.
- После деплоя запущен пересчёт активных Gulf-стран `ae/sa/qa`.

Покрытие: раздельные exclusions, `nan`, точное company-match, English без marker-слов, немецкое описание при английском title, неоднозначный текст, изменение rules-version и перенос миграции `0006 → 0007`.

---

→ [[Changelog 2026-06]] → [[Changelog 2026-05]] → [[Roadmap]] → [[API]] → [[Скоринг]] → [[Трекер]] → [[Источники вакансий]]
