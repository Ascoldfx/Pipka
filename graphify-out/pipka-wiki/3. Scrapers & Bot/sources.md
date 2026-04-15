# Sources Domain

Abstraction wrapper around diverse job boards and API endpoints handling extraction to unified formats.
Dependencies: [[models.md]]
Dependent on by: [[services.md]], [[routes.md]]

## Base Extractor
Path: `app/sources/base.py`
Defines `BaseSource` and structured `SearchParams` payloads:
```python
class SearchParams(BaseModel):
    queries: list[str]
    locations: list[str]
    countries: list[str]

class RawJob(BaseModel):
    url: str
    title: str
    company_name: str
    location: str
    description: str
```

## Aggregator Component
Path: `app/sources/aggregator.py`
A massive manager initializing multiple `BaseSource` providers (such as JobSpy, Remotive). Runs geographic filters allowing cross-checking of blacklist logic across US State Names or arbitrary non-requested geographic hubs.

## Native Sources
Path: `app/sources/*.py`
1. **Remotive** (`remotive.py`) - Remote job aggregation.
2. **Arbeitnow** (`arbeitnow.py`) - European/German startups.
3. **Adzuna** (`adzuna.py`) - Aggregator API требующий строгий App ID и Key. Жестко перебирает комбинации стран и локаций из профиля пользователя.
4. **JobSpy** (`jobspy_source.py`) - Powerful library mapping Google, LinkedIn, Glassdoor schemas.

### 🐛 Решение проблемы локализации JobSpy
Агрегатор JobSpy имеет внутреннюю жесткую изоляцию: он не понимает стран, которых нет в его внутренних словарях (`SITE_MAP` и `COUNTRY_NAME`). Ранее попытка поиска для стран вроде Испании (`es`) или Швеции (`se`) приводила к мгновенному отказу и возврату пустого списка без вызова ИИ.
**Решение:**
В файл `jobspy_source.py` внедрен полный маппинг европейского континента (21 страна). Теперь JobSpy корректно конвертирует код `es` в полноформатное название `spain` и успешно отдает запрос в системы LinkedIn и Indeed.
