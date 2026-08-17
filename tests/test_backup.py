"""backup_service 测试（加密往返 + 密钥处理）。"""

import pytest

from smart_farm.services import backup_service as bs


def test_key_generation():
    key1 = bs.generate_key()
    key2 = bs.generate_key()
    assert key1 != key2
    assert len(key1) > 20


def test_encrypt_decrypt_roundtrip():
    snapshot = {"user": [{"username": "alice", "role": "admin"}], "greenhouse": []}
    key = bs.generate_key()
    ciphertext = bs.encrypt_snapshot(snapshot, key)
    # 密文是 bytes 且与明文不同
    assert isinstance(ciphertext, bytes)
    assert b"alice" not in ciphertext
    restored = bs.decrypt_snapshot(ciphertext, key)
    assert restored == snapshot


def test_decrypt_wrong_key_raises():
    snapshot = {"user": []}
    key1 = bs.generate_key()
    key2 = bs.generate_key()
    ciphertext = bs.encrypt_snapshot(snapshot, key1)
    with pytest.raises(ValueError, match="解密失败"):
        bs.decrypt_snapshot(ciphertext, key2)


def test_decrypt_garbage_raises():
    with pytest.raises(ValueError):
        bs.decrypt_snapshot(b"not-a-real-ciphertext", bs.generate_key())


def test_key_b64_roundtrip():
    key = bs.generate_key()
    encoded = bs.encode_key_b64(key)
    assert bs.decode_key_b64(encoded) == key
