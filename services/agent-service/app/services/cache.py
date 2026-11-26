"""Cache có hạn dùng (TTL) cho agent-service, cùng hàm chuẩn hoá câu hỏi làm khoá. Hai nơi dùng, đều tạo qua `make_cache`:
rag_client.search (vùng "rag") và cache lịch sử trò chuyện (history_cache, vùng "history").

TTLCache: cache trong bộ nhớ, theo từng process, mất khi restart, không khoá; đủ cho 1 worker uvicorn. Mỗi worker/replica một cache
riêng nên kết quả có thể khác nhau giữa các bản. Phần Redis ở cuối file (RedisCache, ResilientCache) là cache dùng chung các bản và còn
sau khi restart; TTLCache khi đó chỉ còn là bản dự phòng lúc Redis lỗi. Chọn nơi cất bằng CACHE_BACKEND + REDIS_URL (xem make_cache).
"""
import copy
import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")
_TRAILING = re.compile(r"[?.!…\s]+$")


def normalize(text: str) -> str:
    """Chuỗi khoá của một câu hỏi: chữ thường, mọi khoảng trắng (kể cả xuống dòng) liên tiếp gộp thành một dấu cách, bỏ khoảng
    trắng đầu cuối, bỏ các dấu ? . ! … ở CUỐI câu. Giữ nguyên dấu tiếng Việt ("đất" khác "dat") và dấu ở giữa câu."""
    return _TRAILING.sub("", _WHITESPACE.sub(" ", text.lower()).strip())


class TTLCache:
    """Cache LRU có hạn dùng. `max_items <= 0` hoặc `ttl_seconds <= 0` thì không lưu gì (get luôn trả None).
    get và set làm việc với BẢN SAO SÂU của giá trị: nơi gọi sửa list/dict nhận được không làm hỏng mục trong cache.
    Giá trị None không phân biệt được với "không có" ở get, nên đừng lưu None."""

    def __init__(self, max_items: int, ttl_seconds: float):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()  # key -> (hết hạn lúc, giá trị); cuối = dùng gần nhất
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self.max_items > 0 and self.ttl_seconds > 0

    def get(self, key):
        """Giá trị còn hạn (bản sao) hoặc None. Mục hết hạn bị xoá. Mục được lấy thành mới dùng gần nhất."""
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(value)

    def get_many(self, keys) -> dict:
        """Lấy nhiều khoá còn hạn (bản sao) dưới dạng dict {key: value}."""
        if not self.enabled or not keys:
            return {}
        with self._lock:
            out = {}
            for key in keys:
                hit = self.get(key)
                if hit is not None:
                    out[key] = hit
            return out

    def set(self, key, value) -> None:
        """Lưu bản sao của value. Đã đủ max_items thì bỏ mục dùng lâu nhất."""
        if not self.enabled or value is None:
            return
        with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def set_many(self, items: dict) -> bool:
        """Lưu nhiều mục {key: value} (bỏ qua value=None)."""
        if not self.enabled or not items:
            return True
        with self._lock:
            for key, value in items.items():
                if value is not None:
                    self.set(key, value)
        return True

    def delete(self, key) -> bool:
        """Xoá một khoá (không có cũng không sao). Luôn trả True: bộ nhớ process là nơi cất duy nhất."""
        with self._lock:
            self._items.pop(key, None)
        return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        """Số mục đang giữ (có thể gồm mục đã hết hạn nhưng chưa bị get dọn)."""
        with self._lock:
            return len(self._items)


# ── Redis ──────────────────────────────────────────────────────────────────────
# RedisCache / ResilientCache / make_cache: cache dùng chung giữa các process qua Redis, CÙNG giao diện với TTLCache
# (get/set/clear/enabled) để chỗ tạo cache chỉ việc thay lớp. rag_client và responder tạo cache qua make_cache.
#
# shortcut: mọi lệnh Redis ở đây là ĐỒNG BỘ nên một lần gọi có thể chặn luồng đang chạy tối đa bằng REDIS_TIMEOUT_SECONDS
# (mặc định 0,2 giây, đặt cho cả kết nối lẫn đọc/ghi). Vì vậy timeout phải ngắn và ResilientCache có ngắt mạch: lỗi liên tiếp thì
# bỏ qua Redis một lúc, không chờ timeout mỗi lần. Bộ đếm ngắt mạch không khoá (như circuit breaker của rag_client).

KEY_PREFIX = "legal:"
_NAMESPACE = re.compile(r"[A-Za-z0-9_-]+")
_SCAN_COUNT = 500  # gợi ý số khoá mỗi vòng SCAN khi clear()


def make_key(namespace: str, key) -> str:
    """Khoá Redis dạng chuỗi ổn định: "legal:<vùng>:<sha256 hex>". Khoá nhận y như TTLCache (tuple các thành phần) được đổi sang
    JSON có sắp xếp rồi băm, nên cùng khoá luôn ra cùng chuỗi, khác khoá thì khác, và chuỗi không chứa nội dung câu hỏi gốc.
    Khoá không đổi được sang JSON thì ném TypeError/ValueError (RedisCache.get/set bắt lại)."""
    raw = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{KEY_PREFIX}{namespace}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


_client = None


def _get_client():
    """Client Redis dùng chung cả process, tạo lười lần đầu cần dùng từ REDIS_URL. Timeout kết nối và đọc/ghi lấy từ cấu hình,
    trả chuỗi (decode_responses). Không thử lại trong thư viện (retry 0 lần): mặc định redis-py tự thử lại có chờ lùi, làm một
    lần gọi lâu hơn timeout. KHÔNG ghi URL vào log (URL có thể chứa mật khẩu)."""
    global _client
    if _client is None:
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        s = get_settings()
        _client = redis.Redis.from_url(
            s.redis_url,
            socket_timeout=s.redis_timeout_seconds,
            socket_connect_timeout=s.redis_timeout_seconds,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )
    return _client


def reset_client() -> None:
    """Bỏ client dùng chung để lần sau tạo lại (cho test)."""
    global _client
    _client = None


class RedisCache:
    """Cache Redis cho một vùng (namespace). Giá trị cất dạng JSON, mỗi mục là MỘT lệnh SET kèm EX (hạn dùng, giây) nên Redis tự xoá
    khi hết hạn. `ttl_seconds <= 0` thì không lưu gì. KHÔNG có max_items: Redis tự giới hạn bằng maxmemory (allkeys-lru trong compose).
    Lệnh Redis lỗi hoặc quá giờ thì ném lỗi ra ngoài (để ResilientCache đếm lỗi và chuyển sang dự phòng); giá trị/khoá không đổi được
    sang JSON thì bỏ qua kèm cảnh báo và KHÔNG tính là lỗi Redis. Log chỉ ghi tên vùng và loại lỗi, không ghi URL hay nội dung.
    Giá trị None không được cất (get trả None nghĩa là "không có")."""

    def __init__(self, namespace: str, ttl_seconds: float, client=None):
        if not _NAMESPACE.fullmatch(namespace):
            raise ValueError("namespace chỉ gồm chữ, số, '_' và '-'")
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self._client = client  # None = dùng client dùng chung của process (_get_client); test truyền Redis giả vào đây

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    def _redis(self):
        return self._client if self._client is not None else _get_client()

    def get(self, key):
        """Giá trị còn hạn hoặc None (không có, hết hạn, khoá/giá trị hỏng). Lệnh Redis lỗi thì ném lỗi."""
        if not self.enabled:
            return None
        try:
            redis_key = make_key(self.namespace, key)
        except (TypeError, ValueError):
            logger.warning("Cache Redis vùng %s: khoá không đổi được sang JSON, bỏ qua", self.namespace)
            return None
        raw = self._redis().get(redis_key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            logger.warning("Cache Redis vùng %s: mục đọc ra không phải JSON hợp lệ, coi như không có", self.namespace)
            return None

    def set(self, key, value) -> None:
        """Cất value (JSON) kèm hạn dùng bằng một lệnh SET ... EX. None hoặc giá trị không đổi được sang JSON thì bỏ qua."""
        if not self.enabled or value is None:
            return
        try:
            redis_key = make_key(self.namespace, key)
            payload = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.warning("Cache Redis vùng %s: khoá hoặc giá trị không đổi được sang JSON (%s), bỏ qua", self.namespace, type(e).__name__)
            return
        # hạn tính bằng giây nguyên, tối thiểu 1 (EX không nhận 0)
        self._redis().set(redis_key, payload, ex=max(1, math.ceil(self.ttl_seconds)))

    def get_many(self, keys) -> dict:
        """Đọc nhiều khoá bằng một lệnh MGET duy nhất. Khoá/giá trị hỏng JSON bị bỏ qua; lỗi mạng Redis ném ra ngoài."""
        if not self.enabled or not keys:
            return {}
        ordered_keys = []
        redis_keys = []
        seen = set()
        for key in keys:
            if key in seen:
                continue
            try:
                rk = make_key(self.namespace, key)
            except (TypeError, ValueError):
                logger.warning("Cache Redis vùng %s: khoá không đổi được sang JSON, bỏ qua", self.namespace)
                continue
            seen.add(key)
            ordered_keys.append(key)
            redis_keys.append(rk)
        if not redis_keys:
            return {}
        client = self._redis()
        mget = getattr(client, "mget", None)
        raw_values = mget(redis_keys) if callable(mget) else [client.get(rk) for rk in redis_keys]
        out = {}
        for key, raw in zip(ordered_keys, raw_values):
            if raw is None:
                continue
            try:
                out[key] = json.loads(raw)
            except ValueError:
                logger.warning("Cache Redis vùng %s: mục đọc ra không phải JSON hợp lệ, coi như không có", self.namespace)
        return out

    def set_many(self, items: dict) -> None:
        """Ghi nhiều mục bằng một pipeline SET ... EX không khóa giao dịch (1 vòng mạng thay vì N vòng)."""
        if not self.enabled or not items:
            return
        ttl = max(1, math.ceil(self.ttl_seconds))
        encoded: list[tuple[str, str]] = []
        for key, value in items.items():
            if value is None:
                continue
            try:
                encoded.append((make_key(self.namespace, key), json.dumps(value, ensure_ascii=False)))
            except (TypeError, ValueError) as e:
                logger.warning("Cache Redis vùng %s: khoá hoặc giá trị không đổi được sang JSON (%s), bỏ qua", self.namespace, type(e).__name__)
        if not encoded:
            return
        client = self._redis()
        pipeline_fn = getattr(client, "pipeline", None)
        if callable(pipeline_fn):
            pipe = pipeline_fn(transaction=False)
            for rk, payload in encoded:
                pipe.set(rk, payload, ex=ttl)
            pipe.execute()
        else:
            for rk, payload in encoded:
                client.set(rk, payload, ex=ttl)

    def delete(self, key) -> None:
        """Xoá MỘT khoá (không có cũng không sao). Lệnh Redis lỗi thì ném lỗi ra ngoài (như get/set)."""
        if not self.enabled:
            return
        try:
            redis_key = make_key(self.namespace, key)
        except (TypeError, ValueError):
            return
        self._redis().delete(redis_key)

    def clear(self) -> None:
        """Xoá các khoá của ĐÚNG vùng này: duyệt bằng SCAN theo tiền tố (không dùng KEYS), không đụng vùng khác hay khoá của ai khác."""
        client = self._redis()
        batch = []
        for redis_key in client.scan_iter(match=f"{KEY_PREFIX}{self.namespace}:*", count=_SCAN_COUNT):
            batch.append(redis_key)
            if len(batch) >= _SCAN_COUNT:
                client.delete(*batch)
                batch = []
        if batch:
            client.delete(*batch)


# ── Ngắt mạch dùng chung ───────────────────────────────────────────────────────
# MỘT ngắt mạch cho cả process (theo REDIS_URL), mọi vùng cache (rag, history) và việc đọc số phiên bản kho luật cùng dùng:
# một nơi thấy Redis chết thì mọi nơi khác bỏ qua Redis ngay, không phải tự chịu từng lần chờ.
# Vì sao: khi container redis bị dừng hẳn thì tên "redis" không còn phân giải được, việc tra tên mất ~4 giây rồi mới lỗi và timeout của
# redis-py (socket_connect_timeout) KHÔNG chặn được bước tra tên; nên lần lỗi đầu tiên phải mở ngắt mạch ngay.
_PROBE_TIMEOUT = 10.0  # giây: một lần thử lại (nửa mở) không báo kết quả quá chừng này thì cho yêu cầu khác thử thay
_connection_errors: tuple | None = None


def is_connection_error(error: BaseException) -> bool:
    """Lỗi KIỂU KẾT NỐI: không kết nối được, không tra được tên (socket.gaierror), từ chối kết nối, hết thời gian. Gồm mọi OSError
    (ConnectionError, TimeoutError, gaierror...) và redis.exceptions.ConnectionError / TimeoutError."""
    global _connection_errors
    if _connection_errors is None:
        errors: list = [OSError]
        try:
            import redis

            errors += [redis.exceptions.ConnectionError, redis.exceptions.TimeoutError]
        except Exception:  # noqa: BLE001 - thiếu thư viện thì chỉ còn OSError
            pass
        _connection_errors = tuple(errors)
    return isinstance(error, _connection_errors)


class Breaker:
    """Ngắt mạch đơn giản, an toàn đa luồng (FastAPI chạy hàm đồng bộ trong nhóm luồng).
    - Lỗi kiểu kết nối (is_connection_error) thì mở NGAY từ lần lỗi đầu; lỗi kiểu khác thì mở khi đủ `failures` lần lỗi LIÊN TIẾP.
    - Đang mở (trong `seconds` giây): allow() trả False, nơi gọi bỏ qua Redis hoàn toàn (không gọi client, không chờ).
    - Hết thời gian mở: allow() chỉ trả True cho MỘT yêu cầu duy nhất (nửa mở, lần thử lại); các yêu cầu khác vẫn bị bỏ qua tới khi
      lần thử đó báo kết quả. Thử lại lỗi thì mở tiếp `seconds` giây; thành công thì đóng (success()).
    Nơi gọi PHẢI báo kết quả bằng success() hoặc failure() sau mỗi lần allow() trả True."""

    def __init__(self, failures: int = 3, seconds: float = 30.0):
        self.failures = max(1, failures)
        self.seconds = seconds
        self.fails = 0
        self.open_until = 0.0  # > 0: đã từng mở; còn trong tương lai = đang ngắt mạch
        self._probe_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if not self.open_until:
                return True
            now = time.monotonic()
            if now < self.open_until or now < self._probe_until:
                return False
            self._probe_until = now + _PROBE_TIMEOUT  # yêu cầu này là lần thử lại duy nhất
            return True

    def success(self) -> None:
        with self._lock:
            self.fails, self.open_until, self._probe_until = 0, 0.0, 0.0

    def failure(self, error: BaseException) -> bool:
        """Ghi một lần lỗi; trả True nếu lần này làm ngắt mạch (mở hoặc mở tiếp)."""
        with self._lock:
            now = time.monotonic()
            was_probe = bool(self.open_until) and now >= self.open_until  # người báo lỗi là lần thử lại sau khi hết thời gian mở
            self.fails += 1
            self._probe_until = 0.0
            if was_probe or is_connection_error(error) or self.fails >= self.failures:
                self.open_until = now + self.seconds
                return True
            return False


_breakers: dict[str, Breaker] = {}
_breakers_lock = threading.Lock()


def shared_breaker(settings=None) -> Breaker:
    """Ngắt mạch dùng chung của process cho REDIS_URL hiện tại (tạo lần đầu cần dùng, theo redis_breaker_failures / redis_breaker_seconds)."""
    s = settings or get_settings()
    with _breakers_lock:
        breaker = _breakers.get(s.redis_url)
        if breaker is None:
            breaker = _breakers[s.redis_url] = Breaker(s.redis_breaker_failures, s.redis_breaker_seconds)
        return breaker


def reset_breakers() -> None:
    """Bỏ mọi ngắt mạch dùng chung (cho test)."""
    with _breakers_lock:
        _breakers.clear()


class ResilientCache:
    """Cache chịu lỗi: Redis (`primary`) là chính, TTLCache trong bộ nhớ (`fallback`) là dự phòng, cùng giao diện get/set/clear/enabled.
    - Redis chạy tốt: get/set chỉ nói chuyện với Redis, không đọc dự phòng.
    - Một lệnh Redis lỗi hoặc quá giờ: cảnh báo log (không kèm URL/giá trị), báo cho ngắt mạch (Breaker), lần gọi đó dùng dự phòng.
    - Ngắt mạch (Breaker) thường là bản DÙNG CHUNG của process (make_cache truyền shared_breaker()): lỗi kiểu kết nối mở ngay cho mọi vùng;
      lỗi khác mở khi đủ `breaker_failures` lần liên tiếp. Đang mở: BỎ QUA Redis, dùng dự phòng ngay, không chờ. Hết thời gian mở thì
      chỉ MỘT yêu cầu thử lại. Không truyền `breaker` thì mỗi ResilientCache có ngắt mạch riêng theo breaker_failures/breaker_seconds.
    - `primary=None` (không có REDIS_URL): chỉ dùng dự phòng, không bao giờ thử kết nối.
    `enabled` theo dự phòng (cả ttl lẫn max_items > 0), để đổi sang Redis không làm đổi ý nghĩa của cấu hình "<= 0 thì không lưu gì".
    Mục trong dự phòng không được đồng bộ sang Redis và ngược lại; lúc Redis lại hoạt động, dự phòng không còn được đọc."""

    def __init__(
        self, primary: "RedisCache | None", fallback: TTLCache, breaker_failures: int = 3, breaker_seconds: float = 30.0,
        breaker: "Breaker | None" = None,
    ):
        self._primary = primary
        self._fallback = fallback
        self._breaker_failures = max(1, breaker_failures)
        self._breaker_seconds = breaker_seconds
        self._breaker = breaker if breaker is not None else Breaker(breaker_failures, breaker_seconds)

    @property
    def _open_until(self) -> float:
        return self._breaker.open_until

    @property
    def enabled(self) -> bool:
        return self._fallback.enabled

    def _redis_allowed(self) -> bool:
        """True nếu được phép gọi Redis lúc này. LƯU Ý: có tác dụng phụ (nửa mở: chỉ một yêu cầu được thử lại), nên gọi đúng MỘT lần
        cho mỗi thao tác rồi báo kết quả bằng _on_success / _on_failure."""
        if self._primary is None:
            return False
        return self._breaker.allow()

    def _on_success(self) -> None:
        self._breaker.success()

    def _on_failure(self, error: Exception) -> None:
        name = self._primary.namespace
        logger.warning("Cache Redis vùng %s lỗi (%s), dùng cache bộ nhớ thay thế", name, type(error).__name__)
        if self._breaker.failure(error):
            logger.warning("Cache Redis: ngắt mạch, bỏ qua Redis %s giây cho mọi vùng (lỗi %s)", self._breaker.seconds, type(error).__name__)

    def get_checked(self, key) -> tuple:
        """Như get nhưng trả thêm (giá trị, có_thẩm_quyền): False khi Redis lỗi/đang ngắt mạch nên giá trị (hoặc None) chỉ là của bản dự
        phòng và KHÔNG cho biết Redis đang giữ gì. True khi trả lời từ Redis, hoặc khi không có Redis (bộ nhớ là nơi cất duy nhất)."""
        if not self.enabled:
            return None, True
        if self._redis_allowed():
            try:
                value = self._primary.get(key)
            except Exception as e:  # mọi lỗi/timeout từ Redis: cache không được làm hỏng request
                self._on_failure(e)
            else:
                self._on_success()
                return value, True
        return self._fallback.get(key), self._primary is None

    def get(self, key):
        return self.get_checked(key)[0]

    def get_many(self, keys) -> dict:
        """Đọc nhiều khoá qua một lượt MGET của Redis; lỗi hoặc đang ngắt mạch thì đọc từ bản dự phòng bộ nhớ."""
        if not self.enabled or not keys:
            return {}
        if self._redis_allowed():
            try:
                values = self._primary.get_many(keys)
            except Exception as e:
                self._on_failure(e)
            else:
                self._on_success()
                return values
        return self._fallback.get_many(keys)

    def set(self, key, value) -> bool:
        """Lưu value. Trả False khi Redis lỗi/đang ngắt mạch nên chỉ lưu được vào bản dự phòng (Redis có thể còn giữ giá trị CŨ);
        True khi đã lưu qua Redis hoặc khi không có Redis. Nơi gọi cũ bỏ qua giá trị trả về."""
        if not self.enabled:
            return True
        if self._redis_allowed():
            try:
                self._primary.set(key, value)
            except Exception as e:
                self._on_failure(e)
            else:
                self._on_success()
                return True
        self._fallback.set(key, value)
        return self._primary is None

    def set_many(self, items: dict) -> bool:
        """Lưu nhiều mục qua một pipeline của Redis; lỗi hoặc đang ngắt mạch thì lưu vào bản dự phòng bộ nhớ."""
        if not self.enabled or not items:
            return True
        if self._redis_allowed():
            try:
                self._primary.set_many(items)
            except Exception as e:
                self._on_failure(e)
            else:
                self._on_success()
                return True
        self._fallback.set_many(items)
        return self._primary is None

    def delete(self, key) -> bool:
        """Xoá khoá khỏi bản dự phòng và khỏi Redis. Trả True khi Redis đã xác nhận xoá (hoặc không có Redis); False khi Redis lỗi/đang
        ngắt mạch, tức là Redis CÓ THỂ còn giữ khoá: nơi gọi phải coi khoá đó là không tin được."""
        self._fallback.delete(key)
        if self._primary is None:
            return True
        if self._redis_allowed():
            try:
                self._primary.delete(key)
            except Exception as e:
                self._on_failure(e)
                return False
            self._on_success()
            return True
        return False

    def clear(self) -> None:
        """Xoá dự phòng và (nếu Redis dùng được) các khoá của vùng này trong Redis. Redis lỗi thì khoá trong Redis còn nguyên tới hết hạn."""
        self._fallback.clear()
        if self._redis_allowed():
            try:
                self._primary.clear()
            except Exception as e:
                self._on_failure(e)
            else:
                self._on_success()


_warned_backends: set[str] = set()  # giá trị CACHE_BACKEND lạ đã cảnh báo (mỗi giá trị một lần)

# ── Số phiên bản kho luật ──────────────────────────────────────────────────────
# rag-service tăng khoá CORPUS_VERSION_KEY trong Redis (INCR) mỗi khi duyệt/thay/nạp luật xong; agent đọc số này và đưa vào khoá cache
# tìm luật, nên sau khi đổi luật các mục cũ tự không được dùng nữa (khoá khác), không cần restart hay xoá tay.
_VERSION_WARN_EVERY = 30.0  # giây: cảnh báo lỗi đọc số phiên bản tối đa một lần mỗi khoảng này, tránh ngập log khi Redis chết
_version: str | None = None
_version_at = 0.0
_version_warned_at = -1e9


def reset_corpus_version() -> None:
    """Bỏ số phiên bản đã nhớ trong process (cho test)."""
    global _version, _version_at, _version_warned_at
    _version, _version_at, _version_warned_at = None, 0.0, -1e9


def get_corpus_version() -> str:
    """Số phiên bản kho luật dạng chuỗi. Không dùng Redis (CACHE_BACKEND khác redis hoặc REDIS_URL rỗng) thì luôn "0", không chạm Redis.
    Khoá chưa có thì "0". Redis lỗi thì "0" kèm cảnh báo (không kèm URL, tối đa một lần mỗi 30 giây), không ném lỗi.
    Kết quả được nhớ trong process CORPUS_VERSION_CACHE_SECONDS giây (mặc định 5; 0 = không nhớ) để mỗi câu hỏi không phải gọi Redis
    thêm một lần. Đồng bộ như phần còn lại: một lần đọc có thể chặn tối đa bằng REDIS_TIMEOUT_SECONDS (riêng bước tra tên của Docker
    có thể mất ~4 giây, nên việc đọc đi qua ngắt mạch DÙNG CHUNG: đang ngắt mạch thì trả "0" ngay, không gọi Redis, không chờ).
    shortcut: đổi luật xong có thể mất tối đa chừng đó giây mới có hiệu lực. Nếu khoá bị Redis đẩy ra (allkeys-lru) thì số quay về "0"
    và các khoá cache cũ mang số đó có thể được dùng lại tới hết TTL."""
    global _version, _version_at, _version_warned_at
    s = get_settings()
    if not _use_redis(s):
        return "0"
    now = time.monotonic()
    if _version is not None and s.corpus_version_cache_seconds > 0 and now - _version_at < s.corpus_version_cache_seconds:
        return _version
    breaker = shared_breaker(s)
    if not breaker.allow():
        return "0"  # đang ngắt mạch (hoặc đã có yêu cầu khác đang thử lại): không gọi Redis, không chờ; không nhớ kết quả này
    try:
        raw = _get_client().get(s.corpus_version_key)
    except Exception as e:  # noqa: BLE001 - cache là phần phụ, đọc số phiên bản lỗi không được làm hỏng câu hỏi
        breaker.failure(e)
        if now - _version_warned_at >= _VERSION_WARN_EVERY:
            _version_warned_at = now
            logger.warning("Không đọc được số phiên bản kho luật từ Redis (%s), dùng \"0\"", type(e).__name__)
        return "0"
    breaker.success()
    value = "0" if raw is None else str(raw)
    if not value.isdigit():
        value = "0"  # Redis trả lời được nhưng giá trị không phải số: coi như chưa có
    _version, _version_at = value, now
    return value


def _use_redis(s) -> bool:
    """Chỉ dùng Redis khi CACHE_BACKEND là "redis" VÀ REDIS_URL không rỗng. "memory" hoặc giá trị lạ (cảnh báo một lần) thì không."""
    backend = (s.cache_backend or "").strip().lower()
    if backend == "redis":
        return bool(s.redis_url)
    if backend != "memory" and backend not in _warned_backends:
        _warned_backends.add(backend)
        logger.warning("CACHE_BACKEND có giá trị không hợp lệ (chỉ nhận redis hoặc memory), dùng cache bộ nhớ")
    return False


def make_cache(namespace: str, max_items: int, ttl_seconds: float):
    """Dựng cache cho một vùng: ResilientCache (Redis + dự phòng TTLCache) khi CACHE_BACKEND=redis và REDIS_URL có giá trị; còn lại
    (memory, giá trị lạ, REDIS_URL rỗng) thì TTLCache như trước. max_items chỉ giới hạn cache dự phòng trong bộ nhớ, không giới hạn Redis."""
    s = get_settings()
    fallback = TTLCache(max_items, ttl_seconds)
    if not _use_redis(s):
        return fallback
    return ResilientCache(
        RedisCache(namespace, ttl_seconds), fallback, s.redis_breaker_failures, s.redis_breaker_seconds, breaker=shared_breaker(s),
    )
