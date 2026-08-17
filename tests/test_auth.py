from smart_farm.services import auth_service as auth


def test_password_hash_and_verify():
    h = auth.hash_password("Admin@123456")
    assert h != "Admin@123456"
    assert auth.verify_password("Admin@123456", h)
    assert not auth.verify_password("wrong", h)


def test_password_complexity():
    assert auth.check_password_complexity("Admin@123456")
    assert not auth.check_password_complexity("short")
    assert not auth.check_password_complexity("alllowercase1!")
    assert not auth.check_password_complexity("NoSpecial1")


def test_jwt_roundtrip():
    token = auth.create_access_token("alice", "admin")
    payload = auth.decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"


def test_jwt_rejects_garbage():
    assert auth.decode_access_token("not-a-real-token") is None


def test_limiter_blocks_after_threshold():
    auth.limiter.reset("testuser")
    for _ in range(5):
        auth.limiter.register_failure("testuser")
    assert auth.limiter.is_blocked("testuser")
    auth.limiter.reset("testuser")
    assert not auth.limiter.is_blocked("testuser")
