"""Cổng kiểm tra tài liệu đính kèm trước khi tìm luật và gọi AI: tài liệu rỗng, quá ít chữ hoặc chữ rác (OCR hỏng) thì
báo người dùng ngay. Chỉ dùng quy tắc, không gọi AI; bật bằng DOCUMENT_GATE_ENABLED (xem agent.process_message)."""
import logging
import re

from app.core.config import get_settings
from app.services import contract_review, prompts, query_filter

logger = logging.getLogger(__name__)

# Chỉ chấm tỷ lệ từ hợp lệ trên đoạn đầu: tài liệu dài không cần quét hết
VALID_RATIO_SAMPLE_CHARS = 5000

TOO_SHORT_REASON = "có quá ít chữ để rà soát"
UNREADABLE_REASON = "có chữ không đọc được, nghi do bản scan mờ hoặc nhận dạng chữ (OCR) bị lỗi"
RETRY_HINT = "Bạn hãy tải lên bản rõ hơn hoặc dán nội dung cần rà soát vào khung chat."

# Hỏi lại khi không nhận ra loại hợp đồng; danh sách ứng với CONTRACT_PATTERNS của contract_review (đổi bên đó thì đổi ở đây)
ASK_TYPE_ANSWER = (
    "Mình chưa nhận ra đây là loại hợp đồng gì. Bạn cho mình biết đây là hợp đồng nào nhé. Hiện mình rà soát được: "
    "chuyển nhượng quyền sử dụng đất, tặng cho đất, thế chấp, thuê đất hoặc nhà, mua bán nhà, mua bán hàng hoá, "
    "hợp đồng dịch vụ, góp vốn thành lập doanh nghiệp. Hoặc bạn hỏi cụ thể điều cần kiểm tra trong tài liệu, "
    "ví dụ: \"điều khoản phạt cọc có hợp lý không?\""
)

# Các mục thường gặp của hợp đồng, kiểm bằng từ khoá (facts với include_checklist). Mỗi mục: (tên hiển thị, các biểu thức tìm
# trên chữ đã bỏ dấu, chữ thường qua contract_review._fold). Danh sách CHUNG cho mọi loại hợp đồng, nhóm sẽ đối chiếu lại với
# quy định về nội dung hợp đồng; chưa có danh sách riêng từng loại (sau này thêm một dict loại hợp đồng -> danh sách cùng dạng).
# Lưu ý chữ đã bỏ dấu: "hợp đồng" thành "hop dong" nên "đồng" (tiền) chỉ tính khi đứng ngay sau số
CHECKLIST = [
    ("Thông tin các bên", [
        r"\bben [ab]\b", r"\bben (?:ban|mua|cho thue|thue|tang cho|nhan|gop von)\b",
    ]),
    ("Giá trị và phương thức thanh toán", [
        r"\bgia\b", r"gia tri", r"thanh toan", r"\d\s*dong\b", r"\bvnd\b", r"\btien\b",
    ]),
    ("Thời hạn, thời gian thực hiện hoặc hiệu lực", [
        r"thoi han", r"thoi gian thuc hien", r"hieu luc", r"ngay bat dau", r"ngay ket thuc",
    ]),
    ("Quyền và nghĩa vụ của các bên", [
        r"quyen va nghia vu", r"quyen, nghia vu", r"trach nhiem cua ben",
    ]),
    ("Trách nhiệm khi vi phạm", [r"vi pham", r"\bphat\b", r"boi thuong"]),
    ("Giải quyết tranh chấp", [r"tranh chap", r"toa an", r"trong tai", r"hoa giai"]),
    ("Chữ ký, đại diện các bên và ngày ký", [
        r"ky ten", r"dai dien", r"ho ten va chu ky", r"ngay[^\n]{0,20}thang[^\n]{0,20}nam",
    ]),
]


def check(documents: list[dict], request_id: str | None = None) -> str | None:
    """documents: như document_client.get_extractions trả về (mỗi phần tử có "text", "source_kind").
    Trả None khi mọi tài liệu đạt; ngược lại trả câu báo người dùng, nêu số thứ tự các tài liệu không đọc được."""
    settings = get_settings()
    problems: list[tuple[int, str]] = []
    for number, document in enumerate(documents, start=1):
        text = (document.get("text") or "").strip()
        ratio = query_filter.valid_token_ratio(text[:VALID_RATIO_SAMPLE_CHARS])
        if len(text) < settings.document_min_chars:
            reason, outcome = TOO_SHORT_REASON, "quá_ngắn"
        elif ratio < settings.document_min_valid_ratio:
            reason, outcome = UNREADABLE_REASON, "chữ_rác"
        else:
            reason, outcome = None, "đạt"
        logger.info(
            "agent kiểm tra tài liệu request_id=%s số=%d loại=%s ký_tự=%d tỷ_lệ_từ_hợp_lệ=%.2f kết_quả=%s",
            request_id, number, document.get("source_kind"), len(text), ratio, outcome,
        )
        if reason is not None:
            problems.append((number, reason))

    if not problems:
        return None
    if len(documents) == 1:
        return f"Mình chưa thể rà soát vì tài liệu bạn đính kèm {problems[0][1]}. {RETRY_HINT}"
    detail = "; ".join(f"tài liệu số {number} {reason}" for number, reason in problems)
    return f"Mình chưa thể rà soát vì {detail}. {RETRY_HINT}"


def facts(documents: list[dict], request_id: str | None = None, include_checklist: bool = False) -> list[str]:
    """Các câu dữ kiện hệ thống tự đếm được cho prompt rà soát (prompts.build_review_prompt), rỗng nếu không có gì đáng nói.
    Chỉ đếm bằng quy tắc, không chặn, không gọi AI; bật bằng DOCUMENT_FACTS_ENABLED (xem review_agent.review).
    include_checklist: thêm câu các mục thường gặp (CHECKLIST) chưa thấy từ khoá, bật bằng DOCUMENT_CHECKLIST_ENABLED."""
    settings = get_settings()
    sentences: list[str] = []
    for number, document in enumerate(documents, start=1):
        text = (document.get("text") or "").strip()
        name = "tài liệu" if len(documents) == 1 else f"tài liệu số {number}"
        placeholders = len(contract_review._PLACEHOLDER.findall(text))
        # Cùng công thức cắt với prompts.format_documents nên số ký tự nêu ở đây là số AI thật sự được đọc
        read, cut = prompts.clip_document(text, len(documents))
        logger.info(
            "agent dữ kiện tài liệu request_id=%s số=%d chỗ_trống=%d ký_tự_đọc=%d tổng=%d",
            request_id, number, placeholders, len(read), len(text),
        )
        is_blank_template = placeholders >= settings.document_placeholder_min
        if is_blank_template:
            sentences.append(f"{name.capitalize()} có {placeholders} chỗ để trống, nhiều khả năng là mẫu chưa điền.")
        if cut:
            sentences.append(
                f"Hệ thống chỉ đọc {len(read)} trên {len(text)} ký tự của {name}, phần còn lại không được xem xét."
            )
        if include_checklist:
            sentence = _checklist_sentence(read, name if len(documents) > 1 else None, is_blank_template, request_id, number)
            if sentence:
                sentences.append(sentence)
    return sentences


def _checklist_sentence(
    read: str, document_name: str | None, is_blank_template: bool, request_id: str | None, number: int,
) -> str | None:
    """Câu các mục CHECKLIST chưa thấy từ khoá trong phần chữ AI được đọc (`read`); None nếu mục nào cũng thấy.
    Mẫu chưa điền thì bỏ qua: mẫu trống thì mục nào cũng chưa điền, báo từng mục chỉ thêm nhiễu."""
    if is_blank_template:
        logger.info("agent kiểm mục hợp đồng request_id=%s số=%d bỏ_qua_mẫu_trống", request_id, number)
        return None
    folded = contract_review._fold(read)
    missing = [name for name, patterns in CHECKLIST if not any(re.search(p, folded) for p in patterns)]
    logger.info(
        "agent kiểm mục hợp đồng request_id=%s số=%d chưa_thấy=%d/%d %s",
        request_id, number, len(missing), len(CHECKLIST), "; ".join(missing),
    )
    if not missing:
        return None
    where = f"ở {document_name} " if document_name else ""
    return (
        f"Kiểm bằng từ khoá, {where}chưa thấy nhắc tới: {', '.join(missing)}. Đây là kiểm tra tự động có thể sót, "
        "hãy đọc lại tài liệu để xác nhận trước khi kết luận là thiếu."
    )


def ask_contract_type(contract_type: str, user_content: str, request_id: str | None = None) -> str | None:
    """Câu hỏi lại người dùng khi không nhận ra loại hợp đồng ("khac") mà câu hỏi chung chung; ngược lại None.
    Câu hỏi cụ thể thì không hỏi lại vì nó đã được đưa vào câu tìm luật (contract_review.review_queries).
    Bật bằng DOCUMENT_ASK_TYPE_ENABLED (xem review_agent.review)."""
    if contract_type != "khac" or contract_review._is_specific(user_content):
        return None
    logger.info("agent hỏi lại loại hợp đồng request_id=%s", request_id)
    return ASK_TYPE_ANSWER
