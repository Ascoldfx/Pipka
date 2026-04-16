# Pipka — AI Job Search Platform

> Автоматический агрегатор вакансий с AI-скорингом для поиска работы уровня Director/VP/Head в DACH+.

## Навигация

- [[Архитектура]] — общая схема системы
- [[База данных]] — все таблицы и связи
- [[Сервисы]] — бизнес-логика
- [[API]] — все эндпоинты
- [[Источники вакансий]] — откуда берём данные
- [[Скоринг]] — как AI оценивает вакансии
- [[Настройки]] — все параметры конфигурации
- [[Деплой]] — сервер, Docker, CI

## Стек

| Слой | Технология |
|------|-----------|
| Backend | FastAPI + Python 3.12 |
| БД | PostgreSQL 16 (asyncpg) |
| ORM | SQLAlchemy 2.0 async |
| AI | Claude (Anthropic) |
| Bot | python-telegram-bot 21 |
| Scheduler | APScheduler 3 |
| Auth | Google OAuth2 (authlib) + SessionMiddleware |
| Server | Contabo VPS, Ubuntu 24.04, Docker |
| Domain | pipka.net (Cloudflare proxy) |

## Быстрые ссылки

- Продакшн: https://pipka.net
- GitHub: https://github.com/Ascoldfx/Pipka
- Сервер: `ssh root@217.76.61.28` (ключ `~/.ssh/id_ed25519`)
- Проект: `/opt/pipka`
