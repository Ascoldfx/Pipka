# Деплой JobHunt на Hetzner CX23

## 1. Заказ сервера

1. Зайди на https://www.hetzner.com/cloud
2. Создай сервер:
   - **Тип**: CX23 (2 vCPU, 4 GB RAM, 40 GB SSD)
   - **ОС**: Ubuntu 24.04
   - **Локация**: Nuremberg или Falkenstein (ближе к Лейпцигу)
   - **SSH-ключ**: добавь свой (см. шаг 2)
3. Запомни IP сервера (например `65.108.xxx.xxx`)

## 2. SSH-ключ (если нет)

На своём Mac открой терминал:

```bash
# Создать ключ (если нет)
ssh-keygen -t ed25519 -C "ascoldfx@gmail.com"

# Скопировать публичный ключ
cat ~/.ssh/id_ed25519.pub
```

Вставь содержимое в Hetzner при создании сервера.

## 3. Подключение к серверу

```bash
ssh root@ТВОЙ_IP
```

## 4. Установка Docker (одна команда)

```bash
curl -fsSL https://get.docker.com | sh
```

## 5. Загрузка проекта

```bash
# Установить git
apt install -y git

# Клонировать репозиторий
cd /opt
git clone https://github.com/Ascoldfx/tea-erp.git jobhunt
cd jobhunt
git checkout claude/quizzical-gates
```

## 6. Настройка .env

```bash
nano .env
```

Вставь содержимое (замени ключи на свои):

```
TELEGRAM_BOT_TOKEN=8767119311:AAGXDUiY0eI6lzYQJxaPqqNCd7x1ULWpX_M
ADZUNA_APP_ID=1b605d72
ADZUNA_APP_KEY=eee0afb0f2f227433875796732e3edf3
ANTHROPIC_API_KEY=sk-ant-api03-xxx
DATABASE_URL=postgresql+asyncpg://jobhunt:jobhunt@db:5432/jobhunt
ARBEITSAGENTUR_API_KEY=jobboerse-jobsuche
LOG_LEVEL=INFO
```

Сохрани: `Ctrl+O`, Enter, `Ctrl+X`

## 7. Запуск

```bash
docker compose up -d --build
```

Готово! Бот работает в фоне. Проверь:

```bash
# Логи бота
docker compose logs -f app

# Статус
docker compose ps
```

## 8. Заполнение профиля

```bash
docker compose exec app python fill_profile.py
```

## 9. Проверка

- Напиши `/start` боту в Telegram
- Открой `http://ТВОЙ_IP:8000/health` — должен ответить `{"status": "ok"}`

---

## Полезные команды

```bash
# Перезапуск
docker compose restart app

# Обновление кода
cd /opt/jobhunt
git pull
docker compose up -d --build

# Логи (последние 100 строк)
docker compose logs --tail 100 app

# Полная остановка
docker compose down

# Очистка БД и перезапуск
docker compose exec app python -c "
from app.database import engine
from app.models import Base
import asyncio
async def reset():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
asyncio.run(reset())
"
docker compose exec app python fill_profile.py
docker compose restart app

# Бэкап БД
docker compose exec db pg_dump -U jobhunt jobhunt > backup_$(date +%Y%m%d).sql

# Восстановление
docker compose exec -T db psql -U jobhunt jobhunt < backup_20260408.sql
```

## Автозапуск при перезагрузке

Docker с `restart: unless-stopped` автоматически запустит контейнеры после ребута сервера.

## Мониторинг

```bash
# Сколько вакансий в базе
docker compose exec db psql -U jobhunt -c "SELECT source, COUNT(*) FROM jobs GROUP BY source;"

# Сколько applied
docker compose exec db psql -U jobhunt -c "SELECT status, COUNT(*) FROM applications GROUP BY status;"

# Активные подписки
docker compose exec db psql -U jobhunt -c "SELECT * FROM search_subscriptions WHERE is_active = true;"
```

## Безопасность (рекомендуется)

```bash
# Файрвол — открыть только SSH и HTTP
ufw allow 22
ufw allow 8000
ufw enable

# Сменить пароль PostgreSQL в docker-compose.yml и .env
# (замени "jobhunt" на сильный пароль в обоих файлах)
```

## Стоимость

- Hetzner CX23: ~€4.5/мес
- Claude API (скоринг): ~€0.5-2/мес при 2-3 поисках в день
- **Итого: ~€5-7/мес**
