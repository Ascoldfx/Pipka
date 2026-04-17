from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.user import User, UserProfile  # noqa: E402, F401
from app.models.job import Job, JobScore  # noqa: E402, F401
from app.models.application import Application, ApplicationHistory, SearchSubscription  # noqa: E402, F401
from app.models.ops_event import OpsEvent  # noqa: E402, F401
