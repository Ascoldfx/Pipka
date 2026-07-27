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

`english`, `international`, `global`, `multinational`, `working language: english`, `english-speaking`, `startup`, `remote`. Используется только при бакетинге high vs medium. Для `english_only` наличие такого слова больше не считается доказательством языка всей вакансии.

### Определение языка описания

`detect_description_language()` считает частотные служебные слова EN/DE/FR/NL/ES/IT. Уверенный неанглийский результат блокируется только при `profile.english_only=True`; `unknown` и короткий текст проходят в AI, чтобы не терять целевые вакансии.

### `FOREIGN_LANGUAGE_REQUIRED` — hard-reject

Триггеры на french/spanish/polish и т.п. в description: `langue requise`, `français requis`, `francais courant`, `maîtrise du français`, аналогично для других языков. Вакансии где требуется чужой язык кроме EN/DE — отбрасываются.

## Порядок проверок

1. **Protected target-title match** — нормализованное точное совпадение с `profile.target_titles` защищает вакансию от общих category/domain/seniority правил. Удаляются только служебное `of` и gender suffix `(m/w/d)`, но функциональные модификаторы сохраняются: `Director of Operations` совпадает с `Director Operations`, а `Director of Retail and Commercial Operations` — нет.
2. **Junior/wrong function** — `REJECT_TITLE_KEYWORDS` в title → `low`, кроме protected target-title.
3. **Commercial-function reject** — `commercial/retail/sales/revenue/store operations` и соответствующие Director/Head/Manager роли → `low`. Если в самом title явно есть `supply chain`, `procurement`, `sourcing`, `purchasing` или `logistics`, отраслевое слово `retail/commercial` не блокирует вакансию.
4. **Foreign language required** — `FOREIGN_LANGUAGE_REQUIRED` в description → `low`, включая protected target-title.
5. **User content exclusions** — валидные `profile.excluded_keywords` ищутся в title+description → `low`, включая protected target-title. Технические заглушки `nan/null/none/n/a/unknown` игнорируются.
6. **Blocked companies** — `profile.excluded_companies` сравнивается только с `Job.company_name`, регистронезависимо и по полному нормализованному названию. Упоминание Amazon или SAP в описании другой компании ничего не блокирует. [[Трекер#auto-exclude]] пишет названия только в этот список.
7. **English-only filter** — если `profile.english_only=True`:
   - явно немецкое название должности (`Geschäftsführer`, `Einkaufsleiter`, `Leiter Logistik`, `Bereichsleiter`, `Produktmanager`, `Krisenmanager` и близкие формы) → `low`, даже если в тексте есть общий маркер `remote`, `global` или `international`;
   - уверенно неанглийское описание (DE/FR/NL/ES/IT) → `low`;
   - английское, короткое или неоднозначное описание → продолжает фильтрацию и AI-проверку.
   Суффикс `(m/w/d)`, немецкий город и страна сами по себе не считаются немецким названием. Двуязычные названия вроде `Einkaufsleiter / Head of Procurement` передаются на проверку описания.
8. **Domain check** — нет protected target-title и `DOMAIN_KEYWORDS` в title или description → `low`.
9. **Work mode filter** — соответствие `profile.work_mode` (`remote`/`onsite`/`hybrid`/`any`) и `Job.is_remote` + ключевых слов.
10. **Country check** — `Job.country` должен быть в `profile.preferred_countries`.
11. **Protected target-title priority** — после обязательных personal/language/location ограничений exact target возвращается как `high`, не требуя generic Director/Head keywords.
12. **Seniority bucketing** — `is_director` / `is_senior_manager` / `is_plain_manager` решают `high` / `medium` / `manager_tier2`.
13. **Default** — domain match, но без seniority-сигналов → `medium`.

## Тесты

`tests/test_rules.py` покрывает:
- Junior auto-reject (`Junior Procurement Analyst` → low)
- Foreign-language reject (`fluent french required` → low)
- Content-exclusion и точное company-exclusion; упоминание заблокированной компании в чужом описании не режет вакансию
- Защита от legacy `nan`
- English-only: немецкий title/описание → low; английский текст без marker-слов и неоднозначный короткий текст → pass
- Wrong function (`Marketing Director` → low)
- Director + domain → high

См. [[Тесты]] для полного списка кейсов.

## Эволюция

- **22 апреля 2026:** введён `manager_tier2` бакет — `plain manager + domain` теперь не reject, а откладывается на второй тур backfill'а ([[Changelog 2026-04#двухуровневый-скоринг]]).
- **22 апреля 2026:** расширены `DIRECTOR_KEYWORDS` под Interim/Crisis/Turnaround/CRO/growth-роли.
- **апрель 2026:** удалён salary-floor check.
- **26 июля 2026:** зарплата полностью исключена и из AI-промптов/вердиктов: большинство источников её не отдаёт, поэтому сравнение было систематически неполным и несправедливым.
- **27 июля 2026:** компании отделены от контентных стоп-фраз миграцией `0007`; `nan` очищается; English-only перешёл с marker-гейта на консервативное определение языка.

## Куда не масштабируется

Все проверки — Python-loop по lowercase-тексту. На 8500 вакансиях × 9 источников × 1 user = ОК. Для multi-tenant (десятки пользователей) и роста до 50K+ вакансий стоит:

- **Pre-filter v2 в SQL** — перенести regex-проверки в `tsvector` + GIN, как уже сделан full-text search ([[Поиск и индексация]]). Один `WHERE search_vector @@ query` вместо Python-цикла.

→ [[Скоринг]] → [[Сервисы]] → [[Кэш и инвалидация]] → [[Тесты]] → [[Roadmap]]
