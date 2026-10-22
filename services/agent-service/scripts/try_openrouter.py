"""Chạy thử OpenRouter qua llm_client (chỉ OpenRouter, không chuyển sang Gemini). Không in khóa.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã cài requirements của agent-service):
    python services/agent-service/scripts/try_openrouter.py
    python services/agent-service/scripts/try_openrouter.py "Câu hỏi khác"
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]  # services/agent-service
ROOT_DIR = SERVICE_DIR.parents[1]                  # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")
os.environ["LLM_PROVIDER"] = "openrouter"

from app.core.config import get_settings  # noqa: E402
from app.services import llm_client  # noqa: E402


async def main():
    s = get_settings()
    print(f"Base URL: {s.openrouter_base_url}")
    print(f"Model:    {s.openrouter_model or '(chưa đặt OPENROUTER_MODEL)'}")
    print(f"API key:  {'đã có' if s.openrouter_api_key else '(chưa đặt OPENROUTER_API_KEY)'}\n")
    question = sys.argv[1] if len(sys.argv) > 1 else "Thủ tục sang tên sổ đỏ gồm những bước nào?"
    try:
        answer, tokens, model = await llm_client.generate(
            system_prompt="Bạn là trợ lý pháp lý Việt Nam. Trả lời ngắn gọn.",
            history=[],
            user_content=question,
        )
    except llm_client.LLMError as e:
        print(f"THẤT BẠI: {e}")
        sys.exit(1)
    print(answer)
    print(f"\n(model: {model}, tokens: {tokens})")


asyncio.run(main())
