from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Downstream services
    rag_service_url: str = "http://rag-service:8000"
    internal_token: str = ""
    rag_timeout_seconds: float = 30.0
    # Lấy chữ đã trích của tài liệu người dùng upload (gọi bằng token của chính người dùng)
    document_service_url: str = "http://document-service:8000"

    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Database
    agent_database_url: str = ""

    # LLM provider: "openrouter", "gemini" hoặc "groq"; danh sách ưu tiên ngăn bởi dấu phẩy
    llm_provider: str = "openrouter,gemini"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_timeout_seconds: float = 45.0
    gemini_max_retries: int = 3

    # Groq
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    groq_timeout_seconds: float = 15.0
    groq_max_retries: int = 1

    # OpenRouter (API tương thích OpenAI). SITE_URL/APP_NAME là tuỳ chọn, chỉ gửi header khi có giá trị
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""
    openrouter_site_url: str = ""
    openrouter_app_name: str = ""
    openrouter_timeout_seconds: float = 30.0
    openrouter_max_retries: int = 1
    # Token chừa cho phần "suy nghĩ" của câu trả lời chính (tính vào max_tokens). Mặc định 0 = không gửi tham số reasoning, hợp với
    # model không bắt buộc suy nghĩ như google/gemini-2.5-flash (đo: mỗi câu nhanh hơn 3-5s, ít token hơn 20-45%). Model suy luận
    # BẮT BUỘC (vd recursive/flint-v1.0:free) phải đặt lại khoảng 1024: để 0 thì câu trả lời có thể bị cắt hoặc rỗng
    openrouter_reasoning_max_tokens: int = 0
    # Lượt gọi phụ chỉ cần trả JSON ngắn (hiện là chấm độ liên quan) không cần suy luận: dùng mức riêng thay cho
    # openrouter_reasoning_max_tokens. 0 = tắt suy luận (chấm từ ~5s còn ~1s). Chỉ tác dụng với OpenRouter
    llm_aux_reasoning_max_tokens: int = 0
    # Model OpenRouter riêng cho lượt gọi PHỤ (chấm liên quan relevance.grade, viết lại câu hỏi query_rewrite.rewrite), rẻ/nhanh hơn
    # model trả lời chính. Rỗng = lượt phụ dùng openrouter_model như cũ. Chỉ tác dụng với provider openrouter (Gemini/Groq trực tiếp bỏ
    # qua). Câu trả lời chính, tóm tắt, rà soát tài liệu và stream không đổi model
    openrouter_aux_model: str = ""
    # Model OpenRouter riêng cho biên bản hội thoại user - luật sư (minutes.py), ví dụ google/gemini-2.5-flash. Rỗng = dùng openrouter_model.
    # Nên đặt model không bắt buộc suy luận: model free hay bắt buộc suy luận, mất 1-2 phút/lần và có lần hết token chưa ra JSON.
    minutes_model: str = ""

    # Câu trả lời mong muốn ~150-200 chữ; giới hạn này là chốt chặn khi model vẫn muốn viết dài
    llm_max_output_tokens: int = 800
    # Chế độ rà soát tài liệu đính kèm: câu trả lời dài hơn (~500 chữ) nên cần thêm token và thời gian chờ.
    # Không thử lại cùng provider (xem llm_client.generate), chỉ chuyển sang provider kế tiếp.
    llm_review_max_output_tokens: int = 2000
    llm_review_timeout_seconds: float = 40.0
    # Số đoạn luật lấy từ rag-service đưa vào prompt
    rag_top_k: int = 3
    # Tìm 2 bước: số đoạn lấy về làm ỨNG VIÊN cho bước chấm độ liên quan (nhiều hơn rag_top_k vì đoạn đúng hay nằm sau hạng top_k),
    # rồi AI chấm chọn lại. Chỉ có tác dụng khi relevance_check bật, llm_bypass tắt và giá trị này lớn hơn rag_top_k. 0 = tắt.
    # Tối đa 20 (rag-service từ chối lớn hơn). Không thêm lượt gọi AI: vẫn một lượt chấm, chỉ nhìn nhiều ứng viên hơn
    rag_candidate_k: int = 0
    # Khi tìm 2 bước: số đoạn tối đa giữ lại sau khi chấm (theo thứ tự tìm kiếm) để đưa vào prompt trả lời
    rag_select_max: int = 5
    # Cache kết quả tìm luật trong bộ nhớ (rag_client.search, app/services/cache.py): cùng câu tìm (chuẩn hoá chữ thường, gộp khoảng
    # trắng, bỏ ? . ! ở cuối) và cùng top_k thì lấy lại, khỏi tốn một lượt embedding Gemini ở rag-service và ~0.7s. Tắt mặc định.
    # Cache theo từng process (mỗi worker một cái), mất khi restart; nạp luật mới xong phải restart agent-service để xoá cache,
    # không thì tới hết rag_cache_ttl_seconds vẫn trả đoạn cũ. <= 0 ở ttl hoặc max_items thì không lưu gì
    rag_cache_enabled: bool = False
    rag_cache_ttl_seconds: int = 86400
    rag_cache_max_items: int = 1000
    # Cache câu trả lời cuối (qa_agent.prepare, responder.LLMCall.result): CHỈ câu đầu hội thoại (lịch sử rỗng), không kèm tài liệu,
    # LLM_BYPASS tắt, và chỉ khớp khi giống hệt sau chuẩn hoá (chữ thường, gộp khoảng trắng, bỏ ? . ! ở cuối) cùng llm_provider và
    # openrouter_model. Trúng thì không gọi rag và AI. Tắt mặc định. Cache theo từng process, mất khi restart; đổi prompt hoặc model
    # (ngoài openrouter_model) thì restart để xoá. <= 0 ở ttl hoặc max_items thì không lưu gì
    answer_cache_enabled: bool = False
    answer_cache_ttl_seconds: int = 21600
    answer_cache_max_items: int = 500
    # CHỈ ĐỂ TEST, không bật ở môi trường thật: bỏ bước LLM, trả thẳng các đoạn luật rag tìm được
    llm_bypass: bool = False
    # Chấm độ liên quan (qa_agent): nhờ LLM chọn đoạn luật rag tìm được thật sự dùng trả lời câu hỏi, không có đoạn nào thì
    # trả câu "ngoài phạm vi" thay vì để LLM tự suy diễn. Mỗi câu hỏi thêm một lượt gọi LLM ngắn nên tắt mặc định;
    # không áp dụng cho rà soát tài liệu. Lỗi hoặc quá giờ thì bỏ qua bước chấm (giữ nguyên các đoạn)
    relevance_check: bool = False
    relevance_timeout_seconds: float = 8.0
    # Số ký tự đầu của MỖI đoạn luật mà bước chấm liên quan được đọc (prompts.build_relevance_prompt). Đoạn luật dài tới ~2500 ký tự
    # nên phần quyết định có thể nằm ngoài phần đầu; tăng để đo, đổi lại prompt chấm dài và chậm hơn. <= 0 thì dùng 600
    relevance_chunk_chars: int = 600
    # Viết lại câu hỏi thành một câu tìm kiếm dùng thuật ngữ luật, tự đứng được một mình, trước khi tìm trong rag-service
    # (query_rewrite.rewrite, nhờ LLM): người dân hỏi đời thường và hay hỏi nối tiếp nên tìm trực tiếp ra Điều lệch.
    # Mỗi câu hỏi thêm một lượt gọi LLM ngắn nên tắt mặc định. Lỗi hoặc quá giờ thì dùng câu gốc. Hiện mới có module, chưa nối vào luồng
    query_rewrite_enabled: bool = False
    query_rewrite_timeout_seconds: float = 5.0
    # Cổng kiểm tra tài liệu đính kèm (document_gate): tài liệu quá ít chữ hoặc chữ rác (OCR hỏng) thì trả lời ngay, không tốn
    # lượt tìm luật và gọi AI. Kiểm tra bằng quy tắc, không gọi AI; bỏ qua khi llm_bypass bật. min_chars = số ký tự tối thiểu
    # (sau khi bỏ khoảng trắng đầu cuối), min_valid_ratio = tỷ lệ tối thiểu các từ là âm tiết tiếng Việt hợp lệ
    document_gate_enabled: bool = False
    document_min_chars: int = 200
    document_min_valid_ratio: float = 0.5
    # Dữ kiện tự kiểm tra cho prompt rà soát (document_gate.facts): hệ thống tự đếm chỗ trống ("......") và xem tài liệu có bị cắt
    # vì quá dài không, rồi đưa vào prompt để AI nêu lại thay vì tự đoán. Không chặn, không hỏi lại. Lỗi thì rà soát như cũ.
    # placeholder_min = từ chừng này chỗ trống trở lên thì coi là mẫu chưa điền
    document_facts_enabled: bool = False
    document_placeholder_min: int = 5
    # Không nhận ra loại hợp đồng (contract_type = "khac") mà câu hỏi lại chung chung ("kiểm tra giúp tôi") thì hỏi lại người dùng
    # loại hợp đồng hoặc điều cần kiểm tra, không tìm luật và không gọi AI (document_gate.ask_contract_type). Câu hỏi cụ thể
    # hoặc loại nhận ra được thì rà soát như cũ. Bỏ qua khi llm_bypass bật; lỗi thì rà soát như cũ
    document_ask_type_enabled: bool = False
    # Danh sách mục thường gặp của hợp đồng (bên, giá, thời hạn, quyền nghĩa vụ, vi phạm, tranh chấp, chữ ký): hệ thống kiểm
    # bằng từ khoá xem tài liệu có nhắc tới không (document_gate.facts) rồi đưa vào prompt rà soát như dữ kiện, chỉ báo "chưa thấy
    # từ khoá", không khẳng định "thiếu". Bật cờ này thì facts chạy dù document_facts_enabled tắt. Lỗi thì rà soát như cũ
    document_checklist_enabled: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
