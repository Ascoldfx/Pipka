#changelog

# Changelog — август 2026

## 6 августа — Gemini 3.6: пакетная очередь без quota storm

- Primary batch scorer переведён на `gemini-3.6-flash`; один запрос оценивает до 15 вакансий.
- Добавлен persistent `GEMINI_DAILY_REQUEST_LIMIT=20`: до 300 вакансий в UTC-день при полных пакетах. Попытки сохраняются в `ops_events`, поэтому рестарт контейнера лимит не обходит.
- `429/ResourceExhausted` больше не ретраится: breaker ставит Gemini на паузу до полуночи UTC. NVIDIA не подхватывает backfill автоматически, что устраняет поток `503`/`ReadTimeout`.
- Ручной detailed analysis выключен по умолчанию и скрыт в Telegram, чтобы не расходовать 3.6-квоту.
- После изменения профиля backfill берёт лишь ранее сильные (score ≥60) немецкие вакансии не старше 31 дня, исключает closed/unreachable и берёт 30 наиболее приоритетных за тик; исторические ~19k оценок не становятся массовой работой.

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
