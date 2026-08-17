"""repositories 管理员申请审批流程测试（内存 SQLite，隔离生产库）。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from smart_farm.data import repositories as repo
from smart_farm.data.models import Base, User
from smart_farm.services import auth_service as auth


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


def _mk_user(session, username="alice", role="user", admin_request=False):
    return repo.create_user(
        session,
        username,
        auth.hash_password("User@123456"),
        role=role,
        admin_request=admin_request,
    )


def test_create_user_with_admin_request(session):
    u = _mk_user(session, admin_request=True)
    assert u.role == "user"  # 申请时不直接升为 admin
    assert u.admin_request is True
    assert u.admin_request_time is not None


def test_list_admin_requests_only_pending(session):
    _mk_user(session, "pending", admin_request=True)
    _mk_user(session, "normal")
    pending = repo.list_admin_requests(session)
    assert [u.username for u in pending] == ["pending"]


def test_approve_admin_request(session):
    u = _mk_user(session, admin_request=True)
    session.commit()
    assert repo.approve_admin_request(session, u.id) is True
    session.commit()
    updated = session.get(User, u.id)
    assert updated.role == "admin"
    assert updated.admin_request is False
    assert updated.admin_request_time is None


def test_approve_non_request_returns_false(session):
    u = _mk_user(session)  # 无申请
    session.commit()
    assert repo.approve_admin_request(session, u.id) is False
    session.commit()
    assert session.get(User, u.id).role == "user"


def test_reject_admin_request(session):
    u = _mk_user(session, admin_request=True)
    session.commit()
    assert repo.reject_admin_request(session, u.id) is True
    session.commit()
    updated = session.get(User, u.id)
    assert updated.role == "user"
    assert updated.admin_request is False


def test_approve_missing_user_returns_false(session):
    assert repo.approve_admin_request(session, 9999) is False


def test_update_user_password(session):
    u = _mk_user(session)
    session.commit()
    new_hash = auth.hash_password("New@123456")
    assert repo.update_user_password(session, u.id, new_hash) is True
    session.commit()
    assert auth.verify_password("New@123456", session.get(User, u.id).password)


def test_update_user_password_missing_user(session):
    assert repo.update_user_password(session, 9999, "x") is False


def test_delete_user(session):
    u = _mk_user(session)
    session.commit()
    assert repo.delete_user(session, u.id) is True
    session.commit()
    assert session.get(User, u.id) is None


def test_delete_user_missing_user(session):
    assert repo.delete_user(session, 9999) is False
