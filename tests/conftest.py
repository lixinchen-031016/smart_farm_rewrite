"""共享测试 fixture：内存 SQLite 会话 + 大棚 + 设备。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smart_farm.data import repositories as repo
from smart_farm.data.models import Base


@pytest.fixture()
def session():
    # 允许跨线程共享（网关/HTTP TestClient 在独立线程处理请求）
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def gh(session):
    return repo.create_greenhouse(session, "一号棚", "东区")


@pytest.fixture()
def device(session, gh):
    return repo.create_device(
        session, name="土壤节点", protocol="http",
        device_key="sf-test-key-0001", greenhouse_id=gh.id,
    )
