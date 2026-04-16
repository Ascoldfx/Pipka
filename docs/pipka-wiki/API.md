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
| GET | `/api/jobs` | `page`, `per_page`, `sort`, `order`, `search`, `country`, `source`, `min_score`, `status` | Список вакансий с пагинацией |
| POST | `/api/jobs/{job_id}/action` | `action=save/applied/reject` | Действие с вакансией |
| GET | `/api/jobs/{job_id}/analyze` | — | Детальный AI-анализ вакансии |

### Статистика

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/api/stats` | `{total_jobs, scored_jobs, applied_jobs, rejected_jobs, top_score}` |

### Профиль

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/api/profile` | Профиль пользователя |
| POST | `/api/profile` | Сохранить профиль (Form data) |
| POST | `/api/profile/resume` | Загрузить резюме (PDF/TXT/DOC) |

#### Поля POST /api/profile
```
resume_text, target_titles, min_salary, languages,
experience_years, industries, work_mode,
preferred_countries, base_location,
excluded_keywords, english_only (0/1)
```

---

## Health

| Метод | Путь | Ответ |
|-------|------|-------|
| GET | `/health` | `{"status": "ok", "service": "pipka"}` |

---

## Авторизация в dashboard

```python
async def _get_user(request, session):
    # 1. Session cookie (Google OAuth) → primary
    user_id = request.session.get("user_id")
    if user_id: return user by id

    # 2. Legacy fallback: первый активный пользователь (Telegram)
    return first active user
```

Роль: `admin` — полный доступ, `user` — только свои данные, `guest` — только просмотр All Jobs.

---

## Гостевой режим

- Показывается полный список вакансий (`min_score=0`)
- Скрыты вкладки: Inbox, Applied, Rejected, Settings
- Скрыты Stats: Scored, Applied, Rejected
- Нет кнопок действий (apply/reject/save)
- Кнопка "Sign in with Google"

→ [[Архитектура]] → [[База данных]] → [[Настройки]]
