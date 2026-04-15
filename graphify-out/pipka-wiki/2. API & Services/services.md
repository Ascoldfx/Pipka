# Services Domain

Business logic controlling aggregation triggers, match scoring, and application pipelines.
Dependencies: [[db.md]], [[models.md]], [[sources.md]]

## 🛡 Система Двойной Фильтрации (Dual-Stage Filtering)
Мы используем 2 уровня отсева вакансий для максимизации качества и экономии API-кредитов Claude.

### 1-й Уровень: Fast Rejection Rules
Path: `app/scoring/rules.py`
Высокоскоростная функция `pre_filter(…)`, которая исполняется *до* вызова ИИ.
- **Хардкод-исключения:** Автоматически режет Junior-позиции, чужие домены (HR/Marketing) и локальные языки (Испанский/Французский) без вызова Claude.
- **Динамические исключения:** Итерирует через массив `UserProfile.excluded_keywords` (например, "Part-time", "Consultant"). Любое пересечение вызывает мгновенное отклонение вакансии с меткой "low".

### 2-й Уровень: ИИ Семантический Парсинг
Path: `app/scoring/matcher.py`
Генерация скорринга через Claude 3 (`anthropic`). Функция `build_profile_text` вшивает список минус-слов в системный промпт как `CRITICAL EXCLUSIONS`. Если Claude понимает из общего смысла текста, что вакансия нарушает эти правила, он принудительно обнуляет (`Score < 20`) оценку.

## Автоматические Сканирования (Schedulers)
Path: `app/services/scheduler_service.py`
Initializes `APScheduler` tracking intervals for automatic continuous searches. Iterates across users and active `UserProfile` variables (countries and targets), building a `SearchParams` payload for `JobAggregator` defined in [[sources.md]].

Triggers notifications by directly pushing to the `bot` object via Telegram core functionality if scores cross 80.

## User Tracker Services
Path: `app/services/tracker_service.py`
State machines mapping the application process, toggling variables inside the `Application` schema mapping rows within PostgreSQL. Also provides lookup functions for missing `JobScore` instances against unreviewed applications.
