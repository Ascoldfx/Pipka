#ratelimit

# Rate limiting

In-process sliding-window лимитер. Стоит на дорогих эндпоинтах, чтобы один логиненный пользователь не выжег дневную AI-квоту click-spam'ом.

Файл: `app/api/_ratelimit.py`.

## Контракт

```python
from app.api._ratelimit import check_rate_limit

check_rate_limit(user_id=user.id, key="analyze", limit=30, window_s=3600)
# Кидает HTTPException(429) с Retry-After если превышен лимит.
```

## Реализация

State: `dict[(user_id, key), deque[float]]` под `threading.Lock`. Ключ — пара `(user_id, key)`, значение — deque монотонных таймстампов вызовов.

На каждый `check_rate_limit`:

1. `now = time.monotonic(); cutoff = now - window_s`.
2. Из левой стороны deque удаляются записи старше `cutoff` (сладинг-окно).
3. Если `len(bucket) >= limit` → 429 + `Retry-After: <seconds_until_oldest_expires>`.
4. Иначе `bucket.append(now)`.

Память: O(limit × количество (user, key) пар). При limit=30 и 10 user'ах с одним key — 300 float в памяти. Не критично.

## Где используется

| Эндпоинт | key | limit | window |
|----------|-----|-------|--------|
| `GET /api/jobs/{id}/analyze` | `"analyze"` | 30 | 3600s (1 час) |

Каждый клик на "🤖 AI Анализ" в UI → один запрос к Gemini/Claude. Без cap'а юзер мог за 30 сек продёрнуть 100 запросов и потратить 100/500 RPD дневной квоты.

Real-time `_score_and_notify` (3-часовой scan) и `_backfill_score` НЕ ограничены — они и так bounded'ы scheduler'ом и `MAX_SCORED_PER_SEARCH` (см. [[Скоринг]]).

## Когда переезжать на Redis

Текущая реализация **single-process**. Если когда-нибудь будет `docker compose scale app=2` — лимиты разойдутся между репликами и каждая разрешит свои 30 запросов в час.

План миграции: `slowapi` + `Redis` storage. Точка переключения — переход на multi-replica (см. [[Roadmap]]).

## Auth-side ограничения (out of scope)

- Cloudflare DDoS protection — на уровне домена, не наш код.
- Telegram bot долгополлинг — Telegram сам ограничивает 30 msg/sec до бота.
- OAuth retry — authlib делает 3 попытки с back-off, отдельной квоты не считаем.

## Не делает

- Не различает GET/POST/etc — лимит на функцию, не на HTTP метод.
- Не разделяет по IP — авторизованный пользователь идентифицируется по `user.id`.
- Не персистит между рестартами — после `docker compose up` лимиты обнуляются. Это feature: восстановление сервиса не наказывает пользователя.

→ [[API#jobs]] → [[Скоринг]] → [[Безопасность]]
