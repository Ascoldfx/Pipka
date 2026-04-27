#bot

# Telegram-бот

Library: `python-telegram-bot` 21. Long-polling, без webhook'ов. Поднимается одним процессом вместе с FastAPI и [[Сервисы#scheduler]] — общий event loop, общий пул коннектов к БД.

Файлы: `app/bot/{bot,formatters,keyboards}.py` + `app/bot/handlers/*.py`.

## Точка входа

`app/bot/bot.py:create_bot_app()` строит `Application` и регистрирует:

- **CommandHandler:** `/start`, `/help`, `/search`, `/profile`.
- **CallbackQueryHandler** — для каждой inline-кнопки (см. [[#Keyboards]]).
- **MessageHandler** — `_text_router` диспетчит текстовые сообщения по `context.user_data` (например, "пользователь сейчас редактирует поле профиля" → отдать в `profile_text_handler`).

## Handlers

| Файл | Функции | Назначение |
|------|---------|-----------|
| `start.py` | `start_handler`, `help_handler` | Главное меню, гайд |
| `search.py` | `search_menu_handler`, `search_preset_handler`, `custom_search_handler`, `text_search_handler`, `show_more_handler` | Меню типов поиска + кастомные запросы. Сам строит `JobAggregator` через `_build_aggregator()` и стримит результаты пачками. |
| `inbox.py` | `inbox_menu_handler`, `inbox_handler` | Просмотр новых scored-вакансий с фильтром по диапазону score (`top` ≥70, `good` ≥40, `all`). |
| `results.py` | `ai_analysis_handler`, `save_job_handler`, `applied_handler`, `reject_handler` | Кнопки действий под каждой карточкой вакансии. Дёргает [[Трекер]]. |
| `tracker.py` | `my_jobs_handler`, `stats_handler`, `status_update_handler` | "Мои вакансии", статистика, переключение статуса (`saved → applied → interviewing → offer / rejected`). |
| `settings.py` | `profile_menu_handler`, `profile_field_handler`, `profile_text_handler` | Редактирование профиля по полям через текстовые сообщения (state в `context.user_data["editing_profile_field"]`). |

## Keyboards

`app/bot/keyboards.py` — все inline-меню. Главные:

- `main_menu()` — Inbox / Search / My Jobs / Profile / Stats.
- `search_type_menu()` — пресеты: Саксония, Германия, International, Европа, CEE, "По профилю", "Свой запрос".
- `inbox_menu()` — фильтр по диапазону score.
- `job_actions(job_db_id)` — `🤖 AI Анализ`, `💾 Save`, `✅ Applied`, `❌ Reject`. Callback-data: `ai_<id>`, `save_<id>`, и т.д. — паттерны мэтчатся через regex в `bot.py`.
- `profile_field_menu()` — какое поле редактировать (resume, target_titles, languages, ...).

## Formatters

`app/bot/formatters.py:format_job_card(job, score)` — строит Markdown-карточку вакансии: заголовок, компания, локация, source, salary range, posted_at + бейдж score. Используется и в боте, и в push-уведомлениях из [[Сервисы#_score_and_notify]] после background scan.

## State management

Используем встроенный `context.user_data` (per-chat dict), две ключевые ситуации:

- `editing_profile_field: str` — какое поле сейчас вводится. Текстовый message → парсится и записывается в `UserProfile`.
- `awaiting_custom_search: True` — пользователь нажал "🎯 Свой запрос". Текстовый message → `text_search_handler` создаёт `SearchParams` и зовёт агрегатор.

Постоянное состояние (профили, applications, scores) живёт в [[База данных]] — в `user_data` ничего критичного не хранится.

## Push-уведомления

Реальные пуши инициирует не сам бот, а scheduler: `_score_and_notify` (см. [[Сервисы#scheduler]]) после успешного скоринга вакансии со `score ≥ 80` шлёт через `bot_app.bot.send_message(chat_id=user.telegram_id, ...)` с inline-клавиатурой `job_actions`. Лимит 10 push'ов за один прогон.

Если пользователь заблокировал бота — `Forbidden` от Telegram API. Сейчас не обрабатывается явно (пункт из [[Roadmap]]).

→ [[Сервисы]] → [[Трекер]] → [[Скоринг]] → [[База данных]]
