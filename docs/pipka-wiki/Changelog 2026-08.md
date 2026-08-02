#changelog

# Changelog — август 2026

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

### Verification

- 162 tests проходили после filter/cache/dedup блока; infrastructure tests добавлены отдельно.
- Production head: `0009_geographic_dedup_hash`; primary scorer: `gemini:gemini-3.5-flash-lite`.

Остались ручные внешние шаги: включить Backblaze B2 write-only key, затем вынести inline dashboard JS/CSS и убрать CSP `unsafe-inline`.

→ [[Changelog 2026-07]] → [[Безопасность]] → [[Деплой]] → [[Миграции]] → [[Roadmap]]
