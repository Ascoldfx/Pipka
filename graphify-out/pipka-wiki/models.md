# Models Domain

Contains the SQLAlchemy declarative models defining the project schema.
Dependencies: [[db.md]]
Dependent Domains: [[bot.md]], [[services.md]], [[routes.md]]

## User Profile Schema
Path: `app/models/user.py`
```python
class User(Base):
    id: Mapped[int] # primary_key
    telegram_id: Mapped[int] # unique
    is_active: Mapped[bool]
    is_admin: Mapped[bool]

class UserProfile(Base):
    user_id: Mapped[int] # foreign_key
    resume_text: Mapped[str]
    target_titles: Mapped[list[str]]
    min_salary: Mapped[int]
    preferred_countries: Mapped[list[str]]
    excluded_keywords: Mapped[list[str]]
```

## Job Aggregation Schema
Path: `app/models/job.py`
```python
class Job(Base):
    id: Mapped[int]
    url: Mapped[str] # unique
    site_job_id: Mapped[str]
    source: Mapped[str]
    title: Mapped[str]
    company_name: Mapped[str]
    country: Mapped[str]

class JobScore(Base):
    job_id: Mapped[int]
    user_id: Mapped[int]
    score: Mapped[int]
    ai_analysis: Mapped[str]
```

## User Actions Schema
Path: `app/models/application.py`
Maintains relations representing the state of an application to a Job.
```python
class Application(Base):
    user_id: Mapped[int]
    job_id: Mapped[int]
    status: Mapped[str] # "applied", "rejected"
```
