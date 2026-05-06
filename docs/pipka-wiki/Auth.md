#auth

# Аутентификация и сессии

Один способ логина — Google OAuth2 через `authlib`. Telegram bot работает по `telegram_id` (без OAuth).

Файлы: `app/api/auth.py`, `app/services/user_service.py`, `app/api/_helpers.py`, middleware в `app/main.py`.

## OAuth flow

```
Browser → /auth/google/login
    ↓
authlib.oauth.google.authorize_redirect(redirect_uri="https://pipka.net/auth/google/callback")
    ↓
[Google consent screen — scope: openid email profile]
    ↓
GET /auth/google/callback?code=...
    ↓
authlib.oauth.google.authorize_access_token() — обмен code → token + userinfo
    ↓
get_or_create_google_user(google_sub, email, name, avatar, session)
    ↓
request.session["user_id"] = user.id
request.session["user_role"] = "admin" if email in ADMIN_EMAILS else "user"
    ↓
RedirectResponse("/")
```

В проде `redirect_uri` принудительно переписывается с `http://` на `https://` — иначе Cloudflare-proxy за ним сломает callback.

**Session fixation defense (Day-1 фикс, май 2026):** в callback'е до записи `user_id` вызывается `request.session.clear()`. Если атакующий до login'а подсунул жертве `pipka_session` cookie (через subdomain XSS / MITM), его plant'нутая сессия теряет identity на login.

## get_or_create_google_user

Файл: `app/services/user_service.py`. Алгоритм:

1. Ищем существующего по `google_sub` → нашли → обновляем `avatar_url`, return.
2. Не нашли по sub → ищем по `email` (legacy: пользователь мог зарегаться через Telegram, потом добавить Google) → нашли → привязываем `google_sub`, return.
3. Никого не нашли → создаём нового. Роль:
   - `admin` если `email.lower() in ADMIN_EMAILS` (env, comma-sep).
   - `user` иначе.

## Сессия

`SessionMiddleware` (Starlette) подписывает cookie `pipka_session` секретом `SESSION_SECRET` (см. [[Настройки]]). Параметры:

| Атрибут | Значение |
|---------|---------|
| `max_age` | 30 дней |
| `same_site` | `lax` |
| `https_only` | `True` |
| Тип | подписанный JWT-like (itsdangerous) |

В session кладутся: `user_id`, `user_email`, `user_name`, `user_avatar`, `user_role`, `csrf_token` (см. [[Безопасность#csrf]]).

## Хелперы (доступ к user/role)

`app/api/_helpers.py`:

- `get_user(request, session)` — ORM-объект `User` с eager-loaded `profile`, или `None`. Используется в каждом эндпоинте, где нужен текущий пользователь.
- `get_role(request, user)` — `"admin" | "user" | "guest"`. Sync, читает session-cookie. Используется только для UI-cosmetics (показать ли admin-вкладку); может отдавать stale-роль до 30 дней. **Не использовать для авторизации.**
- `require_authenticated(request)` → 401 если нет `user_id` в сессии.
- `require_admin_async(request)` → читает `User.role` из БД с per-user TTL-cache 60s (`_ROLE_CACHE` в `_helpers.py`). Если admin revoked в БД — теряет доступ за ≤60s. Это правильный путь для всех protected admin endpoint'ов.
- `require_admin(request)` (sync, **deprecated**) — берёт роль из session, та же 30-day stale-проблема. Оставлен для совместимости.

**Cache invalidation:** `admin_delete_user` явно вызывает `_drop_role_cache(user_id)` сразу после revoke — на multi-replica один воркер не должен держать stale-роль удалённого user'а.

## Гостевой режим

Без логина (`user_id` не в сессии) — `/api/me` возвращает `{authenticated: false, role: "guest"}`. Frontend (см. [[API#jobs]]) показывает кнопку "Sign in with Google", скрывает Inbox/Applied/Settings, выставляет `min_score=0`. Можно листать всю агрегированную базу вакансий read-only.

## Logout

`POST /auth/logout` → `request.session.clear()` → JSON `{"ok": true}`. Frontend (`app.js`) отправляет через `fetch('POST')` чтобы прошёл CSRF-чек, затем navigate'ит на `/`. Раньше был GET — эксплоился через `<img src="/auth/logout">` в любой странице которую посещал залогиненный user.

## Safety / связи

- CSRF на POST/PUT/PATCH/DELETE — см. [[Безопасность#csrf]].
- `admin_emails` — единственный механизм назначения роли admin при первом логине. После логина роль кэшируется в БД и в session — изменение env не понизит уже-admin'а до user.
- Сессия не привязывается к IP, поэтому работает из мобильного приложения / разных устройств. Подмена cookie невозможна без `SESSION_SECRET`.

→ [[API#auth]] → [[Безопасность]] → [[Настройки#google-oauth]]
