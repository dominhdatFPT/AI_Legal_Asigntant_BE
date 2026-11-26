"""Cache lịch sử trò chuyện: history_max_messages (HISTORY_MAX_MESSAGES, mặc định 12) tin gần nhất của mỗi hội thoại, dạng [{"role", "content"}] ĐÚNG như conversations._load_for_chat
trả ra. Supabase (bảng messages) VẪN là nguồn gốc duy nhất; đây chỉ là bản sao để mỗi lượt chat khỏi đọc DB. Quyền sở hữu hội thoại
KHÔNG đi qua cache: conversations._owned_conversation vẫn kiểm trong DB mỗi lượt, cache chỉ thay phần đọc tin nhắn.

Khoá = (mã người dùng, mã hội thoại), vùng "history" của cache.make_cache (Redis chính, bộ nhớ dự phòng, theo CACHE_BACKEND). KHÔNG có số
phiên bản kho luật: lịch sử trò chuyện không phụ thuộc luật hay prompt.

Giao thức (ghi xuyên, KHÔNG write-behind):
- ĐỌC: có trong cache thì dùng; không thì nơi gọi đọc Supabase rồi `put`. Danh sách rỗng không bao giờ được lưu (hội thoại mới), nên "tin
  đầu của hội thoại" (history rỗng) luôn do DB quyết định.
- GHI: `append` chỉ được gọi SAU KHI Supabase ghi cặp hỏi-đáp thành công. Cache đang có lịch sử thì nối cặp mới và cắt còn history_max_messages
  tin cuối; cache chưa có thì để trống (không tự dựng). Supabase ghi lỗi thì nơi gọi không gọi append nên cache không đổi.
- XOÁ: `invalidate` sau khi xoá hội thoại thành công.
- Lỗi cache không bao giờ làm hỏng chat: mọi lỗi → coi như không có cache. Sau một lần ghi DB thành công mà không cập nhật được cache
  thì cố xoá khoá; nếu cả việc xoá cũng không làm được (Redis chết) thì khoá bị ghi vào `_dirty`: mọi lần đọc sau đó của khoá này bỏ qua cache
  (đọc Supabase) và không `put` cho tới khi xoá được khoá trong Redis, nên khi Redis sống lại lịch sử cũ không thể xuất hiện.
shortcut: `_dirty` nằm trong bộ nhớ từng process. Chạy nhiều bản agent-service dùng chung Redis mà Redis chết đúng lúc một bản ghi tin thì
bản khác có thể đọc lịch sử cũ tới hết HISTORY_CACHE_TTL_SECONDS (3600 giây). Cache bộ nhớ (CACHE_BACKEND=memory hoặc không có REDIS_URL)
chỉ an toàn khi chạy MỘT process (mặc định hiện tại: một worker uvicorn).
Log chỉ ghi loại lỗi, không ghi nội dung tin nhắn, URL hay mã người dùng."""
import logging
import uuid

from app.core.config import get_settings
from app.services import cache

logger = logging.getLogger(__name__)

_cache = None
_dirty: set[tuple[str, str]] = set()  # khoá mà lần xoá/cập nhật gần nhất không xác nhận được ở Redis: không tin cho tới khi xoá được


def _get_cache():
    """Tạo lần đầu cần dùng, theo cấu hình lúc đó (vùng "history")."""
    global _cache
    if _cache is None:
        s = get_settings()
        _cache = cache.make_cache("history", s.history_cache_max_items, s.history_cache_ttl_seconds)
    return _cache


def reset() -> None:
    """Bỏ đối tượng cache và danh sách khoá không tin được (cho test); KHÔNG xoá khoá trong Redis."""
    global _cache
    _cache = None
    _dirty.clear()


def _key(user_id: uuid.UUID | str, conversation_id: uuid.UUID | str) -> tuple[str, str]:
    return (str(user_id), str(conversation_id))


def _enabled() -> bool:
    return get_settings().history_cache_enabled


def _valid(history) -> bool:
    return (
        isinstance(history, list) and bool(history)
        and all(isinstance(m, dict) and isinstance(m.get("role"), str) and isinstance(m.get("content"), str) for m in history)
    )


def _delete(key) -> bool:
    """Xoá khoá; True khi chắc chắn không còn ở Redis. Không bao giờ ném lỗi."""
    try:
        return _get_cache().delete(key) is not False
    except Exception as e:  # noqa: BLE001
        logger.warning("history cache lỗi khi xoá khoá (%s)", type(e).__name__)
        return False


def _invalidate_key(key) -> None:
    if not _delete(key):
        _dirty.add(key)
        logger.warning("history cache: chưa xoá được khoá ở Redis, bỏ qua cache của hội thoại đó cho tới khi xoá được")


def get(user_id, conversation_id) -> list[dict] | None:
    """Lịch sử trong cache (danh sách mới, sửa thoải mái) hoặc None (không có / hỏng / cache tắt / lỗi): nơi gọi đọc Supabase."""
    if not _enabled():
        return None
    key = _key(user_id, conversation_id)
    try:
        if key in _dirty:
            if not _delete(key):
                return None
            _dirty.discard(key)
        value, _authoritative = _get_checked(key)
        if value is None:
            return None
        if not _valid(value):
            logger.warning("history cache: mục đọc ra sai hình dạng, bỏ và đọc Supabase")
            _invalidate_key(key)
            return None
        return value
    except Exception as e:  # noqa: BLE001 - cache là phần phụ
        logger.warning("history cache lỗi khi đọc (%s), đọc Supabase như cũ", type(e).__name__)
        return None


def _get_checked(key):
    c = _get_cache()
    fn = getattr(c, "get_checked", None)
    return fn(key) if fn else (c.get(key), True)


def put(user_id, conversation_id, history: list[dict]) -> None:
    """Lưu lịch sử vừa đọc từ Supabase. Rỗng thì không lưu. Khoá đang không tin được thì không lưu."""
    if not _enabled() or not history:
        return
    key = _key(user_id, conversation_id)
    if key in _dirty:
        return
    try:
        _get_cache().set(key, history)
    except Exception as e:  # noqa: BLE001
        logger.warning("history cache lỗi khi lưu (%s)", type(e).__name__)


def append(user_id, conversation_id, new_messages: list[dict], limit: int) -> None:
    """GỌI SAU KHI Supabase đã ghi cặp tin mới thành công. `new_messages` là [{"role","content"}] lấy từ chính các dòng vừa chèn."""
    if not _enabled():
        return
    key = _key(user_id, conversation_id)
    try:
        current, authoritative = _get_checked(key)
        if not authoritative:
            # Redis không trả lời được: không biết nó đang giữ gì, nên không thể nối thêm. Xoá khoá (đánh dấu nếu không xoá được)
            _invalidate_key(key)
            return
        if current is None:
            return  # chưa có trong cache thì để trống, lần đọc sau đọc Supabase
        if not _valid(current):
            _invalidate_key(key)
            return
        stored = _get_cache().set(key, (current + new_messages)[-limit:])
        if stored is False:
            _invalidate_key(key)  # chỉ lưu được vào bản dự phòng, Redis có thể còn giá trị cũ
    except Exception as e:  # noqa: BLE001
        logger.warning("history cache lỗi khi cập nhật sau lần ghi DB (%s), xoá khoá", type(e).__name__)
        _invalidate_key(key)


def invalidate(user_id, conversation_id) -> None:
    """Xoá khoá của hội thoại (sau khi xoá hội thoại thành công, hoặc mọi chỗ khác làm đổi tin nhắn)."""
    if not _enabled():
        return
    _invalidate_key(_key(user_id, conversation_id))
