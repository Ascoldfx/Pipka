#ops

# Ops-панель

Админский инструмент мониторинга в Dashboard'е (вкладка `Ops`). Отдаёт две картины:

- **Health & throughput** — `/api/ops/overview`
- **Дедуп-эффект** — `/api/ops/dedup`

Бэкенд: `app/api/ops.py` + `app/services/ops_service.py`. Источник данных — таблица [[База данных#ops_events|ops_events]].

> Хардкод на русском (не i18n) — целевая аудитория одна, лень на шесть переводов.

## /api/ops/overview

```
GET /api/ops/overview?window_hours=24
```

Параметр `window_hours: int = Query(24, ge=6, le=168)`. По умолчанию — за последние 24 часа.

Ответ собирает `build_ops_overview(session, user_id, window_hours, next_run_at, scan_running)`:

| Блок | Содержимое |
|------|-----------|
| **Pipeline** | `total_jobs`, `jobs_recent` (за окно), `jobs_week`, `scored_total`, `scored_recent`, `top_matches` (score≥70), `pending` (есть Job, нет JobScore у user'а) |
| **Sources** | Кол-во вакансий по каждому источнику, в т.ч. `merged_sources > 1` |
| **Scheduler** | `next_run_at` (`background_scan`), `scan_running` (флаг in-flight), `last_scan_duration` |
| **Events** | Последние N записей `ops_events` за окно: `event_type, status, source, message, payload, created_at` |
| **Applications** | Сколько `applied`/`rejected`/`offer` у user за неделю |

## /api/ops/dedup

```
GET /api/ops/dedup?limit=200
```

Возвращает вакансии, у которых `merged_sources` массив длины > 1 — то есть слитые fuzzy-дедупом из нескольких источников.

```sql
SELECT * FROM jobs
WHERE raw_data->'merged_sources' IS NOT NULL
  AND jsonb_array_length(raw_data->'merged_sources') > 1
ORDER BY scraped_at DESC
LIMIT $limit
```

Ускорено GIN-индексом `ix_jobs_merged_sources` на `(raw_data->'merged_sources')` (см. [[Миграции|0001_baseline]]).

Используется чтобы:
- Видеть, насколько эффективно работает [[Дедупликация|fuzzy-дедуп]].
- Ловить ложные мерджи (одна и та же company с разными вакансиями в разных городах — должны были разделиться по `_locations_conflict`).

## ops_events

Таблица `ops_events` — централизованный журнал. Запись через `record_ops_event(event_type, status, source=, message=, payload=)` (`app/services/ops_service.py`). Запись fail-open: если БД упала, логируется warning, но основной flow не падает.

### Текущие event_types

| event_type | status | Источник | Когда пишется |
|------------|--------|----------|---------------|
| `scan` | `success`/`error` | `_background_scan` | По завершении/падении основного 3ч-скана |
| `gemini_429` | `retry` | `_call_gemini_raw` | Каждая ResourceExhausted с tenacity-ретраем |
| `gemini_exhausted` | `error` | `_call_gemini_raw` | После 5-й попытки |
| `gemini_breaker_open` | `warning` | `_record_exhaust` | 3 подряд exhausted → breaker до полуночи UTC |
| `nvidia_429`, `nvidia_exhausted` | как у gemini | NVIDIA matcher | Аналогично, для NVIDIA Build |
| `nvidia_rescore` | `success` | `_nvidia_idle_rescore` | После каждого тика idle-rescore'а |
| `backup` | `success`/`error` | `_daily_backup` | После cron в 02:30 UTC |
| `api_error` | `error`/`warn` | `NoCacheAPIMiddleware` | 5xx или необработанная exception на API |
| `cleanup` | `success`/`error` | `_cleanup_old_jobs` | После daily cleanup в 03:00 UTC |

Подробнее по конкретным событиям — [[Скоринг#надёжность]], [[Observability]].

### Размер и ретеншн

`ops_events` сейчас **не ротируется** автоматически. Ретеншн-cron — пункт из [[Roadmap]]. На текущем темпе (~50–150 событий в сутки) таблица вырастает на ~50KB/день — не блокер.

## Read path

Frontend: `app/static/js/app.js:loadOpsOverview()` тянет `/api/ops/overview` каждые `S.opsWindow` секунд (по умолчанию 24 часа, кнопки 6/24/72/168). Рисует:

- KPI-карточки (Total Jobs, Top, Inbox, ...).
- Pipeline timeline (jobs_recent vs scored_recent).
- Source breakdown — bar chart.
- Events feed — последние ~50, цветные по `status`.
- Dedup table — отдельно через `/api/ops/dedup`.

→ [[API#ops]] → [[Сервисы]] → [[Observability]] → [[База данных#ops_events]]
