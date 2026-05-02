#scoring #prefilter

# Pre-filter правила

Файл: `app/scoring/rules.py`. Чисто-Python, без сетевых вызовов и без БД. Запускается в горячем пути `_score_and_notify` и `_backfill_score` ([[Сервисы#scheduler]]) ДО любого AI-обращения, чтобы не сжигать квоту Gemini/Claude/NVIDIA на заведомо нерелевантные вакансии.

Возвращает `tuple[bool, str]`:
- `True, "high"` — director/VP/head + domain match → AI-скоринг tier 1 ([[Скоринг]])
- `True, "medium"` — senior manager / lead + domain → AI-скоринг tier 1 (но с меньшим приоритетом сортировки в backfill)
- `False, "manager_tier2"` — plain "manager" + domain → AI-скоринг tier 2 (только когда tier 1 пуст)
- `False, "low"` — hard reject, в БД пишется `JobScore(score=0, model_version="prefilter")` ([[Кэш и инвалидация#prefilter sentinel]])

## Списки ключевых слов

### `DIRECTOR_KEYWORDS` — senior-level title

EN: `director`, `head of`, `vp`, `vice president`, `chief`, `coo`, `cfo`, `cpo`, `cso`, `cro`, `senior director`, `global director`, `principal`, `partner`.

Interim/Crisis/Turnaround (трактуются как senior-by-nature): `interim manager/director/head`, `crisis manager/director`, `krisenmanager` (DE), `turnaround manager/director`, `restructuring`, `growth director`.

DE: `direktor`, `leiter`, `abteilungsleiter`, `bereichsleiter`, `geschäftsführer`, `geschaeftsfuehrer`.

### `REJECT_TITLE_KEYWORDS` — hard-reject по title

Junior/operational: `specialist`, `analyst`, `coordinator`, `assistant`, `clerk`, `sachbearbeiter`, `referent`, `mitarbeiter`, `fachkraft`, `junior`, `trainee`, `werkstudent`, `praktikant`, `azubi`, `intern`, `student`, `buyer`, `dispatcher`, `planner`, `merchandiser`.

Wrong function (не Supply Chain / Procurement / Operations): `marketing`, `sales director`, `account executive/manager`, `hr director/manager`, `human resources`, `people operations/lead`, `talent`, `recruiting/recruitment`, `engineering manager`, `software`, `developer`, `data scientist`, `product manager/director/lead`, `finance director`, `financial controller`, `accounting`, `legal`, `compliance director`, `regulatory`, `creative director`, `design director`, `art director`, `editorial`, `content director`, `communications director`, `customer success/service`, `support manager`, `research director`, `r&d director`, `scientific`, `medical director`, `clinical`, `real estate`, `property`, `founding`, `co-founder`, `consultant`, `consulting`, `berater`, `beratung`, `advisory`, `advisor`.

### `DOMAIN_KEYWORDS` — нужно совпадение для прохождения

`supply chain`, `procurement`, `einkauf`, `beschaffung`, `logistics`, `logistik`, `operations`, `s2p`, `source to pay`, `sourcing`, `purchasing`, `lieferkette`, `warehouse`, `lager`, `demand planning`, `inventory`, `distribution`, `fulfillment`, `supplier`, `vendor management`, `category management`, `strategic sourcing`, `indirect/direct procurement`.

Crisis-related: `crisis management`, `turnaround`, `transformation`, `restructuring`, `interim management`, `business continuity`, `operational excellence`, `continuous improvement`, `growth`.

### `ENGLISH_FRIENDLY_SIGNALS`

`english`, `international`, `global`, `multinational`, `working language: english`, `english-speaking`, `startup`, `remote`. Используется и при бакетинге high vs medium, и для filter'а `english_only` в профиле.

### `FOREIGN_LANGUAGE_REQUIRED` — hard-reject

Триггеры на french/spanish/polish и т.п. в description: `langue requise`, `français requis`, `francais courant`, `maîtrise du français`, аналогично для других языков. Вакансии где требуется чужой язык кроме EN/DE — отбрасываются.

## Порядок проверок

1. **Junior/wrong function** — `REJECT_TITLE_KEYWORDS` в title → `low`
2. **Foreign language required** — `FOREIGN_LANGUAGE_REQUIRED` в description → `low`
3. **User exclusions** — `profile.excluded_keywords` в title+description → `low`. Триггерит [[Трекер#auto-exclude]] косвенно: компании с >5 reject'ами пользователя автоматически добавляются сюда.
4. **English-only filter** — если `profile.english_only=True` И нет ни одного из `ENGLISH_FRIENDLY_SIGNALS` → `low`
5. **Domain check** — нет `DOMAIN_KEYWORDS` в title или description → `low` (вакансия не из нашей области)
6. **Work mode filter** — соответствие `profile.work_mode` (`remote`/`onsite`/`hybrid`/`any`) и `Job.is_remote` + ключевых слов
7. **Country check** — `Job.country` должен быть в `profile.preferred_countries`
8. **Seniority bucketing** — `is_director` / `is_senior_manager` / `is_plain_manager` решают `high` / `medium` / `manager_tier2`
9. **Default** — domain match, но без seniority-сигналов → `medium`

## Тесты

`tests/test_rules.py` покрывает:
- Junior auto-reject (`Junior Procurement Analyst` → low)
- Foreign-language reject (`fluent french required` → low)
- User-exclusion (Amazon в `excluded_keywords` → low)
- English-only fail/pass (немецкий title без EN-маркеров → low; international description → pass)
- Wrong function (`Marketing Director` → low)
- Director + domain → high

См. [[Тесты]] для полного списка кейсов.

## Эволюция

- **22 апреля 2026:** введён `manager_tier2` бакет — `plain manager + domain` теперь не reject, а откладывается на второй тур backfill'а ([[Changelog 2026-04#двухуровневый-скоринг]]).
- **22 апреля 2026:** расширены `DIRECTOR_KEYWORDS` под Interim/Crisis/Turnaround/CRO/growth-роли.
- **апрель 2026:** удалён salary-floor check (зарплата редко доступна в листинге, AI-скорер сам это оценит).

## Куда не масштабируется

Все проверки — Python-loop по lowercase-тексту. На 8500 вакансиях × 9 источников × 1 user = ОК. Для multi-tenant (десятки пользователей) и роста до 50K+ вакансий стоит:

- **Pre-filter v2 в SQL** — перенести regex-проверки в `tsvector` + GIN, как уже сделан full-text search ([[Поиск и индексация]]). Один `WHERE search_vector @@ query` вместо Python-цикла.

→ [[Скоринг]] → [[Сервисы]] → [[Кэш и инвалидация]] → [[Тесты]] → [[Roadmap]]
