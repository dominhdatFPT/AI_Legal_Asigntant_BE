"""Chạy thử Gemini qua llm_client.

Cách chạy (ở thư mục gốc legal-assistant-ai, đã bật venv):
    python services/agent-service/scripts/try_gemini.py
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]  # services/agent-service
ROOT_DIR = SERVICE_DIR.parents[1]                  # legal-assistant-ai
sys.path.insert(0, str(SERVICE_DIR))
load_dotenv(ROOT_DIR / ".env")

from app.services import llm_client  # noqa: E402


async def main():
    answer, tokens = await llm_client.generate(
        system_prompt="Bạn là trợ lý pháp lý Việt Nam. Trả lời ngắn gọn.",
        history=[],
        user_content="Thủ tục sang tên sổ đỏ gồm những bước nào?",
    )
    print(answer)
    print(f"\n(tokens: {tokens})")


asyncio.run(main())
