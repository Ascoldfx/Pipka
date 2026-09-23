#changelog

# Changelog — сентябрь 2026

## 23 сентября 2026 — scoring fallback, очередь и Ops probes

- После HTTP 410 для устаревшей NVIDIA Llama-модели выбран `poolside/laguna-xs-2.1` как текущий fallback.
- Реальный полный scoring prompt подтвердил валидный JSON-score. NVIDIA matcher переведён на SSE streaming, обработку по одной вакансии, timeout 60 секунд, одну попытку и ограничение 768 output tokens. Это снижает зависания; внешние 503/timeout остаются возможны.
- Gemini `gemini-3.6-flash` остаётся основным batch scorer; quota cap 20 запросов/сутки. При breaker тот же свежий batch автоматически передаётся NVIDIA.
- Из Ops feed исключены известные внешние security-probes только при unsafe method и 403/404. Middleware по-прежнему блокирует запрос; исторические события не удаляются. Production проверка: 403 и счётчик не вырос.
- Проверено: production `/health` healthy, БД на Alembic `0013_application_identity`, последнее сканирование прошло. В контрольном окне записано 11 scores с NVIDIA fallback.
- Подтверждены актуальные ссылки кода по SHA-256. VPS Git HEAD `4a29d79` отстаёт: три runtime коммита развернуты file overlay. Последние локальные commits: `34a7f81`, `db1c96c`, `2dee88f`.
- Секретный инвентарь сверяли по именам/флагам, значения не читали и в Obsidian не сохраняли. Production `.env` — `0600 root:root`.
- Обнаружены настройки доступа для последующего решения: public registration действует из code default `true`; Basic Auth credentials заданы в env, но не используются приложением или проверенным nginx; старый `ANTHROPIC_API_KEY` остался в env без активного потребителя.
- Screenshot от 22 сентября раскрыл NVIDIA API key. Требуется отозвать показанный ключ и заменить production secret, если это он; ротация не подтверждена.

### Полная актуализация вики и графа Obsidian

Сверка всех страниц с кодом рабочей папки (`main` @ `2dee88f` + незакоммиченные правки):

- **Новые узлы:** [[Биллинг и кредиты]], [[Онбординг и обратная связь]], [[Мульти-юзер очереди]] — подсистемы, введённые 7–17 августа, ранее не имели страниц.
- `База данных.md`: таблицы `payment_transactions`, `user_feedbacks`; колонки `users.credits`, `total_credits_purchased`, `onboarded`.
- `API.md`: раздел Feedback/онбординг, значения `source` `builtin`/`gupy`, ссылка на [[Resume parsing]] (была страницей-сиротой).
- `Настройки.md`: добавлены все 13 недостающих env-переменных (пакеты биллинга, trial-кредиты, `SCAN_INTERVAL_MINUTES`, `SEMANTIC_SKIP_*`, `NVIDIA_SCORING_REASONING_EFFORT`, legacy `DASHBOARD_*`/`GUEST_*`); убран дублирующийся раздел embeddings. Semantic skip описан как понижение приоритета (с migration `0008` он не отсекает).
- `Архитектура.md`: дерево файлов переписано — 13 миграций, 11 роутеров, 11 источников, клиенты Gemini/NVIDIA, `security_*`, scripts, CI.
- `Тесты.md`: реестр 37 тестовых файлов / 158 тестов по подсистемам + явные пробелы (списание кредитов, Telegram-нотификация фидбека).
- `Безопасность.md`: из критичных секретов убран удалённый `ANTHROPIC_API_KEY` (помечен как orphan), добавлены `CRYPTOMUS_PAYMENT_KEY`, `GUPY_FEED_TOKEN`, `JOOBLE_API_KEY`, `ADZUNA_APP_KEY`.
- `Ops панель.md`, `Источники вакансий.md`: скан ежечасный, а не 3-часовой; отмечено, что ротация JobSpy осталась на 3ч-слоте.
- `Сервисы.md`: списание кредитов в `_score_and_notify`, ссылка на мульти-юзер очереди.
- `index.md`: новые узлы, 11 роутеров/источников, строка `Changelog 2026-06`, тег `#index`; описание проекта — мульти-региональный + монетизация.
- `Changelog 2026-07/08`: блоки переставлены в хронологическом порядке (правило CLAUDE.md), заголовки августа унифицированы (`D августа 2026 — …`), дописаны пропущенные блоки 7 августа (монетизация, BuiltIn, онбординг, гостевой режим) и 9 августа (scheduler стартует до Telegram-бота).
- `Roadmap.md`: синхронизация Git/VPS (P0), часовая ротация JobSpy и тесты биллинга (P1), чистка `deduct_user_credits` и legacy env (Cleanup).
- **Граф:** теги на страницах без тегов, colorGroups vault дополнены `#billing`, `#feedback`, `#resume`, `#index`; 0 битых ссылок, 0 сирот.
- **Хуки Claude Code:** `.claude/hooks/session-start.sh` и глобальный Stop-хук указывали на несуществующий `graphify-out/pipka-wiki/` — переведены на `docs/pipka-wiki/`.
- `CLAUDE.md`: раздел скоринга (3.6 Flash, NVIDIA Laguna fallback, Claude удалён, embeddings Nemotron) и структура каталогов.

Подробный audit snapshot: [[Текущее состояние и доступы]].

→ [[Скоринг]] → [[Сервисы]] → [[Настройки]] → [[Auth]] → [[Безопасность]] → [[Ops панель]] → [[Деплой]] → [[Миграции]]
