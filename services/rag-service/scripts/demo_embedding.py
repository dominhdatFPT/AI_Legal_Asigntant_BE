"""Demo embedding cho buổi báo cáo với thầy/mentor.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv):
    python services/rag-service/scripts/demo_embedding.py

Mặc định dùng FakeEmbedder (không cần mạng, không cần API key).
Để dùng Gemini thật: đặt EMBEDDING_PROVIDER=gemini trong .env và bật VPN/mạng.

Output mong đợi khi thành công:
    ✅ 1. Số chiều vector: 768
    ✅ 2. Cặp liên quan:  score ≈ 0.6–1.0
    ✅ 3. Cặp không liên quan: score < 0.4
    ✅ 4. Kết quả tìm kiếm: 3 chunk với score, nội dung, trích dẫn

Lỗi thường gặp:
    ImportError: chưa cài requirements → pip install -r services/rag-service/requirements.txt
    EmbeddingError: GEMINI_API_KEY chưa điền → đổi EMBEDDING_PROVIDER=fake trong .env
    Network timeout: mạng chặn Google API → đổi EMBEDDING_PROVIDER=fake hoặc bật VPN
"""
import math
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]   # services/rag-service
ROOT_DIR    = SERVICE_DIR.parents[1]                # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")

# Dùng FakeEmbedder nếu không có Gemini (chạy offline)
if not os.getenv("EMBEDDING_PROVIDER"):
    os.environ["EMBEDDING_PROVIDER"] = "fake"
    print("⚠️  EMBEDDING_PROVIDER không đặt → dùng FakeEmbedder (offline, không cần API key)\n")

from app.services.embedding import FakeEmbedder, get_embedder  # noqa: E402
from app.services.chunking import chunk_legal_text, embedding_text  # noqa: E402


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def separator(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


# ─── 1. Khởi tạo embedder ────────────────────────────────────────────────────
separator("1. Khởi tạo Embedder")
try:
    embedder = get_embedder()
    print(f"Provider : {os.getenv('EMBEDDING_PROVIDER', 'fake')}")
    print(f"Model    : {embedder.model_name}")
    print(f"✅ Số chiều vector: {embedder.dim}")
except Exception as e:
    print(f"❌ Không tạo được embedder: {e}")
    sys.exit(1)


# ─── 2. In 10 số đầu của 1 vector ───────────────────────────────────────────
separator("2. Xem thử 1 vector")
sample_text = "Điều 188. Điều kiện thực hiện các quyền của người sử dụng đất"
vec = embedder.embed_query(sample_text)
print(f"Text: \"{sample_text}\"")
print(f"Vector ({len(vec)} chiều), 10 số đầu:")
print("  [" + ", ".join(f"{x:+.4f}" for x in vec[:10]) + ", ...]")
norm = math.sqrt(sum(x * x for x in vec))
print(f"Norm (độ dài vector): {norm:.6f}  {'✅ đã normalize ≈ 1.0' if abs(norm - 1.0) < 0.001 else '⚠️ chưa normalize'}")


# ─── 3. So sánh cặp câu liên quan vs không liên quan ────────────────────────
separator("3. So sánh similarity")

pairs = [
    (
        "liên quan",
        "Thủ tục chuyển nhượng quyền sử dụng đất",
        "Hồ sơ sang tên sổ đỏ gồm những giấy tờ gì?",
    ),
    (
        "liên quan",
        "Điều kiện thừa kế đất đai theo pháp luật Việt Nam",
        "Con cái có được hưởng quyền sử dụng đất của cha mẹ không?",
    ),
    (
        "không liên quan",
        "Thủ tục chuyển nhượng quyền sử dụng đất",
        "Công thức tính diện tích hình tròn bằng pi nhân r bình phương",
    ),
    (
        "không liên quan",
        "Luật Kinh doanh bất động sản 2023",
        "Cách nấu phở bò truyền thống miền Bắc",
    ),
]

is_fake = isinstance(embedder, FakeEmbedder)
if is_fake:
    print("⚠️  FakeEmbedder không hiểu nghĩa — cặp liên quan có thể score=0 nếu không share từ.")
    print("    Với Gemini thật: cặp liên quan ≈ 0.7–0.9, không liên quan < 0.3\n")

for label, text_a, text_b in pairs:
    vec_a = embedder.embed_query(text_a)
    vec_b = embedder.embed_query(text_b)
    sim   = cosine_similarity(vec_a, vec_b)
    icon  = "📝" if is_fake else (
        "✅" if (label == "liên quan" and sim > 0.4) or (label == "không liên quan" and sim < 0.6) else "⚠️"
    )
    print(f"\n{icon} [{label.upper()}]  score = {sim:.4f}")
    print(f"   A: \"{text_a[:60]}\"")
    print(f"   B: \"{text_b[:60]}\"")


# ─── 4. Demo chunking + retrieval giả lập ──────────────────────────────────
separator("4. Demo chunking + tìm kiếm (in-memory, không cần DB)")

SAMPLE_LAW = """
Điều 188. Điều kiện thực hiện các quyền của người sử dụng đất
1. Người sử dụng đất được thực hiện các quyền chuyển đổi, chuyển nhượng, cho thuê, cho thuê lại, thừa kế, tặng cho, thế chấp quyền sử dụng đất; góp vốn bằng quyền sử dụng đất khi có các điều kiện sau đây:
a) Có Giấy chứng nhận, trừ trường hợp quy định tại khoản 3 Điều 186 và trường hợp nhận thừa kế quy định tại khoản 1 Điều 168 của Luật này;
b) Đất không có tranh chấp;
c) Quyền sử dụng đất không bị kê biên để bảo đảm thi hành án;
d) Trong thời hạn sử dụng đất.

Điều 189. Quyền chuyển đổi quyền sử dụng đất của hộ gia đình, cá nhân
1. Hộ gia đình, cá nhân sử dụng đất nông nghiệp được Nhà nước giao trong hạn mức; đất được Nhà nước giao có thu tiền sử dụng đất, cho thuê đất trả tiền thuê đất một lần cho cả thời gian thuê; đất nhận chuyển đổi, nhận chuyển nhượng, nhận thừa kế, được tặng cho quyền sử dụng đất; đất sử dụng trước ngày 15 tháng 10 năm 1993 theo quy định tại điểm b khoản 1 Điều 100 của Luật này thì được chuyển đổi quyền sử dụng đất nông nghiệp với hộ gia đình, cá nhân khác trong cùng xã, phường, thị trấn để thuận lợi cho sản xuất nông nghiệp mà không phải nộp thuế thu nhập từ việc chuyển đổi và không phải nộp lệ phí trước bạ.

Điều 190. Quyền chuyển nhượng quyền sử dụng đất
1. Người sử dụng đất được chuyển nhượng quyền sử dụng đất theo quy định sau đây:
a) Người sử dụng đất được chuyển nhượng quyền sử dụng đất, trừ trường hợp quy định tại Điều 191 của Luật này;
b) Hộ gia đình, cá nhân không được chuyển nhượng quyền sử dụng đất trong các trường hợp: đất được Nhà nước giao đất không thu tiền sử dụng đất cho hộ gia đình, cá nhân; đất do Nhà nước giao có thu tiền sử dụng đất bằng nguồn vốn hỗ trợ từ ngân sách nhà nước.
"""

doc_title = "Luật Đất đai 2024 (31/2024/QH15)"
chunks    = chunk_legal_text(SAMPLE_LAW)
print(f"\nVăn bản mẫu: {doc_title}")
print(f"Cắt được {len(chunks)} chunk:")
for i, c in enumerate(chunks):
    info = c.section_info
    tag  = f"Điều {info.get('dieu','?')}"
    if info.get('khoan'): tag += f" Khoản {info['khoan']}"
    if info.get('diem'):  tag += f" Điểm {info['diem']}"
    print(f"  [{i+1}] {tag} — {len(c.content)} ký tự")

# Tạo mini vector store in-memory
print("\n⚙️  Tạo embedding cho các chunk...")
texts    = [embedding_text(doc_title, c) for c in chunks]
vectors  = embedder.embed_documents(texts)
print(f"   → {len(vectors)} vector × {len(vectors[0])} chiều")

# Tìm kiếm
QUERY = "Muốn chuyển nhượng đất cần có điều kiện gì?"
print(f"\n🔍 Câu hỏi: \"{QUERY}\"")
query_vec = embedder.embed_query(QUERY)

results = sorted(
    [(cosine_similarity(query_vec, v), chunks[i]) for i, v in enumerate(vectors)],
    reverse=True,
)

print(f"\nTop 3 chunk liên quan nhất:")
for rank, (score, chunk) in enumerate(results[:3], 1):
    info = chunk.section_info
    tag  = f"Điều {info.get('dieu','?')}"
    if info.get('khoan'): tag += f" Khoản {info['khoan']}"
    if info.get('diem'):  tag += f" Điểm {info['diem']}"
    snippet = chunk.content[:120].replace("\n", " ")
    print(f"\n  #{rank}  score={score:.4f}  [{tag}]")
    print(f"       \"{snippet}...\"")

separator("✅ Demo hoàn tất")
print("Giải thích kết quả:")
print("- Score gần 1.0 = rất liên quan")
print("- Score < 0.3   = ít liên quan")
print("- FakeEmbedder hoạt động bằng hash từ khóa nên score thấp hơn Gemini thật")
print("  nhưng đủ để demo luồng code không cần mạng.")
