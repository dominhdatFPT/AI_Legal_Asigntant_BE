"""redis_url trong config.py: mặc định rỗng (= không dùng Redis) và đọc được từ biến môi trường REDIS_URL."""
from app.core.config import Settings


def test_redis_url_mac_dinh_rong(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert Settings(_env_file=None).redis_url == ""


def test_redis_url_doc_tu_bien_moi_truong(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis-test:6379/0")
    assert Settings(_env_file=None).redis_url == "redis://redis-test:6379/0"
