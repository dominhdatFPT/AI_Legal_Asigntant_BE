from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Downstream services
    rag_service_url: str = "http://rag-service:8000"
    internal_token: str = ""
    rag_timeout_seconds: float = 30.0
    rag_batch_enabled: bool = True
    # Lấy chữ đã trích của tài liệu người dùng upload (gọi bằng token của chính người dùng)
    document_service_url: str = "http://document-service:8000"

    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Database
    agent_database_url: str = ""

    # Địa chỉ Redis dùng làm cache. Rỗng = không dùng Redis (cache chạy bằng bộ nhớ process như cũ)
    redis_url: str = ""
    # Nơi cất hai cache (tìm luật, lịch sử trò chuyện), chỉ có tác dụng khi cờ cache tương ứng bật: "redis" = cất ở Redis (dùng chung các
    # bản agent-service, còn sau khi restart), tự rơi về cache bộ nhớ khi Redis lỗi hoặc khi redis_url rỗng; "memory" = chỉ dùng
    # cache bộ nhớ của từng process. Giá trị khác coi như "memory" và ghi cảnh báo một lần
    cache_backend: str = "redis"
    # Thời gian chờ tối đa (giây) cho MỖI lần kết nối/đọc/ghi Redis. Gọi Redis là đồng bộ nên một lần gọi có thể chặn tới chừng này
    redis_timeout_seconds: float = 0.2
    # Ngắt mạch Redis: từng này lần lỗi LIÊN TIẾP thì bỏ qua Redis trong redis_breaker_seconds giây (cache dùng bộ nhớ process, không
    # chờ timeout); hết thời gian thì thử lại, thành công thì quay về Redis
    redis_breaker_failures: int = 3
    redis_breaker_seconds: float = 30.0
    # Tên khoá Redis giữ số phiên bản kho luật (rag-service tăng khi duyệt/thay luật xong, agent đọc để đưa vào khoá cache nên cache
    # cũ tự không được dùng nữa). PHẢI GIỐNG corpus_version_key của rag-service. Không dùng Redis thì số phiên bản luôn là "0"
    corpus_version_key: str = "legal:corpus_version"
    # Số giây nhớ số phiên bản trong process để mỗi câu hỏi không phải gọi Redis thêm một lần. Đổi luật xong có thể mất tối đa chừng
    # này giây mới có hiệu lực. 0 = không nhớ (mỗi lần tạo khoá cache đọc Redis một lần)
    corpus_version_cache_seconds: float = 5.0

    # LLM provider: "openrouter", "gemini" hoặc "groq"; danh sách ưu tiên ngăn bởi dấu phẩy.
    # Mặc định chỉ openrouter: tuyến duy nhất đã xác minh giá cho trần $0.01/câu (chạy chung compose/render)
    llm_provider: str = "openrouter"

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
    openrouter_model: str = "deepseek/deepseek-v4.1-flash"
    openrouter_site_url: str = ""
    openrouter_app_name: str = ""
    openrouter_timeout_seconds: float = 30.0
    # 0 = không thử lại ngầm (mỗi lần gửi thật phải có khoản giữ chỗ riêng trong CaseBudget); chỉ tăng khi không bật trần tiền
    openrouter_max_retries: int = 0
    # Token dành cho suy luận, tính vào max_tokens. 0 = gửi reasoning.effort=none ở cả generate và stream.
    # Số dương = gửi reasoning.max_tokens và cộng ngân sách suy luận vào giới hạn token đầu ra.
    openrouter_reasoning_max_tokens: int = 0
    # Lượt gọi phụ chỉ cần trả JSON ngắn (hiện là chấm độ liên quan) không cần suy luận: dùng mức riêng thay cho
    # openrouter_reasoning_max_tokens. 0 = tắt suy luận (chấm từ ~5s còn ~1s). Chỉ tác dụng với OpenRouter
    llm_aux_reasoning_max_tokens: int = 0
    # Model OpenRouter riêng cho lượt gọi PHỤ (chấm liên quan relevance.grade, viết lại câu hỏi query_rewrite.rewrite), rẻ/nhanh hơn
    # model trả lời chính. Rỗng = lượt phụ dùng openrouter_model như cũ. Chỉ tác dụng với provider openrouter (Gemini/Groq trực tiếp bỏ
    # qua). Câu trả lời chính, tóm tắt, rà soát tài liệu và stream không đổi model
    openrouter_aux_model: str = "deepseek/deepseek-v4.1-flash"
    # Model OpenRouter riêng cho biên bản hội thoại user - luật sư (minutes.py), ví dụ google/gemini-2.5-flash. Rỗng = dùng openrouter_model.
    # Nên đặt model không bắt buộc suy luận: model free hay bắt buộc suy luận, mất 1-2 phút/lần và có lần hết token chưa ra JSON.
    minutes_model: str = ""

    # Câu trả lời mong muốn ~150-200 chữ; giới hạn này là chốt chặn khi model vẫn muốn viết dài
    environment: str = "development"
    llm_max_output_tokens: int = 800
    daily_token_limit: int = 20000
    public_chat_daily_token_limit: int = 8000
    daily_token_quota_enabled: bool = True
    # Mặc định tắt (fail-closed): chỉ tin CF-Connecting-IP khi lưu lượng thực sự đi qua Cloudflare Proxy.
    trust_cloudflare_headers: bool = False
    # Nhịp gửi sự kiện SSE `status` giữ sống kết nối trong lúc responder đệm câu trả lời hồ sơ phức tạp (giây).
    sse_heartbeat_seconds: float = Field(default=10.0, gt=0)
    # Trần chi phí (micro-USD) cho API nội bộ trích biên bản tư vấn user-luật sư (MAX_OUTPUT_TOKENS=16000; 30000 = $0.03, 0 = tắt).
    minutes_cost_budget_micro_usd: int = Field(default=30000, ge=0)
    # Mặc định rỗng (fail-closed): chỉ miễn trừ hạn mức khi operator chủ động khai báo qua env TEST_UNLIMITED_USERS.
    # Trên production chỉ so khớp user_id (UUID trong JWT sub), không so khớp username.
    test_unlimited_users: str = ""
    # Chế độ rà soát tài liệu đính kèm: câu trả lời dài hơn (~500 chữ) nên cần thêm token và thời gian chờ.
    # Không thử lại cùng provider (xem llm_client.generate), chỉ chuyển sang provider kế tiếp.
    llm_review_max_output_tokens: int = 2000
    llm_review_timeout_seconds: float = 40.0
    # Ngân sách tìm kiếm thông thường và fallback khi không chấm được liên quan.
    # Profile ARCH_PERF triển khai: RAG_TOP_K=5, RAG_CANDIDATE_K=12,
    # RAG_SELECT_MAX=5, RELEVANCE_CHECK=true, RELEVANCE_CHUNK_CHARS=800.
    rag_top_k: int = Field(default=3, ge=1, le=20)
    # Tìm 2 bước: số đoạn lấy về làm ỨNG VIÊN cho bước chấm độ liên quan (nhiều hơn rag_top_k vì đoạn đúng hay nằm sau hạng top_k),
    # rồi AI chấm chọn lại. Chỉ có tác dụng khi relevance_check bật, llm_bypass tắt và giá trị này lớn hơn rag_top_k. 0 = tắt.
    # Tối đa 20 (rag-service từ chối lớn hơn). Không thêm lượt gọi AI: vẫn một lượt chấm, chỉ nhìn nhiều ứng viên hơn
    rag_candidate_k: int = 0
    # Khi tìm 2 bước: số đoạn tối đa giữ lại sau khi chấm (theo thứ tự tìm kiếm) để đưa vào prompt trả lời
    rag_select_max: int = 5
    adaptive_rag_enabled: bool = True
    complex_query_chars: int = Field(default=1200, ge=1)
    case_max_issues: int = Field(default=8, ge=1, le=12)
    case_max_retrieval_queries: int = Field(default=8, ge=1, le=12)
    case_retrieval_concurrency: int = Field(default=3, ge=1, le=12)
    case_candidate_k_per_issue: int = Field(default=12, ge=1, le=20)
    case_max_candidates: int = Field(default=48, ge=1)
    case_select_max: int = Field(default=16, ge=1)
    # Trần TOKEN THẬT (không phải byte) của phần chứng cứ trong prompt responder
    case_context_token_budget: int = Field(default=6000, ge=1, le=40000)
    planner_timeout_seconds: float = Field(default=20.0, gt=0)
    planner_max_output_tokens: int = Field(default=896, ge=1)
    case_max_output_tokens: int = Field(default=3500, ge=1)
    case_complex_max_output_tokens: int = Field(default=3500, ge=0)
    case_token_budget_target: int = Field(default=0, ge=0)
    case_token_budget_max: int = Field(default=0, ge=0)
    # Trần tiền CỨNG cho toàn một câu hỏi (LLM + embedding), đơn vị micro-USD: 10000 = $0.01 = 10_000_000 nano-USD.
    # Áp cho mọi nhánh và mọi người dùng (kể cả TEST_UNLIMITED_USERS, vốn chỉ miễn hạn mức token ngày). 0 = tắt (chỉ test)
    case_cost_budget_micro_usd: int = Field(default=10000, ge=0)
    # Embedding online của rag-service: tổng token tối đa mỗi câu hỏi và đơn giá nano-USD/token (Gemini Embedding $0.15/1M)
    embedding_total_input_tokens: int = Field(default=1024, ge=0)
    embedding_price_nano_per_token: int = Field(default=150, ge=0)
    # Mục tiêu input của responder (system + câu hỏi + lịch sử + chứng cứ) và trần tổng của bước chấm liên quan (cả hai vòng)
    responder_input_target: int = Field(default=8000, ge=1)
    relevance_total_input_tokens: int = Field(default=3600, ge=0)
    relevance_total_output_tokens: int = Field(default=320, ge=0)
    # Cổng admission cục bộ (không RAG/embedding/LLM): độ dài tối đa, số nhóm vấn đề tối đa của hồ sơ phức tạp
    admission_max_question_chars: int = Field(default=20000, ge=1)
    admission_max_issue_groups: int = Field(default=8, ge=1)
    admission_gate_enabled: bool = True
    case_relevance_dedup_enabled: bool = True
    case_max_retrieval_rounds: int = Field(default=2, ge=1, le=2)
    case_total_deadline_seconds: float = Field(default=90, gt=0)
    # Tự động xóa hội thoại (và toàn bộ tin nhắn ON DELETE CASCADE) khỏi DB sau N ngày không có tin nhắn mới (updated_at).
    # 0 = tắt tự động xóa. Chu kỳ quét nền mặc định 900 giây (15 phút), xóa theo lô ngắn để tránh khóa bảng lâu.
    conversation_retention_days: int = Field(default=3, ge=0)
    conversation_cleanup_interval_seconds: int = Field(default=900, ge=0)
    conversation_cleanup_batch_size: int = Field(default=200, ge=1, le=2000)
    # Số tin nhắn gần nhất của mỗi hội thoại mà AI nhớ (mỗi lượt hỏi-đáp là 2 tin). MỘT nguồn duy nhất: nơi đọc Supabase trong
    # conversations._load_for_chat và nơi cắt trong cache lịch sử (history_cache.append) cùng dùng giá trị này nên không bao giờ lệch nhau
    history_max_messages: int = Field(default=12, ge=1)
    # Cache lịch sử trò chuyện (history_max_messages tin gần nhất của mỗi hội thoại, app/services/history_cache.py): Supabase VẪN là nguồn
    # gốc duy nhất, cache chỉ là bản sao để mỗi lượt chat khỏi đọc bảng messages. Khoá gồm mã người dùng + mã hội thoại, vùng "history"
    # của cache.make_cache (nơi cất theo cache_backend). Ghi xuyên: chỉ cập nhật cache SAU KHI Supabase ghi xong; xoá hội thoại thì
    # xoá khoá. Tắt (false) thì chạy y như trước khi có cache lịch sử. ttl là giới hạn cuối cùng nếu lỡ có sai lệch; max_items chỉ
    # giới hạn bản dự phòng trong bộ nhớ
    history_cache_enabled: bool = True
    history_cache_ttl_seconds: int = 3600
    history_cache_max_items: int = 500
    # Kiểm tra mẫu đơn bằng AI (app/services/form_check.py): lượt/ngày/người dùng, riêng với hạn mức chatbot (0 = không giới hạn);
    # kết quả cache theo từng ô (vùng "formcheck"), chỉ ô chưa có trong cache mới tốn lượt
    form_check_daily_limit: int = 30
    form_check_cache_ttl_seconds: int = 604800
    form_check_cache_max_items: int = 2000
    # Cache kết quả tìm luật (rag_client.search, app/services/cache.py:make_cache, vùng "rag"): cùng câu tìm (chuẩn hoá chữ thường,
    # gộp khoảng trắng, bỏ ? . ! ở cuối), cùng top_k và cùng số phiên bản kho luật thì lấy lại, khỏi tốn một lượt embedding Gemini ở
    # rag-service và ~0.7s. BẬT mặc định.
    # Nơi cất theo cache_backend: Redis thì dùng chung các bản và CÒN SAU KHI RESTART; bộ nhớ process (memory, hoặc dự phòng khi
    # Redis lỗi) thì riêng từng worker và mất khi restart. Nạp/duyệt/thay luật xong thì rag-service tăng số phiên bản trong Redis nên
    # cache cũ tự không được dùng nữa (cần Redis chạy lúc đó; với cache_backend=memory số phiên bản luôn "0" nên vẫn phải restart).
    # Redis chết đúng lúc đổi luật thì cache có thể còn bản cũ tới hết rag_cache_ttl_seconds (xoá tay khoá legal:rag:* nếu cần).
    # max_items chỉ giới hạn bản dự phòng trong bộ nhớ; Redis tự giới hạn bằng maxmemory. <= 0 ở ttl hoặc max_items thì không lưu gì
    rag_cache_enabled: bool = True
    rag_cache_ttl_seconds: int = 86400
    rag_cache_max_items: int = 1000
    # CHỈ ĐỂ TEST, không bật ở môi trường thật: bỏ bước LLM, trả thẳng các đoạn luật rag tìm được
    llm_bypass: bool = False
    # Chấm độ liên quan (qa_agent): nhờ LLM chọn đoạn luật rag tìm được thật sự dùng trả lời câu hỏi, không có đoạn nào thì
    # trả câu "ngoài phạm vi" thay vì để LLM tự suy diễn. Mỗi câu hỏi thêm một lượt gọi LLM ngắn nên tắt mặc định;
    # không áp dụng cho rà soát tài liệu. Lỗi hoặc quá giờ thì bỏ qua bước chấm (giữ nguyên các đoạn)
    relevance_check: bool = True
    relevance_timeout_seconds: float = 8.0
    # Kích thước cửa sổ xem trước theo từ khóa của câu hỏi (prompts.build_relevance_prompt).
    # Chỉ cắt bản xem trước để chấm; prompt trả lời vẫn nhận nguyên đoạn đã chọn. <= 0 thì dùng 800.
    relevance_chunk_chars: int = 800
    # Viết lại câu hỏi thành một câu tìm kiếm dùng thuật ngữ luật, tự đứng được một mình, trước khi tìm trong rag-service
    # (query_rewrite.rewrite, nhờ LLM): người dân hỏi đời thường và hay hỏi nối tiếp nên tìm trực tiếp ra Điều lệch.
    # Mỗi câu hỏi thêm một lượt gọi LLM ngắn nên tắt mặc định. Lỗi hoặc quá giờ thì dùng câu gốc.
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

    @model_validator(mode="after")
    def validate_case_budget(self) -> "Settings":
        if (self.case_token_budget_target > 0 and self.case_token_budget_max > 0
                and self.case_token_budget_target > self.case_token_budget_max):
            raise ValueError("case_token_budget_target must not exceed case_token_budget_max")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
