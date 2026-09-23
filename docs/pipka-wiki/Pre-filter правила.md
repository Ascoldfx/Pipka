#scoring #prefilter

# Pre-filter правила

Файл: `app/scoring/rules.py`. Чисто-Python, без сетевых вызовов и без БД. Запускается в горячем пути `_score_and_notify` и `_backfill_score` ([[Сервисы#scheduler]]) ДО любого AI-обращения, чтобы не сжигать квоту Gemini/NVIDIA на заведомо нерелевантные вакансии.

Возвращает `tuple[bool, str]`:
- `True, "high"` — director/head уровень **в title** + функция **в title** → AI-скоринг tier 1 ([[Скоринг]])
- `True, "medium"` — director-уровень + смежная операционная роль или общее руководство в title (`ADJACENT_LEADERSHIP_TITLE_PATTERN`: production, plant, site, transportation, lean, value stream, commodity, buying, MD, CEO/COO, Produktion, Standort, Betrieb, Werksleitung, Geschäftsführer) или senior/lead/principal **вместе** с функцией в title → tier 1
- `False, "manager_tier2"` — прочий director-уровень без функции в title (Account Director, CFO, Head of Engineering), функция в title без seniority, plain manager, senior-маркер без функции → AI-скоринг tier 2 (только когда tier 1 пуст)
- `False, "low"` — hard reject, в БД пишется `JobScore(score=0, model_version="prefilter")` ([[Кэш и инвалидация#prefilter sentinel]])

## Списки ключевых слов

### Seniority — только по title

С 23.09.2026 seniority и функция читаются **из названия должности**, а не из описания. Раньше совпадение `operations`/`supply chain` где угодно в описании плюс `partner`/`principal`/`lead` пропускали в приоритетную очередь «Senior Graphic Designer», «Growth Partner», «Principal Hardware Test Engineer»: ~90% tier 1 были нецелевыми.

- `DIRECTOR_TITLE_PATTERN` (regex, целые слова): `director`, `head`, `vp`/`svp`/`evp`, `vice president`, `chief`, `ceo`/`coo`, `cpo`, `cfo`, `cso`, `cro`, `md`, `interim`, `krisenmanager`, `turnaround`, `restructuring`, PT-варианты (`diretor`, `vice-presidente`, `superintendente`, `gerente executivo/nacional`). Немецкие композиты ищутся внутри слова: `direktor`, `leiter`, `leitung`, `geschäftsführ` → `Standortleiter`, `Betriebsdirektor`, `Fachbereichsleitung`.
- `SENIOR_TITLE_PATTERN`: `senior manager`, `lead`, `leader`, `teamlead`, `principal`, `partner`, `gerente sênior` — засчитываются **только** вместе с функцией в title.
- `TITLE_DOMAIN_KEYWORDS` = `DOMAIN_KEYWORDS` без `growth` (слишком общее в title: Growth Marketer/Engineer). Growth-цели профиля матчатся точным совпадением target title.

### VP — только по opt-in

`is_non_target_vp()`: title, где единственный маркер уровня — `VP`/`SVP`/`EVP`/`Vice President`/`Vizepräsident`, → `low`, если ни один `target_titles` профиля не содержит VP. Кандидат убрал VP из целей: в Германии такие вакансии нереалистичны. Комбинированный title «Director / VP Procurement» сохраняется по маркеру `director`. Логика как у COO opt-in.

### `REJECT_TITLE_KEYWORDS` — hard-reject по title

Junior/operational: `specialist`, `analyst`, `coordinator`, `assistant`, `clerk`, `sachbearbeiter`, `referent`, `mitarbeiter`, `fachkraft`, `junior`, `trainee`, `werkstudent`, `praktikant`, `azubi`, `student`, `buyer`, `dispatcher`, `planner`, `merchandiser`. Internship проверяется отдельным whole-word regex `\bintern(?:ship)?s?\b`, чтобы `International`/`Internal` не становились false reject.

Wrong function (не Supply Chain / Procurement / Operations): `marketing`, `sales director`, `business development`, `account executive/manager`, `hr director/manager`, `human resources`, `people operations/lead`, `talent`, `recruiting/recruitment`, `engineering manager`, `software`, `developer`, `data scientist`, `product manager/director/lead`, `finance director`, `financial controller`, `accounting`, `legal`, `compliance director`, `regulatory`, `creative director`, `design director`, `art director`, `editorial`, `content director`, `communications director`, `customer success/service`, `support manager`, `research director`, `r&d director`, `scientific`, `medical director`, `clinical`, `real estate`, `property`, `founding`, `co-founder`, `consultant`, `consulting`, `berater`, `beratung`, `advisory`, `advisor`.

### `DOMAIN_KEYWORDS` — нужно совпадение для прохождения

`supply chain`, `procurement`, `einkauf`, `beschaffung`, `logistics`, `logistik`, `operations`, `s2p`, `source to pay`, `sourcing`, `purchasing`, `lieferkette`, `warehouse`, `lager`, `demand planning`, `inventory`, `distribution`, `fulfillment`, `supplier`, `vendor management`, `category management`, `strategic sourcing`, `indirect/direct procurement`.

Crisis-related: `crisis management`, `turnaround`, `transformation`, `restructuring`, `interim management`, `business continuity`, `operational excellence`, `continuous improvement`, `growth`.

### Определение языка описания

`detect_description_language()` считает частотные служебные слова EN/DE/FR/NL/ES/IT. Уверенный неанглийский результат блокируется только при `profile.english_only=True`; `unknown` и короткий текст проходят в AI, чтобы не терять целевые вакансии.

### `FOREIGN_LANGUAGE_REQUIRED` — hard-reject

Триггеры на french/spanish/polish и т.п. в description: `langue requise`, `français requis`, `francais courant`, `maîtrise du français`, аналогично для других языков. Вакансии где требуется чужой язык кроме EN/DE — отбрасываются.

## Порядок проверок

1. **Protected target-title match** — нормализованное точное совпадение с `profile.target_titles` защищает вакансию от общих category/domain/seniority правил. Удаляются только служебное `of` и gender suffix `(m/w/d)`, но функциональные модификаторы сохраняются: `Director of Operations` совпадает с `Director Operations`, а `Director of Retail and Commercial Operations` — нет.
2. **COO / VP opt-in** — только если `COO`/`Chief Operating Officer` является самой ролью в начале title, она получает `low` без opt-in. `Director, COO Transformation Office` и `Head of Operations — COO Organisation` не считаются COO-ролями. Аналогично VP-only title без VP в целях профиля → `low`.
3. **Junior/wrong function** — `REJECT_TITLE_KEYWORDS` в title → `low`, кроме protected target-title. `Business Development` относится к коммерческой функции.
4. **Commercial-function reject** — `commercial/retail/sales/revenue/store operations` и соответствующие Director/Head/Manager роли → `low`. Если в самом title явно есть `supply chain`, `procurement`, `sourcing`, `purchasing` или `logistics`, отраслевое слово `retail/commercial` не блокирует вакансию.
5. **Foreign language required** — `FOREIGN_LANGUAGE_REQUIRED` в description → `low`, включая protected target-title.
6. **User content exclusions** — валидные `profile.excluded_keywords` ищутся в title+description → `low`, включая protected target-title. Технические заглушки `nan/null/none/n/a/unknown` игнорируются.
7. **Blocked companies** — `profile.excluded_companies` сравнивается только с `Job.company_name`, регистронезависимо и по полному нормализованному названию. Упоминание Amazon или SAP в описании другой компании ничего не блокирует. [[Трекер#auto-exclude]] пишет названия только в этот список.
8. **English-only filter** — если `profile.english_only=True`:
   - явно немецкое название должности (`Geschäftsführer`, `Einkaufsleiter`, `Leiter Logistik`, `Bereichsleiter`, `Produktmanager`, `Krisenmanager` и близкие формы) → `low`, даже если в тексте есть общий маркер `remote`, `global` или `international`;
   - уверенно неанглийское описание (DE/FR/NL/ES/IT) → `low`;
   - английское, короткое или неоднозначное описание → продолжает фильтрацию и AI-проверку.
   Суффикс `(m/w/d)`, немецкий город и страна сами по себе не считаются немецким названием. Двуязычные названия вроде `Einkaufsleiter / Head of Procurement` передаются на проверку описания.
9. **Domain check** — нет protected target-title и `DOMAIN_KEYWORDS` в title или description → `low`.
10. **Work mode filter** — соответствие `profile.work_mode` (`remote`/`onsite`/`hybrid`/`any`) и `Job.is_remote` + ключевых слов.
11. **Country check** — `Job.country` должен быть в `profile.preferred_countries`.
12. **Protected target-title priority** — после обязательных personal/language/location ограничений exact target возвращается как `high`, не требуя generic Director/Head keywords.
13. **Seniority bucketing (по title)** — director-маркер + функция в title → `high`; director-маркер + смежная роль / общее руководство → `medium`; senior-маркер + функция в title → `medium`.
14. **Default** — функция в title, прочий director-маркер, senior-маркер или plain manager/gerente → `manager_tier2`; иначе → `low`. Замер 45 дней DE: director-уровень без функции в title — 927 оценок, 80% <40; с гейтом смежных ролей в tier 1 осталось 88 (31 со score ≥70), остальное уходит в tier 2 и оценивается позже. Раньше default был `medium`: это и заполняло tier 1 нецелевыми ролями.

## Порядок AI-очереди — `focus_rank()`

Pre-filter решает, **что** скорить; `focus_rank(job, profile)` решает, **в каком порядке**. Ключ сортировки в real-time и backfill ([[Скоринг]]):

1. **Окно свежести** по `posted_at` (или `scraped_at`): ≤24 ч → 0, ≤`FRESH_SEARCH_DAYS` (3 дня) → 1, старше → 2. Рекрутер разбирает первых откликнувшихся, поэтому сегодняшняя вакансия всегда идёт раньше вчерашней.
2. **Procurement внутри окна** — если в `target_titles` есть procurement-роль (`PROCUREMENT_TITLE_PATTERN`: procurement, purchasing, sourcing, CPO, buying, category management, S2P, Einkauf*, Beschaffung, compras, suprimentos), вакансии с этим в title идут первыми.
3. Затем exact target → semantic similarity → без embedding → низкая similarity (`_order_by_semantic_priority`), затем новизна.

Similarity больше не может поднять старую вакансию над сегодняшней. Порядок ничего не отсекает.

## Тесты

`tests/test_rules.py` покрывает:
- Junior auto-reject (`Junior Procurement Analyst` → low)
- Foreign-language reject (`fluent french required` → low)
- Content-exclusion и точное company-exclusion; упоминание заблокированной компании в чужом описании не режет вакансию
- Защита от legacy `nan`
- English-only: немецкий title/описание → low; английский текст без marker-слов и неоднозначный короткий текст → pass
- Wrong function (`Marketing Director` → low)
- Director + domain → high
- Строгий title-матч (23.09.2026): описание с supply chain/operations не делает tier 1 из Graphic Designer / Engineer / Growth Partner; реальные целевые title (Head of Procurement, Standortleiter, Fachbereichsleitung Einkauf, MD, Principal Supply Chain Transformation) остаются в tier 1
- VP-only → low без opt-in; Director / VP → high
- `focus_rank`: свежесть важнее функции, procurement первым внутри окна

См. [[Тесты]] для полного списка кейсов.

## Эволюция

- **22 апреля 2026:** введён `manager_tier2` бакет — `plain manager + domain` теперь не reject, а откладывается на второй тур backfill'а ([[Changelog 2026-04]]).
- **22 апреля 2026:** расширены `DIRECTOR_KEYWORDS` под Interim/Crisis/Turnaround/CRO/growth-роли.
- **апрель 2026:** удалён salary-floor check.
- **26 июля 2026:** зарплата полностью исключена и из AI-промптов/вердиктов: большинство источников её не отдаёт, поэтому сравнение было систематически неполным и несправедливым.
- **27 июля 2026:** компании отделены от контентных стоп-фраз миграцией `0007`; `nan` очищается; English-only перешёл с marker-гейта на консервативное определение языка.
- **1 августа 2026:** whole-word internship, primary-role COO detection и semantic priority закрыли false-negative классы `International`/COO-office/низкая embedding similarity.

- **23 сентября 2026:** seniority и функция только из title; default `medium` → `manager_tier2`; VP стал opt-in; порядок очереди — окно свежести → procurement ([[Changelog 2026-09]]). Замер на production (DE, 3 дня): tier 1 874 → 201, совпадения со score ≥70 сохранены 369/394.

## Куда не масштабируется

Все проверки — Python-loop по lowercase-тексту. На 8500 вакансиях × 9 источников × 1 user = ОК. Для multi-tenant (десятки пользователей) и роста до 50K+ вакансий стоит:

- **Pre-filter v2 в SQL** — перенести regex-проверки в `tsvector` + GIN, как уже сделан full-text search ([[Поиск и индексация]]). Один `WHERE search_vector @@ query` вместо Python-цикла.

→ [[Скоринг]] → [[Сервисы]] → [[Кэш и инвалидация]] → [[Тесты]] → [[Roadmap]]
