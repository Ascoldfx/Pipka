#api

# API Endpoints

Все эндпоинты на `https://pipka.net`. После рефакторинга `dashboard.py` (26.04.2026) разнесены по 8 файлам в `app/api/` (см. [[Архитектура]]). Один `APIRouter` на файл, все включаются в `app/main.py`.

## Auth (`app/api/auth.py`)

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/auth/google/login` | Редирект на Google OAuth |
| GET | `/auth/google/callback` | Callback от Google, создаёт сессию |
| GET | `/auth/logout` | Очищает сессию, редирект `/` |
| GET | `/api/me` | `{authenticated, role, name, email, avatar, csrf_token}` |

Подробнее — [[Auth]].

## Pages (`app/api/pages.py`)

| Метод | Путь | Возвращает |
|-------|------|-----------|
| GET | `/` | dashboard.html — SPA |
| GET | `/llms.txt` | манифест для AI-краулеров |
| GET | `/infographic` | infographic.html — публичная инфографика |

## Jobs (`app/api/jobs.py`)

### Просмотр

| Метод | Путь | Параметры | Описание |
|-------|------|-----------|---------|
| GET | `/api/countries` | — | Список стран с количеством вакансий |
| GET | `/api/jobs` | `page`, `per_page`, `sort`, `order`, `search`, `country`, `countries`, `source`, `min_score`, `status`, `region`, `include_closed`, `semantic` | Список вакансий с пагинацией |

`include_closed=1` — показывать закрытые ([[Проверка ссылок]]). По умолчанию скрываются.

`semantic=1` — pre-rank через cosine-similarity к embedding профиля ([[Поиск и индексация]]); top-`SEMANTIC_SEARCH_LIMIT` (default 500) кандидатов сортируются по близости. Без флага — обычный SQL-сорт по `sort` колонке.

`search=…` на PostgreSQL использует tsvector + `websearch_to_tsquery`. На SQLite (dev) — fallback `ILIKE`.

> **Значения `source`:** `adzuna`, `linkedin`, `indeed`, `glassdoor`, `arbeitnow`, `remotive`, `arbeitsagentur`, `xing`, `berlinstartupjobs`, `wttj`, `jooble`, `watchlist`
> **Значения `region`:** `saxony`, `germany`, `dach`, `europe`, `cee`
> **Значения `sort`:** `score`, `date`, `salary`, `title`, `company`

### Действия

| Метод | Путь | Параметры | Описание |
|-------|------|-----------|---------|
| POST | `/api/jobs/{job_id}/action` | `action=save/applied/reject` | Действие через [[Трекер]] |
| GET | `/api/jobs/{job_id}/analyze` | — | AI-анализ через Gemini/Claude. **Rate-limit: 30/час/user** ([[Rate limiting]]) |

POST требует CSRF-заголовок — [[Безопасность#3-csrf-double-submit]].

## Stats (`app/api/stats.py`)

| Метод | Путь | Кэш | Описание |
|-------|------|-----|---------|
| GET | `/api/stats` | 30s/user | `{total_jobs, scored, top_matches, applied, rejected, inbox, sources}` |
| GET | `/api/public/stats` | 5min global | Публичная статистика для лэндинга/инфографики |

`/api/stats` инвалидируется через `invalidate_stats_cache(user_id)` после `job_action` и `update_profile`.

## Profile (`app/api/profile.py`)

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/api/profile` | Профиль текущего пользователя |
| POST | `/api/profile` | Сохранить профиль (Form data) |
| POST | `/api/profile/resume` | Upload резюме (PDF/DOCX/TXT, ≤10 MB) |

### Поля POST /api/profile

```
resume_text, target_titles, min_salary, languages,
experience_years, work_mode, preferred_countries,
excluded_keywords, english_only (0/1), target_companies
```

Валидация — [[Безопасность#4-input-validation]].

Изменение профиля → новый `profile_hash` → постепенная пере-оценка stale-строк ([[Кэш и инвалидация]]).

## Scan (`app/api/scan.py`)

| Метод | Путь | Доступ | Описание |
|-------|------|--------|---------|
| POST | `/api/scan` | admin | Запустить scan вручную |
| GET | `/api/scan/status` | публичный | `{next_run, running}` |

## Ops (`app/api/ops.py`)

| Метод | Путь | Доступ | Описание |
|-------|------|--------|---------|
| GET | `/api/ops/overview?window_hours=24` | admin | Health & throughput |
| GET | `/api/ops/dedup?limit=200` | admin | Fuzzy-merged вакансии |

Подробнее — [[Ops панель]].

## Admin (`app/api/admin.py`)

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/api/admin/user/{user_id}/profile` | Полный профиль + статистика по user'у |
| DELETE | `/api/admin/user/{user_id}` | Soft-delete (`is_active=False`) |

Все требуют `require_admin` ([[Auth#хелперы]]).

## Health (`app/api/health.py`)

| Метод | Путь | Ответ |
|-------|------|-------|
| GET | `/health` | `{"status": "ok", "service": "pipka"}` |

Сейчас это shallow check. Deep healthcheck — пункт [[Roadmap]].

## CSRF на mutating запросах

POST/PUT/PATCH/DELETE требуют заголовок `X-CSRF-Token`, равный `csrf_token` cookie. JS-обёртка fetch автоматически подмешивает (см. [[Безопасность#3-csrf-double-submit]]).

Исключения: `/auth/*` (Google callback), `/health`.

## Гостевой режим

Без логина:
- `/api/me` → `{authenticated: false, role: "guest"}`.
- `/api/jobs` показывает все вакансии без `min_score` фильтра.
- Скрыты Inbox, Applied, Rejected, Settings вкладки.
- Нет кнопок действий.

Подробнее — [[Auth#гостевой-режим]].

→ [[Архитектура]] → [[Auth]] → [[База данных]] → [[Безопасность]] → [[Настройки]]
