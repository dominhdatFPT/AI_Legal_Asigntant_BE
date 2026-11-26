import inspect
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.core.config import get_settings
from app.services import cache, prompts

logger = logging.getLogger(__name__)
VN_TZ = timezone(timedelta(hours=7))
TTL = 86400
INFLIGHT_TTL = 180
MESSAGE = "Bạn đã hết lượt sử dụng AI trong ngày hôm nay. Vui lòng quay lại vào ngày mai!"
INFLIGHT_MESSAGE = "Yêu cầu AI trước đó của bạn đang được xử lý. Vui lòng chờ hoàn tất trước khi gửi câu hỏi tiếp theo."

# Cộng và đặt TTL nguyên tử; không kéo dài TTL của khoá đã có hạn.
INCREMENT = """
local n = redis.call('INCRBY', KEYS[1], ARGV[1])
if redis.call('TTL', KEYS[1]) == -1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return n
"""

# Giải phóng khóa in-flight nguyên tử: chỉ xóa nếu đúng token của chủ sở hữu (tránh xóa nhầm khóa request mới).
RELEASE_INFLIGHT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_LOCAL_LOCK = threading.Lock()
_LOCAL_INFLIGHT: dict[str, tuple[str, float]] = {}  # subject_id -> (token, expires_at)
_LOCAL_USAGE: dict[str, tuple[int, float]] = {}     # quota_key -> (used_tokens, expires_at)
_LOCAL_MAX_ENTRIES = 5000


def reset_local_state() -> None:
    """Xóa bộ nhớ khóa/quota cục bộ (dùng cho test isolation)."""
    with _LOCAL_LOCK:
        _LOCAL_INFLIGHT.clear()
        _LOCAL_USAGE.clear()


def _prune_local_dict(store: dict[str, tuple], now: float) -> None:
    if len(store) < _LOCAL_MAX_ENTRIES:
        return
    expired = [k for k, (_, exp) in store.items() if exp <= now]
    for k in expired:
        del store[k]
    while len(store) >= _LOCAL_MAX_ENTRIES:
        store.pop(next(iter(store)))


def _local_get_usage(key: str) -> int:
    now = time.monotonic()
    with _LOCAL_LOCK:
        entry = _LOCAL_USAGE.get(key)
        if entry is None:
            return 0
        used, exp = entry
        if exp <= now:
            del _LOCAL_USAGE[key]
            return 0
        return used


def _local_add_usage(key: str, amount: int) -> int:
    now = time.monotonic()
    with _LOCAL_LOCK:
        _prune_local_dict(_LOCAL_USAGE, now)
        entry = _LOCAL_USAGE.get(key)
        if entry is None or entry[1] <= now:
            new_val = amount
            exp = now + TTL
        else:
            new_val = entry[0] + amount
            exp = entry[1]
        _LOCAL_USAGE[key] = (new_val, exp)
        return new_val


def _acquire_local_inflight(subject_id: str, token: str, ttl: int) -> str:
    now = time.monotonic()
    with _LOCAL_LOCK:
        _prune_local_dict(_LOCAL_INFLIGHT, now)
        existing = _LOCAL_INFLIGHT.get(subject_id)
        if existing is not None and existing[1] > now:
            raise HTTPException(
                status_code=429,
                detail={"code": "AI_REQUEST_IN_PROGRESS", "message": INFLIGHT_MESSAGE},
            )
        _LOCAL_INFLIGHT[subject_id] = (token, now + ttl)
    return f"local:{token}"


def _release_local_inflight(subject_id: str, token: str) -> None:
    with _LOCAL_LOCK:
        existing = _LOCAL_INFLIGHT.get(subject_id)
        if existing is not None and existing[0] == token:
            del _LOCAL_INFLIGHT[subject_id]


def _supports_atomic_set_nx(client) -> bool:
    fn = getattr(client, "set", None)
    if not callable(fn):
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "nx" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _supports_lua_release(client) -> bool:
    fn = getattr(client, "eval", None)
    if not callable(fn):
        return False
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params.values())


def get_today_vn() -> str:
    return datetime.now(VN_TZ).date().isoformat()


def _key(user_id: str, target_date: str | None = None) -> str:
    return f"legal:quota:{user_id}:{target_date or get_today_vn()}"


def _inflight_key(user_id: str) -> str:
    return f"legal:quota:inflight:{user_id}"


def estimate_tokens(prompt: str = "", completion: str = "") -> int:
    length = len(prompt) + len(completion)
    return max(1, length // 4) if length else 0


def is_exempt(metadata: dict | None = None, completion: str = "") -> bool:
    """Provider usage stays intact; unavailable legal context costs no user quota.

    Only trust the server's result type or the exact fallback answer, never a
    quota_exempt flag supplied by a caller or text inside the user's question.
    """
    return (metadata or {}).get("type") == "out_of_scope" or completion.strip() == prompts.OUT_OF_SCOPE_ANSWER


def is_unlimited_user(user_id: str, username: str | None = None) -> bool:
    """Kiểm tra tài khoản được operator cấu hình miễn trừ qua TEST_UNLIMITED_USERS (mặc định rỗng).

    Không bao giờ hardcode tài khoản bypass trong mã nguồn. Trên môi trường production chỉ so khớp
    user_id (UUID đã ký trong JWT sub), không so khớp username.
    """
    settings = get_settings()
    raw_list = getattr(settings, "test_unlimited_users", "")
    if isinstance(raw_list, str):
        exempt = {u.strip().lower() for u in raw_list.split(",") if u.strip()}
    elif isinstance(raw_list, (list, set, tuple)):
        exempt = {str(u).strip().lower() for u in raw_list if str(u).strip()}
    else:
        exempt = set()
    if not exempt:
        return False
    if str(user_id).strip().lower() in exempt:
        return True
    env = str(getattr(settings, "environment", "")).strip().lower()
    if env != "production" and username and str(username).strip().lower() in exempt:
        return True
    return False


def _execute(operation):
    settings = get_settings()
    if not cache._use_redis(settings):
        logger.warning("Quota: Redis chưa được cấu hình, dùng bộ đếm cục bộ dự phòng")
        return None
    breaker = cache.shared_breaker(settings)
    if not breaker.allow():
        logger.warning("Quota: Redis đang ngắt mạch, dùng bộ đếm cục bộ dự phòng")
        return None
    try:
        result = operation(cache._get_client())
    except Exception as exc:
        breaker.failure(exc)
        logger.warning("Quota: Redis lỗi (%s), dùng bộ đếm cục bộ dự phòng", type(exc).__name__)
        return None
    breaker.success()
    return result


def acquire_inflight_slot(user_id: str, username: str | None = None, ttl: int = INFLIGHT_TTL) -> str | None:
    """Khóa nguyên tử mỗi người dùng tối đa 1 lượt AI đang chạy đồng thời (chống spam song song vượt hạn mức).

    Trả về slot token để giải phóng trong `finally` sau khi cộng token. Trả về `None` nếu quota tắt hoặc user được miễn.
    """
    settings = get_settings()
    if not settings.daily_token_quota_enabled:
        return None
    if is_unlimited_user(user_id, username):
        return None

    token = uuid.uuid4().hex
    if cache._use_redis(settings):
        breaker = cache.shared_breaker(settings)
        if breaker.allow():
            client = cache._get_client()
            if _supports_atomic_set_nx(client):
                try:
                    acquired = client.set(_inflight_key(user_id), token, nx=True, ex=ttl)
                except Exception as exc:
                    breaker.failure(exc)
                    logger.warning("Quota inflight: Redis lỗi (%s), chuyển sang khóa cục bộ", type(exc).__name__)
                else:
                    breaker.success()
                    if acquired:
                        return f"redis:{token}"
                    raise HTTPException(
                        status_code=429,
                        detail={"code": "AI_REQUEST_IN_PROGRESS", "message": INFLIGHT_MESSAGE},
                    )
    return _acquire_local_inflight(str(user_id), token, ttl)


def release_inflight_slot(user_id: str, slot_token: str | None) -> None:
    """Giải phóng khóa in-flight theo đúng token sở hữu (idempotent, an toàn khi gọi lại)."""
    if not slot_token:
        return
    if slot_token.startswith("local:"):
        _release_local_inflight(str(user_id), slot_token[len("local:"):])
        return
    if not slot_token.startswith("redis:"):
        return
    raw = slot_token[len("redis:"):]
    key = _inflight_key(user_id)

    def _release(client):
        if _supports_lua_release(client):
            return client.eval(RELEASE_INFLIGHT, 1, key, raw)
        if client.get(key) == raw and callable(getattr(client, "delete", None)):
            return client.delete(key)
        return 0

    _execute(_release)


def check_daily_quota(
    user_id: str,
    target_date: str | None = None,
    username: str | None = None,
    limit_override: int | None = None,
    min_tokens: int = 0,
) -> None:
    """min_tokens = sàn token tối thiểu mà lượt này cần (admission): còn lại ít hơn sàn thì từ chối NGAY, trước RAG/LLM.
    Hạn mức ngày tách khỏi trần $0.01; TEST_UNLIMITED_USERS chỉ miễn hạn mức này."""
    settings = get_settings()
    if not settings.daily_token_quota_enabled:
        return
    if is_unlimited_user(user_id, username):
        return

    key = _key(user_id, target_date)

    def read(client):
        raw = client.get(key)
        return 0 if raw is None else int(raw)

    used = _execute(read)
    if used is None:
        used = _local_get_usage(key)
    limit = settings.daily_token_limit if limit_override is None else limit_override
    if used >= limit or (min_tokens > 0 and limit - used < min_tokens):
        raise HTTPException(status_code=429, detail={
            "code": "DAILY_QUOTA_EXCEEDED", "message": MESSAGE,
        })


def add_daily_tokens(
    user_id: str, tokens_used: int | None, target_date: str | None = None,
    prompt: str = "", completion: str = "", metadata: dict | None = None,
    username: str | None = None,
) -> None:
    if is_unlimited_user(user_id, username):
        return
    if is_exempt(metadata, completion):
        return
    if not get_settings().daily_token_quota_enabled:
        return
    if isinstance(tokens_used, bool) or not isinstance(tokens_used, int) or tokens_used <= 0:
        tokens_used = estimate_tokens(prompt, completion)
    if not tokens_used:
        return
    key = _key(user_id, target_date)

    def increment(client):
        if callable(getattr(client, "eval", None)):
            return client.eval(INCREMENT, 1, key, tokens_used, TTL)
        # Compatibility with simple test doubles; real Redis uses the atomic script.
        value = int(client.get(key) or 0) + tokens_used
        ttl = client.ttl(key) if callable(getattr(client, "ttl", None)) else TTL
        client.set(key, value, ex=ttl if ttl > 0 else TTL)
        return value

    if _execute(increment) is None:
        _local_add_usage(key, tokens_used)
