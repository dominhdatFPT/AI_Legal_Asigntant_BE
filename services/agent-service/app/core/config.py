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
    # Model suy luận (vd recursive/flint-v1.0:free) bắt buộc "suy nghĩ" trước khi trả lời và tính vào max_tokens:
    # chừa thêm chừng này token cho phần suy nghĩ, nếu không câu trả lời bị cắt/rỗng. 0 = không gửi tham số reasoning
    openrouter_reasoning_max_tokens: int = 1024

    # Câu trả lời mong muốn ~150-200 chữ; giới hạn này là chốt chặn khi model vẫn muốn viết dài
    llm_max_output_tokens: int = 800
    # Chế độ rà soát tài liệu đính kèm: câu trả lời dài hơn (~500 chữ) nên cần thêm token và thời gian chờ.
    # Không thử lại cùng provider (xem llm_client.generate), chỉ chuyển sang provider kế tiếp.
    llm_review_max_output_tokens: int = 2000
    llm_review_timeout_seconds: float = 40.0
    # Số đoạn luật lấy từ rag-service đưa vào prompt
    rag_top_k: int = 4
    # CHỈ ĐỂ TEST, không bật ở môi trường thật: bỏ bước LLM, trả thẳng các đoạn luật rag tìm được
    llm_bypass: bool = False
    # Chấm độ liên quan (qa_agent): nhờ LLM chọn đoạn luật rag tìm được thật sự dùng trả lời câu hỏi, không có đoạn nào thì
    # trả câu "ngoài phạm vi" thay vì để LLM tự suy diễn. Mỗi câu hỏi thêm một lượt gọi LLM ngắn nên tắt mặc định;
    # không áp dụng cho rà soát tài liệu. Lỗi hoặc quá giờ thì bỏ qua bước chấm (giữ nguyên các đoạn)
    relevance_check: bool = False
    relevance_timeout_seconds: float = 8.0

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
