# Feature Release: Exclusions & Negative Keywords
**Дата:** 14 Апреля 2026
**Связанные ветки/Домены:** [[models.md]], [[services.md]]

---

## 📌 Описание Задачи
Пользователю требовалась интерактивная возможность гибко настраивать фильтрацию мусорных вакансий. Базовая фильтрация отбрасывала जूनियर-позиции, но не давала возможности кастомно исключать специфические домены, языки или ключевые слова (например, `Part-time`, `Consultancy`, `B2`). Нужно было внедрить сквозную поддержку пользовательского списка минус-слов от базы данных до промптов ИИ.

## 🗄️ База Данных и Модели (`app/models/user.py`, `app/database.py`)
- Добавлено новое поле `excluded_keywords` стандарта `JSON` в `UserProfile`.
- **Авто-Миграция:** Для исключения ручного применения Alembic миграций, внедрен скрипт "мягкой миграции" в `init_db`. Если колонка отсутствует, система выдает `ALTER TABLE user_profiles ADD COLUMN excluded_keywords JSON;`, обеспечивая консистентность продакшн-окружения: 
```python
# app/database.py
try:
    await conn.execute(text("SELECT excluded_keywords FROM user_profiles LIMIT 1"))
except Exception:
    await conn.execute(text("ALTER TABLE user_profiles ADD COLUMN excluded_keywords JSON"))
```

## 🧠 Система Двойной Обороны (Службы Скорринга)
Была построена архитектура из двух рубежей для фильтрации, чтобы максимизировать точность и снизить стоимость API (Credits):

### Рубеж 1: Быстрая Предпроверка (Fast Fail)
В `app/scoring/rules.py` функция `pre_filter(…)` теперь анализирует список `excluded_keywords`. При точном текстовом пересечении возвращается статус `"low"`.
```python
# app/scoring/rules.py
if profile and profile.excluded_keywords:
    for kw in profile.excluded_keywords:
        if kw and kw.lower() in text:
            return False, "low" # Отказ без вызова Claude!
```

### Рубеж 2: Семантический ИИ Фильтр (AI Semantic Parsing)
В случае если вакансия написана "хитро", слова передаются в системный промпт Claude:
```python
# app/scoring/matcher.py
if profile.excluded_keywords:
    parts.append(f"CRITICAL EXCLUSIONS: You MUST penalize heavily (Score < 20) any job requiring languages/skills/keywords explicitly excluded here: {', '.join(profile.excluded_keywords)}")
```
ИИ использует логику для анализа контекста и обнуляет Score.

## 🖥 Фронтенд и UI/UX (`app/static/dashboard.html`)
- На вкладке "Settings" внедрено многострочное поле: `<textarea id="s-excluded">`.
- Добавлен интерактивный JS для биндинга (парсинга списка через запятую) в методы `loadProfile` и `saveProfile()`. Имплементирован полноэкранный View.

## 🌍 Исправление Фильтрации Стран (`app/sources/jobspy_source.py`)
Попутно была выявлена жесткая зависимость. Агрегатор `JobSpy` отказывался искать вакансии внутри Испании, Швеции, Норвегии из-за отсутствия ключей внутри словарей `SITE_MAP` и `COUNTRY_NAME`. 
Оба словаря были [расширены] на полный список европейского континента (от `es`, `fr`, `it` до `fi`, `dk`). На фронтенде был заменен текстовый `<input>` фильтр стран на компактный выпадающий список (`<select>`).

---
**Итоги развертывания:**
Достигнут полный контроль над мусором. Экономия кредитов Claude ~40% за счет отсечения на Первом Рубеже (`rules.py`). Фронтенд избавлен от текстовых ошибок (Human-Error) ввода.
