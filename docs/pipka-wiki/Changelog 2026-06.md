#changelog

# Changelog июнь 2026

## 5 июня 2026

### Сканы валились: tz-naive vs tz-aware в агрегаторе

Background scan (`_background_scan`, каждые 3ч) начал падать с `TypeError: can't compare offset-naive and offset-aware datetimes` на строке `aggregator.py:226`. Корень: `cutoff = datetime.now() - timedelta(...)` — naive, а `job.posted_at` от части источников (jobspy/jooble/wttj) приходит tz-aware через `datetime.fromisoformat(...)` без `.replace(tzinfo=None)`. Сравнение крашило весь scan, и видимая статистика на дашборде/Ops «застыла» с момента первой ошибки.

Ранее (28.04.2026) баг проявлялся однократно и самоотлечился — теперь стабильно воспроизводился (4 успешных скана подряд, затем 2 фейла подряд) на свежих данных одного из источников.

- `app/sources/aggregator.py` — нормализация на месте сравнения: если `posted_at.tzinfo is not None` → `replace(tzinfo=None)`. Одна точка вместо обхода каждого источника по отдельности.

См. [[Сервисы#scheduler]], [[Источники вакансий]].

---

→ [[Changelog 2026-05]] → [[Changelog 2026-04]] → [[Roadmap]] → [[Архитектура]] → [[Сервисы]] → [[Источники вакансий]]
