#changelog

# Changelog — август 2026

## 8 августа — production guards для платежей, CSP и semantic index

- `app/api/billing.py` и `app/services/billing_service.py`: live checkout и webhook теперь fail-closed без обоих Cryptomus credentials; test fulfilment доступен только в явном локальном sandbox-режиме без live credentials. Сравнение webhook-signature выполняется constant-time, а зачисление кредита блокирует строку транзакции и проверяет provider transaction ID.
- `app/security_headers.py`, `app/static/dashboard.html`, `app/static/js/events.js`: восстановлены `script-src-attr 'none'` и запрет JavaScript `unsafe-inline`; оставшиеся inline event handlers переведены на существующую `data-action` delegation.
- `app/services/embedding_service.py`: embedding queue ограничена открытыми German vacancies не старше 31 дня с AI score ≥60. Старый архив больше не будет постепенно расходовать embedding quota.
- Обновлены `Настройки.md`, `Поиск и индексация.md` и `API.md`; добавлены regression tests и обновлены ожидания после удаления Claude.

### NVIDIA burst для очереди embeddings

- `embed_index_burst` запускается каждые 30 минут только при `EMBEDDING_PROVIDER=nvidia` и при scoped queue строго больше 100 вакансий. Один запуск обрабатывает обычный пакет из 70 вакансий; при малом остатке сохраняется двухчасовой основной запуск.
- Ops events теперь корректно маркируют provider (`nvidia_embedding`), а не устаревший `gemini_embedding`.

### Точные queue KPI в Ops

- Заменена ложная карточка «все вакансии без score»: Ops отдельно показывает scope AI backfill, scope NVIDIA embeddings и исторический archive coverage. Alerts используют только первые две рабочие очереди.

## 7 августа — удаление Claude, NVIDIA-fallback и исправление блокировок БД

- **Полное удаление Claude API:** Из проекта полностью вырезана библиотека `anthropic`, удалены все Claude-переменные из настроек (`app/config.py`).
- **Перенаправление на NVIDIA:**
  - Логика общего скоринга (`score_jobs`) теперь автоматически маршрутизирует запросы на Gemini (основной) или NVIDIA (резервный).
  - Реальный скоринг и детальный анализ вакансий по кнопке в Telegram теперь автоматически переключаются на бесплатный **NVIDIA Nemotron/Llama** при исчерпании лимитов Gemini.
- **Исправление LockTimeout / LockNotAvailableError:**
  - Устранена гонка при старте: `run.py` теперь строго дожидается применения Alembic-миграций (`await init_db()`) перед запуском планировщика и Telegram-бота, предотвращая конкуренцию запросов за таблицы.
  - Транзакции в `embedding_service.py` теперь фиксируются (`commit()`) сразу после SELECT-запросов и после обновления каждого отдельного эмбеддинга, не блокируя БД во время длительных внешних HTTP-запросов к API NVIDIA.
- **Синхронизация суточных лимитов:** Время сброса лимитов и сброса circuit breaker'а Gemini синхронизировано с Тихоокеанским временем (полночь в Калифорнии = 08:00 UTC), решая проблему ложных 429-блокировок в утреннее время.
- **Исправление багов очереди (Backfill Scorer):**
  - Исправлен критический баг, из-за которого фоновый скоринг игнорировал новые вакансии, пропустившие оценку в реальном времени. В запрос добавлен выбор `or_(prior_strong_score, has_no_score)`.
  - При ручном запуске из очереди мгновенно убрано **252 вакансии** (отсеяны правилами pre-filter без затрат лимитов API), общее число неразобранных вакансий упало с 693 до 441.

## 6 августа — Gemini 3.6: пакетная очередь без quota storm

- Primary batch scorer переведён на `gemini-3.6-flash`; один запрос оценивает до 15 вакансий.
- Добавлен persistent `GEMINI_DAILY_REQUEST_LIMIT=20`: до 300 вакансий в UTC-день при полных пакетах. Попытки сохраняются в `ops_events`, поэтому рестарт контейнера лимит не обходит.
- `429/ResourceExhausted` больше не ретраится: breaker ставит Gemini на паузу до полуночи UTC. NVIDIA не подхватывает backfill автоматически, что устраняет поток `503`/`ReadTimeout`.
- Ручной detailed analysis выключен по умолчанию и скрыт в Telegram, чтобы не расходовать 3.6-квоту.
- После изменения профиля backfill берёт лишь ранее сильные (score ≥60) немецкие вакансии не старше 31 дня, исключает closed/unreachable и берёт 30 наиболее приоритетных за тик; исторические ~19k оценок не становятся массовой работой.

## 6 августа — NVIDIA Nemotron embeddings

- Semantic index переведён с Gemini Embedding на `nvidia/nemotron-3-embed-1b`: отдельная NVIDIA-квота, multilingual retrieval и обязательные `passage` (вакансия) / `query` (профиль) режимы.
- Alembic `0010` очищает несовместимые Gemini-векторы, меняет pgvector 768 → 2048 и пересоздаёт HNSW-индексы. Смешивать векторы двух моделей запрещено.

## 2 августа — access control и credential hardening

- Public Google/Telegram registration закрыта по умолчанию; добавлены email/Telegram allowlists. Inactive users блокируются в обоих каналах.
- Telegram получил общий pre-handler access guard, 6 search/hour и 10 detailed AI analysis/hour. Невалидная vacancy/profile больше не расходует AI quota.
- Telegram profile editor получил те же размерные cap'ы, что web.
- Admin API отдаёт только 1500-character resume preview, запрещает деактивацию себя/другого admin и пишет success/denied actions в `ops_events`.
- AI prompts явно маркируют profile/job text как untrusted data; model instructions из вакансии игнорируются. Невалидные/negative job indexes из model JSON отбрасываются единым validator'ом.
- Sentry scrub стал case-insensitive и редактирует secret-shaped tokens/emails даже в произвольных строках и breadcrumb messages. Raw model response при JSON parse error больше не логируется.
- CI получил tracked-file credential scan (`scripts/check_secrets.py`).
- Добавлены security regression tests; полный suite: 192 passed.

## 1 августа — полный code/security audit и production hardening

### Фильтры без ложных отсечений

- `intern` переведён на whole-word regex: `International` и `Internal` проходят, `Intern/Internship` отклоняются.
- COO opt-in срабатывает только на саму роль, а не на `COO Transformation Office/Organisation`.
- Embedding similarity больше не пишет `score=0`: exact target и похожие роли только поднимаются в очереди. Alembic `0008` удалил 868 synthetic `semantic_skip` rows на production.
- `SCORING_RULES_VERSION=2026-08-01.1`; кэш валиден только для `prefilter` или текущей primary model. Смена Gemini автоматически возвращает старые AI rows в backfill.

### Страны, dedup и даты

- `RawJob.dedup_hash` v2 включает title/company/country/location. Однаковый title одной компании в Singapore больше не скрывает Dubai/Saudi posting.
- Fuzzy dedup явно запрещает merge разных стран.
- Alembic `0009` пересчитал 13 850 production rows и сохранил historical duplicates/FK.
- Все source timestamps проходят общую UTC-normalization; timezone offset больше не отбрасывается через `.replace(tzinfo=None)`.

### Application security

- Исправлен middleware order; CSRF реально проверяет session token.
- Rate limit доверяет только sanitized `X-Real-IP` от loopback nginx. Nginx/UFW доверяют только official Cloudflare IP ranges.
- Dashboard XSS закрыт escaping, URL normalization и event delegation.
- URL checker блокирует SSRF на private/link-local/loopback IP, credentials, порты кроме 80/443 и перепроверяет redirects.
- PDF/DOCX parser работает в изолированном subprocess с rlimits, sanitized env, timeout/kill, defused XML и ZIP limits.
- Google OAuth требует verified email; public Swagger/OpenAPI в production отключен.

### VPS, containers, health и backups

- SSH root/password login отключён; `pipkaops` + key + sudo, Fail2ban active.
- App container запускается как UID 10001: read-only FS, no capabilities, no-new-privileges, tmpfs и persistent backup volume only.
- `/health` теперь проверяет DB, scheduler и last scan age; `/health/live` — shallow liveness; startup grace 90s.
- Dumps пишутся атомарно в worker thread. Каждое воскресенье full restore в disposable DB проверяет, что бэкап действительно восстанавливается.
- Restore-тест выявил несовместимость `pg_dump 17` с сервером PostgreSQL 16. Runtime закреплён на `postgresql-client-16`, а backup теперь проверяет совпадение major-версий до создания dump.
- Добавлен `.dockerignore`: серверный `.env`, приватные ключи, локальные данные и служебные каталоги больше не могут попасть в Docker image.
- Dashboard снова подключает CSRF fetch-wrapper до application JS: сохранение профиля, job actions, scan и logout передают `X-CSRF-Token`, а health/static probes больше не создают session cookies.
- Удалён устаревший `static/js/app.js`; все inline `onclick/onchange` заменены на `data-action` delegation. HTML получает per-response CSP nonce, `script-src-attr 'none'`, JavaScript `unsafe-inline` удалён.
- Fuzzy dedup больше не сравнивает каждую пару из 1k+ вакансий: обязательное совпадение normalised title используется как bucket key, поэтому сравниваются только реальные кандидаты. Production scan: **96.96s → 20.83s**, 1 573 unique jobs и только 667 candidate comparisons; следующий `embedding_index` завершился без misfire. Ops payload включает `fuzzy_comparisons`.
- Добавлен GitHub Actions CI на push/PR: pytest, Ruff, JS/CSP contract check, Compose validation и fresh Alembic upgrade на `pgvector:pg16`.

### Verification

- 162 tests проходили после filter/cache/dedup блока; infrastructure tests добавлены отдельно.
- Production head: `0009_geographic_dedup_hash`; primary scorer: `gemini:gemini-3.5-flash-lite`.

Остались ручные/следующие шаги: включить Backblaze B2 write-only key; вынести inline CSS/`style=` и убрать оставшийся `style-src 'unsafe-inline'`.

→ [[Changelog 2026-07]] → [[Безопасность]] → [[Деплой]] → [[Миграции]] → [[Roadmap]]
## 10 August — Gemini quota fallback for batch scoring

- When the Gemini 3.6 Flash circuit breaker is open or opens during a batch,
  the selected vacancies move to NVIDIA in the same scheduler run.
- The fallback is recorded as `scoring_fallback`; NVIDIA retries remain bounded
  and do not requeue an unlimited historical backlog.
