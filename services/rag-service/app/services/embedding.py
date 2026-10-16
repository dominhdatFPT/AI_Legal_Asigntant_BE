"""Tạo embedding (vector) cho văn bản.

- GeminiEmbedder: gọi Gemini API, model gemini-embedding-001, rút còn 768 chiều (MRL).
  Khi nạp văn bản dùng task_type RETRIEVAL_DOCUMENT, khi tìm kiếm dùng RETRIEVAL_QUERY.
- FakeEmbedder: vector băm từ khóa, không gọi mạng. Dùng cho test và chạy thử offline.

Muốn đổi sang model khác (VD bge-m3 chạy trên máy): viết class mới có cùng 4 thứ
`model_name`, `dim`, `embed_documents`, `embed_query`, rồi sửa get_embedder().
Đổi model = phải nạp lại toàn bộ văn bản (vector của 2 model không so sánh được với nhau).
"""

import hashlib
import logging
import math
import re
import time
import unicodedata
from collections import deque
from functools import lru_cache
from typing import Callable, Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 503, 504}


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


class GeminiEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        dim: int = 768,
        batch_size: int = 20,
        client=None,  # test truyền client giả
        sleep: Callable[[float], None] = time.sleep,
        tokens_per_minute: int = 0,  # 0 = không tự giảm tốc
        clock: Callable[[], float] = time.monotonic,
    ):
        if client is None:
            if not api_key:
                raise EmbeddingError("Chưa cấu hình GEMINI_API_KEY")
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._sleep = sleep
        self.model_name = model
        self.dim = dim
        self.batch_size = batch_size
        self.tokens_per_minute = tokens_per_minute
        self._clock = clock
        self._sent: deque[tuple[float, int]] = deque()  # (thời điểm gửi, số token ước tính)

    @staticmethod
    def estimate_tokens(texts: list[str]) -> int:
        """Ước lượng token: tiếng Việt khoảng 3 ký tự / token (chỉ dùng để giảm tốc, không cần chính xác)."""
        return sum(len(t) for t in texts) // 3 + 1

    def _throttle(self, tokens: int) -> None:
        """Gói free giới hạn token mỗi phút. Chờ trước khi gửi để không bị 429 liên tục."""
        if self.tokens_per_minute <= 0:
            return
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0][0] >= 60:
                self._sent.popleft()
            used = sum(t for _, t in self._sent)
            if not self._sent or used + tokens <= self.tokens_per_minute:
                break
            wait = 60 - (now - self._sent[0][0]) + 0.5
            logger.info("Gần chạm giới hạn %d token/phút, chờ %.0fs", self.tokens_per_minute, wait)
            self._sleep(wait)
        self._sent.append((self._clock(), tokens))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Dùng khi nạp văn bản. Gói free giới hạn theo phút nên chờ lâu hơn khi bị 429."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            self._throttle(self.estimate_tokens(batch))
            out.extend(self._call(batch, "RETRIEVAL_DOCUMENT", delays=(5, 15, 30, 60, 60)))
            logger.info("Đã embedding %d/%d đoạn", len(out), len(texts))
        return out

    def embed_query(self, text: str) -> list[float]:
        """Dùng khi người dùng hỏi. Thử lại ít vì agent-service chỉ chờ 10 giây."""
        return self._call([text], "RETRIEVAL_QUERY", delays=(1, 2))[0]

    def _call(self, batch: list[str], task_type: str, delays: tuple[float, ...]) -> list[list[float]]:
        from google.genai import errors, types

        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        for attempt in range(len(delays) + 1):
            try:
                resp = self._client.models.embed_content(
                    model=self.model_name, contents=batch, config=config
                )
                break
            except errors.APIError as e:
                if e.code in RETRYABLE_STATUS and attempt < len(delays):
                    logger.warning("Gemini embedding lỗi %s, thử lại sau %ss", e.code, delays[attempt])
                    self._sleep(delays[attempt])
                    continue
                raise EmbeddingError(f"Gemini embedding lỗi {e.code}: {e.message}") from e
            except Exception as e:  # lỗi mạng, timeout...
                if attempt < len(delays):
                    self._sleep(delays[attempt])
                    continue
                raise EmbeddingError(f"Không gọi được Gemini embedding: {e}") from e

        embeddings = resp.embeddings or []
        if len(embeddings) != len(batch):
            raise EmbeddingError(f"Gemini trả {len(embeddings)} vector cho {len(batch)} đoạn")
        vectors = []
        for emb in embeddings:
            values = list(emb.values or [])
            if len(values) != self.dim:
                raise EmbeddingError(f"Vector có {len(values)} chiều, cần {self.dim}")
            # gemini-embedding-001 chỉ chuẩn hóa sẵn ở 3072 chiều; 768 chiều phải tự chuẩn hóa
            vectors.append(l2_normalize(values))
        return vectors


_WORD = re.compile(r"\w+", re.UNICODE)


class FakeEmbedder:
    """Vector 'túi từ' băm vào `dim` ô. Câu có nhiều từ chung → vector gần nhau.
    Không hiểu nghĩa (không biết 'sổ đỏ' = 'giấy chứng nhận'), chỉ để test luồng."""

    def __init__(self, dim: int = 768):
        self.dim = dim
        self.model_name = f"fake-hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        text = unicodedata.normalize("NFC", text).lower()
        words = _WORD.findall(text)
        features = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
        vec = [0.0] * self.dim
        for f in features:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "big")
            vec[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        return l2_normalize(vec)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    from app.models.chunk import EMBEDDING_DIM

    if s.embedding_dim != EMBEDDING_DIM:
        raise EmbeddingError(
            f"EMBEDDING_DIM={s.embedding_dim} không khớp vector({EMBEDDING_DIM}) trong database"
        )
    provider = s.embedding_provider.lower()
    if provider == "fake":
        return FakeEmbedder(s.embedding_dim)
    if provider == "gemini":
        return GeminiEmbedder(
            api_key=s.gemini_api_key,
            model=s.embedding_model,
            dim=s.embedding_dim,
            batch_size=s.embedding_batch_size,
            tokens_per_minute=s.embedding_tokens_per_minute,
        )
    raise EmbeddingError(f"EMBEDDING_PROVIDER không hợp lệ: {s.embedding_provider}")
