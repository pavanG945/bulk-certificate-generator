import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db, get_session_factory
from app.main import app
from app.routers.jobs import get_certificate_dir


@pytest.fixture()
def client(tmp_path):
    """App wired to an in-memory database and a temp certificate directory."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection, so the background thread sees the same DB
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_certificate_dir] = lambda: tmp_path
    # TestClient is not used as a context manager, so the app lifespan does not run.
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def make_payload(recipients, **overrides):
    payload = {
        "title": "Python Bootcamp",
        "issuer": "Acme Academy",
        "issue_date": "2026-09-30",
        "recipients": recipients,
    }
    payload.update(overrides)
    return payload
