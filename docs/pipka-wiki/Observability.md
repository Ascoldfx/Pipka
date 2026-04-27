#observability

# Observability

Три слоя: **structured logging** (Python logging), **OpsEvent журнал** в БД, **Sentry** для исключений и перформанса.

## 1. Python logging

Стандартный `logging` модуль, level через `LOG_LEVEL` env (default `INFO`). Все модули используют named logger — `logger = logging.getLogger(__name__)`.

Docker-логи json-file driver, ротация `10MB × 3` файла на контейнер (см. [[Деплой]]). Хватает на ~6–24 часа активности. После — теряются.

Просмотр в реальном времени:

```bash
ssh root@217.76.61.28 -i ~/.ssh/id_ed25519
docker logs -f pipka-app-1 --since 5m
docker logs -f pipka-app-1 --since 5m 2>&1 | grep -E 'ERROR|Traceback|Scan finished'
```

Dedicated access logger `pipka.access` логирует:
- 4xx/5xx ответы.
- Все мутирующие методы (POST/DELETE/PATCH) + `user_id`.

Конфиг — `NoCacheAPIMiddleware` в `app/main.py`.

## 2. OpsEvent — структурированный журнал в БД

Таблица [[База данных#ops_events|ops_events]] — централизованный журнал доменных событий. В отличие от docker-логов, переживает рестарты и доступен через [[Ops панель|UI]].

Запись: `await record_ops_event(event_type, status, source=, message=, payload=)` (`app/services/ops_service.py`).

Контракт `record_ops_event` **fail-open**: если БД недоступна, пишется warning в logger и функция молча возвращается — наблюдаемость не должна ломать основной flow.

Полный список `event_type` и когда они пишутся — см. [[Ops панель#ops_events]].

## 3. Sentry

`sentry-sdk[fastapi]>=2.18`. Инициализация в `app/main.py` ДО создания FastAPI app — иначе ASGI-хуки SDK не повесятся.

```python
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,            # default "production"
        traces_sample_rate=settings.sentry_traces_sample_rate,   # 0.05
        profiles_sample_rate=settings.sentry_profiles_sample_rate, # 0.05
        attach_stacktrace=True,
        send_default_pii=False,    # не отправляем session cookies / IP
        integrations=[AsyncioIntegration(), SqlalchemyIntegration()],
    )
```

`SENTRY_DSN` пустой → SDK не инициализируется, нулевая нагрузка. Для активации — взять DSN на sentry.io (бесплатный тариф 5000 событий/мес), вставить в `/opt/pipka/.env`, рестарт.

Что попадает:
- Все необработанные exception'ы из FastAPI handler'ов.
- 5% запросов с performance traces (`traces_sample_rate=0.05`).
- 5% профилирование CPU.
- SQLAlchemy spans (медленные запросы, N+1).

Что НЕ попадает (для приватности):
- session cookies, IP, headers.
- payload из `OpsEvent` (только то, что попадает в exception).

См. [[Настройки#sentry-опционально]] для всех env-параметров.

## NoCacheAPIMiddleware

`app/main.py:NoCacheAPIMiddleware` — два побочных эффекта на каждом API-ответе:

1. **Cache-Control: no-store** на всё, что отдают `/`, `/api/*`, `/auth/*`. Браузеры не кэшируют — JSON всегда свежий.
2. **OpsEvent на 4xx/5xx** — успешные POST не пишутся (шум), но любая ошибка попадает в `ops_events` с типом `api_error`. Скрипт-сканеры на `/api/.env`, `/api/config`, `/api/vendor/...` фильтруются (`is_probe = 404 + GET`) — не засоряют ops.

## Метрики, которых пока нет

- **Prometheus `/metrics`** — пункт [[Roadmap]]. Базовый набор `http_requests_total{path,status}`, `gemini_calls_total{result}`, `scan_duration_seconds`.
- **Healthcheck "deep"** — сейчас `/health` возвращает фиксированный `{"status":"ok"}`, не проверяет БД/планировщик. Apt for: добавить `db_ping`, `scheduler_alive`, `last_scan_age_seconds`.
- **Tracing** — Sentry traces покрывают; OpenTelemetry не настраивали.

→ [[Ops панель]] → [[Настройки#sentry-опционально]] → [[Безопасность]]
