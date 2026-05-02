#service #watchlist

# Watchlist — точечный мониторинг компаний

Параллельная подсистема к основному скану ([[Сервисы#scheduler]]) — каждые 6 часов проверяет, появились ли новые вакансии в **конкретных компаниях**, которые пользователь явно отметил в профиле как "интересные".

Запускается из `_watchlist_scan` в [[Сервисы]], использует `WatchlistSource` из `app/sources/watchlist.py`.

## Зачем отдельно от основного скана

Основной 3-часовой скан ходит по `target_titles` (например, "Director Supply Chain") и поднимает всё что подходит под title. Но если ты ждёшь вакансию **в Nestlé**, и Nestlé внезапно открыла "Senior Director Procurement" — она попадёт в общий пул через несколько часов вместе с тысячами других. Watchlist делает обратное: ищет **по названию компании** (Adzuna company-filter) и показывает все её свежие вакансии независимо от title-fit'а.

Дальше уже [[Скоринг|AI-скорер]] решит, насколько каждая релевантна.

## Профиль

`UserProfile.target_companies: list[str]` — список компаний (`["Nestlé", "Bayer", "Unilever"]`). Редактируется через UI / `POST /api/profile`. Хранится в [[База данных#user_profiles]].

## Источник: WatchlistSource

Файл: `app/sources/watchlist.py`. По интерфейсу — обычный [[Источники вакансий|JobSource]], только трактует `SearchParams.queries` как **названия компаний**, а не как titles.

Под капотом — Adzuna API с параметром `company` (case-insensitive фильтр). Перебирает каждую компанию × каждую страну из `profile.preferred_countries`. Defaults to `["de"]` если страны не указаны.

```python
# Примерно так
for company in companies:
    for country in countries:
        results = await adzuna.search(
            country=country,
            company=company,
            results_per_page=20,
        )
```

Все RawJob получают `source="watchlist"` и `raw_data["watchlist_company"]` — это позволяет UI:
- Показать золотой бэдж `⭐ watchlist` в карточке (см. `app/static/dashboard.html:source-tag.watchlist`)
- Дать отдельный фильтр "Source = ⭐ Watchlist" в тулбаре

## Cron

```python
scheduler.add_job(_watchlist_scan, "interval", hours=6, args=[bot_app], id="watchlist_scan")
```

Раз в 6 часов. Без ночного rate-limit'а — Adzuna дешёвый API, лимит ~5к/сутки.

## Поток

```
APScheduler @ 6h tick → _watchlist_scan(bot_app)
  │
  ├─ for user in active users with profile:
  │     ├─ companies = user.profile.target_companies or []
  │     ├─ if not companies: skip
  │     ├─ params = SearchParams(queries=companies, countries=preferred or ["de"])
  │     ├─ aggregator = JobAggregator([WatchlistSource()])  # только этот источник
  │     ├─ stored_jobs = await aggregator.search(params, session)
  │     │     ↑ дедупит по dedup_hash против jobs (если уже была через основной скан — не дубли)
  │     │       и аплоадит в БД bulk-upsert'ом
  │     └─ await _score_and_notify(bot_app, user, stored_jobs, session)
  │           ↑ AI-скорит и пушит в Telegram score≥80
  │
  └─ logger.info("Watchlist scan completed")
```

## Дедупликация с основным сканом

Aggregator использует общий `dedup_hash` (sha256 от title+company), значит вакансия появившаяся в обоих сканах:

- Сохраняется один раз с `source="adzuna"` (основной скан был быстрее)
- В `raw_data.merged_sources` появляется `["adzuna", "watchlist"]`
- В UI рисуется бэдж `adzuna +1` с тултипом "adzuna · watchlist"

Подробнее — [[Дедупликация]].

## Отличия для пушей

`_score_and_notify` идентичен основному пути — те же 80-score threshold, лимит 10 push'ов, та же inline-клавиатура [[Telegram-бот#keyboards|job_actions]]. Никакой специальной логики для watchlist'а нет, но в push-сообщении `format_job_card` подсветит source как `⭐ watchlist`.

## Текущие ограничения

- **Только Adzuna**. Если Nestlé не публикует в Adzuna напрямую (а часто крупные компании postят на свои career-страницы или LinkedIn), watchlist их пропустит. См. [[Roadmap]] — расширение на JobSpy/LinkedIn for watchlist.
- **Нет fuzzy-match по компаниям**. `Nestlé` ищется буквально; `Nestle` (без é), `Nestle Switzerland`, `Société des Produits Nestlé` — отдельные строки. Adzuna сам делает substring-match, но ловит не всё.
- **Нет staggering** между пользователями. Все запускаются одновременно каждые 6ч — для одного-двух users норм, для 50+ начнёт упираться в Adzuna RPM. Пункт [[Roadmap]].

## Auto-exclude и watchlist

[[Трекер#auto-exclude]] добавляет компанию в `profile.excluded_keywords` после 5 reject'ов. Если эта компания одновременно в `target_companies` — конфликт: watchlist её ищет, pre-filter выкидывает с low-score=0.

Сейчас разруливается естественно: пользователь, у которого 5 раз была reject'нута Nestlé, скорее всего сам уберёт её из watchlist в UI. Но в идеале UI должен подсвечивать конфликт — пункт [[Roadmap]].

→ [[Сервисы]] → [[Источники вакансий]] → [[Скоринг]] → [[Трекер]] → [[Telegram-бот]] → [[Дедупликация]] → [[База данных]]
