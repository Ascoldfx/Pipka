# Pipka — агент поиска работы

Stack: Python, FastAPI, PostgreSQL + Alembic, Docker.
Repo: github.com/Ascoldfx/Pipka, ветка `main`.

## Структура
- `app/` — основной код агента
- `alembic/` — миграции БД
- `run.py` — точка входа
- `docker-compose.yml` — локальный запуск

## Antigravity sync
После изменений в Antigravity скажи "обнови граф" — Claude делает pull, патчит wiki.
