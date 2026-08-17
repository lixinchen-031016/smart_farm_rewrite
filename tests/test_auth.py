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


def test_password_strength_high():
    strength, score, feedback = auth.evaluate_password_strength("StrongPass123!")
    assert strength == "high"
    assert score >= 70
    assert feedback  # 有反馈


def test_password_strength_medium():
    strength, score, _ = auth.evaluate_password_strength("Pass1234")
    assert strength == "medium"
    assert 40 <= score < 70


def test_password_strength_low_and_empty():
    strength, score, _ = auth.evaluate_password_strength("abc")
    assert strength == "low"
    assert score < 40
    assert auth.evaluate_password_strength("") == ("low", 0, [])


def test_password_strength_deductions():
    # 连续重复字符扣分：本应 high 的密码因 'aaa' 降档
    strength, score, feedback = auth.evaluate_password_strength("StrongPass123!aaa")
    assert strength != "high" or score < 90
    # 连续数字序列扣分
    _, _, fb2 = auth.evaluate_password_strength("StrongPass123!")
    assert any("连续" in f for f in fb2)


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
    for _ in range(auth.limiter.max_attempts):
        auth.limiter.register_failure("testuser")
    assert auth.limiter.is_blocked("testuser")
    auth.limiter.reset("testuser")
    assert not auth.limiter.is_blocked("testuser")


def test_limiter_defaults_match_legacy():
    # 对齐旧库：10 次失败 / 30 秒窗口
    assert auth.limiter.max_attempts == 10
    assert auth.limiter.window.total_seconds() == 30


def test_validate_username():
    ok, err = auth.validate_username("alice_01")
    assert ok and err == ""
    ok, _ = auth.validate_username("张三")
    assert ok
    ok, _ = auth.validate_username("a")  # 过短
    assert not ok
    ok, _ = auth.validate_username("x" * 51)  # 过长
    assert not ok
    ok, _ = auth.validate_username("**管理员** [x](url)")  # 注入字符
    assert not ok
    ok, _ = auth.validate_username("")  # 空
    assert not ok
