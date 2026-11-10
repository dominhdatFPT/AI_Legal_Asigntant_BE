"""Cache trong bộ nhớ có hạn dùng (TTL) và giới hạn số mục, cùng hàm chuẩn hoá câu hỏi làm khoá. Chưa nối vào luồng nào.

shortcut: cache theo từng process, mất khi restart, không khoá; đủ cho 1 worker uvicorn. Khi chạy nhiều worker/replica thì mỗi cái
một cache riêng, kết quả có thể khác nhau giữa các bản; cần dùng chung thì cân nhắc Redis.
"""
import copy
import re
import time
from collections import OrderedDict
from typing import Any

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

    @property
    def enabled(self) -> bool:
        return self.max_items > 0 and self.ttl_seconds > 0

    def get(self, key):
        """Giá trị còn hạn (bản sao) hoặc None. Mục hết hạn bị xoá. Mục được lấy thành mới dùng gần nhất."""
        entry = self._items.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(value)

    def set(self, key, value) -> None:
        """Lưu bản sao của value. Đã đủ max_items thì bỏ mục dùng lâu nhất."""
        if not self.enabled:
            return
        self._items[key] = (time.monotonic() + self.ttl_seconds, copy.deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        """Số mục đang giữ (có thể gồm mục đã hết hạn nhưng chưa bị get dọn)."""
        return len(self._items)
