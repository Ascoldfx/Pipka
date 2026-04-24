#api

# API Endpoints

Все эндпоинты на `https://pipka.net`

## Auth (`app/api/auth.py`)

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/auth/google/login` | Редирект на Google OAuth |
| GET | `/auth/google/callback` | Callback от Google, создаёт сессию |
| GET | `/auth/logout` | Очищает сессию, редирект на `/` |
| GET | `/api/me` | Текущий пользователь (`{authenticated, role, name, email, avatar_url}`) |

### Сессия
Cookie `pipka_session` (signed, 30 дней, HTTPS-only, SameSite=lax).

---

## Dashboard (`app/api/dashboard.py`)

### Вакансии

| Метод | Путь | Параметры | Описание |
|-------|------|-----------|---------|
| GET | `/api/jobs` | `page`, `per_page`, `sort`, `order`, `search`, `country`, `countries` (comma-sep), `source`, `min_score`, `status`, `region` | Список вакансий с пагинацией |
| POST | `/api/jobs/{job_id}/action` | `action=save/applied/reject` | Действие с вакансией |
| GET | `/api/jobs/{job_id}/analyze` | — | Детальный AI-анализ вакансии |

> **Значения `source`:** `adzuna`, `linkedin`, `indeed`, `glassdoor`, `arbeitnow`, `remotive`, `arbeitsagentur`, `xing`, `berlinstartupjobs`, `wttj`, `watchlist`
> **Значения `region`:** `saxony`, `germany`, `dach`, `europe`, `cee`
> **Значения `sort`:** `score`, `date`, `salary`, `title`, `company`

### Статистика и прочее

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/api/stats` | `{total_jobs, scored, top_matches, applied, rejected, inbox, sources}` |
| GET | `/infographic` | Возвращает публичный дашборд-инфографику в HTML |
| GET | `/api/public/stats` | Публичная статистика платформы: `{total_jobs_processed, ai_analyses_performed, jobs_last_24h, active_sources, system_status}` |
| GET | `/api/countries` | Список стран с количеством вакансий |
| POST | `/api/scan` | Запустить сканирование вручную (только admin) |
| GET | `/api/scan/status` | Состояние планировщика (`{next_run, running}`) |
| GET | `/api/ops/overview` | Операционная сводка системы (только admin). Query: `window_hours` (6–168, default 24) |
| GET | `/api/ops/dedup` | Список вакансий объединённых fuzzy-дедупом (`merged_sources` > 1). Query: `limit` (10–500, default 200). Только admin. |
| GET | `/api/admin/user/{user_id}/profile` | Профиль пользователя + агрегаты по JobScore (только admin) |
| DELETE | `/api/admin/user/{user_id}` | Удаление пользователя каскадно (profile, scores, applications). Только admin |

### Профиль

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/api/profile` | Профиль пользователя |
| POST | `/api/profile` | Сохранить профиль (Form data) |
| POST | `/api/profile/resume` | Загрузить резюме (PDF/DOCX/TXT, max 10MB) |

#### Поля POST /api/profile
```
resume_text, target_titles, min_salary, languages,
experience_years, industries, work_mode,
preferred_countries, excluded_keywords,
english_only (0/1), target_companies
```
> `base_location` удалён (апрель 2026)

---

## Health (`app/api/health.py`)

| Метод | Путь | Ответ |
|-------|------|-------|
| GET | `/health` | `{"status": "ok", "service": "pipka"}` |

---

## Авторизация в dashboard

```python
async def _get_user(request, session):
    # Session cookie (Google OAuth) — единственный метод аутентификации
    user_id = request.session.get("user_id")
    if user_id: return user by id
    return None  # не аутентифицирован
```

Роль: `admin` — полный доступ, `user` — только свои данные, `guest` — только просмотр All Jobs.

Роль определяется из `request.session["user_role"]` (устанавливается при OAuth callback) или из поля `user.role` в БД.

---

## Гостевой режим

- Показывается полный список вакансий (`min_score=0`)
- Скрыты вкладки: Inbox, Applied, Rejected, Settings
- Скрыты Stats: Scored, Applied, Rejected
- Нет кнопок действий (apply/reject/save)
- Кнопка "Sign in with Google"

→ [[Архитектура]] → [[База данных]] → [[Настройки]]
