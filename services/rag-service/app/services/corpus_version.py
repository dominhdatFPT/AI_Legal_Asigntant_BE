"""Số phiên bản kho luật trong Redis: rag-service TĂNG số này mỗi khi dữ liệu mà tìm kiếm nhìn thấy đổi xong (duyệt bản nháp, thay nội
dung, nạp luật mới); agent-service ĐỌC số này và đưa vào khoá cache tìm luật / cache câu trả lời, nên sau khi đổi luật cache cũ
tự không được dùng nữa (khoá khác), không cần restart hay xoá tay. Hai service phải dùng CÙNG tên khoá (CORPUS_VERSION_KEY).

Mọi lỗi đều bị bắt: việc duyệt hay thay luật đã commit xong vào DB, tăng phiên bản chỉ là việc phụ, không được làm hỏng nó.
Không tăng được thì cache tìm luật và câu trả lời có thể còn bản cũ tới hết TTL (cảnh báo log nói rõ điều này)."""
import logging

from app.core.config import get_settings
from app.services import hybrid

logger = logging.getLogger(__name__)


def bump_corpus_version() -> int | None:
    """Tăng số phiên bản bằng MỘT lệnh INCR (nguyên tử) trên khoá CORPUS_VERSION_KEY, trả về số mới. REDIS_URL rỗng thì không làm gì
    và trả None. Lỗi (không kết nối, quá giờ...) thì ghi cảnh báo (không kèm URL) và trả None, không bao giờ ném lỗi.
    Mở một kết nối riêng rồi đóng ngay: việc này hiếm (mỗi lần duyệt/thay luật xong), không cần client dùng chung."""
    hybrid.reset_index()  # The writer also invalidates its process-local BM25 cache after commit.
    s = get_settings()
    if not s.redis_url:
        return None
    client = None
    try:
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        client = redis.Redis.from_url(
            s.redis_url,
            socket_timeout=s.redis_timeout_seconds,
            socket_connect_timeout=s.redis_timeout_seconds,
            decode_responses=True,
            retry=Retry(NoBackoff(), 0),
        )
        return int(client.incr(s.corpus_version_key))
    except Exception as e:  # noqa: BLE001 - mọi lỗi đều không được làm hỏng việc duyệt/thay luật
        logger.warning(
            "Không tăng được số phiên bản kho luật trong Redis (%s): cache tìm luật và cache câu trả lời của agent-service "
            "có thể còn bản cũ tới hết TTL",
            type(e).__name__,
        )
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
