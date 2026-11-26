# -*- coding: utf-8 -*-
"""
========================================================================================
BÀI TEST ĐÁNH GIÁ CHẤT LƯỢNG RAG (LLM-AS-A-JUDGE / DEEPEVAL) - HỆ THỐNG TRỢ LÝ PHÁP LÝ AI
========================================================================================
Kịch bản: Đóng vai người dân gặp vướng mắc thực tế về đất đai theo Luật Đất đai 2024.
Hệ thống: Legal Assistant AI (RAG Pipeline + Trích xuất căn cứ pháp lý).
Bộ công cụ đánh giá: DeepEval (Answer Relevancy, Faithfulness, GEval Legal Actionability).
"""

import sys
import os
import warnings
from pathlib import Path
from typing import List, Optional

# Tắt cảnh báo rác
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Nạp biến môi trường từ BE/.env nếu có
try:
    from dotenv import dotenv_values
    env_paths = [
        Path(__file__).parent / "BE" / ".env",
        Path(__file__).parent.parent / ".env",
        Path(__file__).parent / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            env_vars = dotenv_values(str(env_path))
            for k, v in env_vars.items():
                if v and k not in os.environ:
                    os.environ[k] = v
            break
except Exception:
    pass

import openai
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams as EvaluationParams
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval


# ======================================================================================
# 1. TÙY BIẾN LLM JUDGE (HỖ TRỢ OPENROUTER / OPENAI API VỚI JSON STRUCTURED OUTPUT)
# ======================================================================================
class OpenRouterJudgeLLM(DeepEvalBaseLLM):
    """
    Mô hình LLM Giám khảo (Judge) chạy qua OpenRouter / OpenAI API.
    Sử dụng model DeepSeek V4.1 Flash (deepseek/deepseek-v4.1-flash) để chấm điểm khách quan, tiết kiệm chi phí và chuẩn xác cao.
    """
    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Không tìm thấy OPENROUTER_API_KEY hoặc OPENAI_API_KEY trong môi trường hoặc BE/.env")
        
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key)
        super().__init__(self.model_name)

    def load_model(self):
        return self.client

    def generate(self, prompt: str, schema=None, **kwargs) -> str:
        messages = [{"role": "user", "content": prompt}]
        extra_body = {"reasoning": {"enabled": False}} if "deepseek" in self.model_name.lower() else None
        if schema:
            schema_json = schema.model_json_schema() if hasattr(schema, "model_json_schema") else str(schema)
            prompt_with_schema = f"{prompt}\n\nPlease respond strictly with a valid JSON object conforming to this schema:\n{schema_json}"
            messages = [{"role": "user", "content": prompt_with_schema}]
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                extra_body=extra_body,
            )
            raw = response.choices[0].message.content
            if hasattr(schema, "model_validate_json"):
                return schema.model_validate_json(raw)
            return raw

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.0,
            extra_body=extra_body,
        )
        return response.choices[0].message.content
        return response.choices[0].message.content

    async def a_generate(self, prompt: str, schema=None, **kwargs) -> str:
        return self.generate(prompt, schema=schema, **kwargs)

    def get_model_name(self) -> str:
        return self.model_name


# ======================================================================================
# 2. DỮ LIỆU ĐÓNG VAI NGƯỜI DÂN & TRÍCH ĐOẠN PHÁP LUẬT (RAG KNOWLEDGE BASE)
# ======================================================================================
CITIZEN_PERSONA = """
Họ tên: Bác Nguyễn Văn An (60 tuổi)
Địa chỉ: Xã Yên Bài, Huyện Ba Vì, TP. Hà Nội
Hoàn cảnh thực tế:
- Năm 2005, bác có mua lại bằng giấy viết tay (không công chứng, không chứng thực của xã)
  một thửa đất nông nghiệp trồng cây lâu năm diện tích 450m2 từ một người cùng làng.
- Từ năm 2005 đến nay gia đình bác canh tác ổn định, đóng thuế đất đầy đủ, ranh giới rõ ràng,
  không có tranh chấp với ai, đất phù hợp với quy hoạch sử dụng đất cấp huyện.
- Bác nghe tin Luật Đất đai 2024 có hiệu lực từ ngày 01/08/2024 có nhiều điểm mới về việc hợp thức hóa
  đất mua bán giấy viết tay trước 2008 nên rất băn khoăn muốn hỏi rõ quyền lợi của mình.
"""

CITIZEN_QUERY = (
    "Chào chuyên viên tư vấn pháp lý, tôi là Nguyễn Văn An ở Ba Vì, Hà Nội. "
    "Năm 2005 gia đình tôi có mua 450m2 đất nông nghiệp bằng giấy viết tay của người cùng làng, "
    "chỉ có chữ ký hai bên và người làm chứng chứ không công chứng hay qua xã. "
    "Từ đó đến nay nhà tôi nộp thuế đất đầy đủ, canh tác trồng cây ổn định, không ai tranh chấp gì "
    "và xã đã xác nhận đất phù hợp quy hoạch. "
    "Tôi xin hỏi theo Luật Đất đai 2024 mới nhất:\n"
    "1. Mảnh đất mua giấy tay năm 2005 của tôi có đủ điều kiện để được cấp Giấy chứng nhận quyền sử dụng đất (Sổ đỏ) lần đầu không?\n"
    "2. Tôi cần chuẩn bị những hồ sơ, giấy tờ gì, nộp ở cơ quan nào và gia đình tôi có bị xử phạt hành chính hay thu hồi đất không?"
)

RETRIEVAL_CONTEXT = [
    (
        "[1] Điều 138 Khoản 1 & Khoản 3 Luật Đất đai 2024 (Số 31/2024/QH15): Cấp Giấy chứng nhận quyền "
        "sử dụng đất, quyền sở hữu tài sản gắn liền với đất cho hộ gia đình, cá nhân đang sử dụng đất không có giấy tờ.\n"
        "- Hộ gia đình, cá nhân đang sử dụng đất từ ngày 15/10/1993 đến trước ngày 01/01/2008, nay được Ủy ban nhân dân "
        "cấp xã nơi có đất xác nhận là không có tranh chấp thì được công nhận quyền sử dụng đất.\n"
        "- Trường hợp thửa đất nông nghiệp đang sử dụng vào mục đích nông nghiệp phù hợp với quy hoạch sử dụng đất cấp huyện "
        "thì được cấp Giấy chứng nhận quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất theo hình thức Nhà nước giao "
        "đất không thu tiền sử dụng đất đối với diện tích trong hạn mức giao đất nông nghiệp."
    ),
    (
        "[2] Điều 254 Khoản 1 Luật Đất đai 2024 (Số 31/2024/QH15): Quy định chuyển tiếp về đăng ký đất đai, cấp Giấy chứng nhận.\n"
        "- Hộ gia đình, cá nhân đang sử dụng đất do nhận chuyển nhượng quyền sử dụng đất trước ngày 01 tháng 01 năm 2008 "
        "bằng giấy tờ viết tay mà chưa được cấp Giấy chứng nhận thì được thực hiện thủ tục đăng ký đất đai, cấp Giấy chứng nhận "
        "quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất lần đầu theo quy định của Luật này mà không phải làm thủ tục "
        "chuyển quyền sử dụng đất; cơ quan có thẩm quyền không được yêu cầu nộp hợp đồng chuyển nhượng có công chứng/chứng thực "
        "và không xử phạt hành vi chuyển quyền không đúng quy định."
    ),
    (
        "[3] Điều 21 và Điều 28 Nghị định số 101/2024/NĐ-CP của Chính phủ: Trình tự, thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu.\n"
        "- Nơi tiếp nhận hồ sơ: Bộ phận Một cửa theo quy định của UBND cấp tỉnh, hoặc Chi nhánh Văn phòng đăng ký đất đai cấp huyện nơi có đất, "
        "hoặc nộp trực tiếp tại UBND cấp xã nơi có đất nếu người sử dụng đất có nhu cầu.\n"
        "- Thành phần hồ sơ gồm: Đơn đăng ký đất đai, tài sản gắn liền với đất (Mẫu số 04/ĐK); Giấy tờ viết tay mua bán xác lập năm 2005; "
        "Chứng từ thực hiện nghĩa vụ tài chính hoặc biên lai nộp thuế đất qua các năm; Bản sao giấy tờ tùy thân (CCCD) và trích lục/sơ đồ hiện trạng thửa đất."
    )
]

AI_SYSTEM_RESPONSE = (
    "Thửa đất 450m2 mua bằng giấy viết tay năm 2005 của gia đình bác ĐỦ ĐIỀU KIỆN để được cấp Giấy chứng nhận quyền sử dụng đất (Sổ đỏ) lần đầu theo quy định của Luật Đất đai 2024 và không bị xử phạt hay thu hồi.\n\n"
    "Cụ thể căn cứ pháp luật và hướng dẫn thủ tục cho bác như sau:\n"
    "• Điều kiện công nhận quyền sử dụng đất: Bác mua đất bằng giấy viết tay trước ngày 01/01/2008, sử dụng đất nông nghiệp ổn định, phù hợp quy hoạch sử dụng đất cấp huyện và được UBND cấp xã xác nhận không có tranh chấp thì được cấp Giấy chứng nhận quyền sử dụng đất lần đầu (Điểm a Khoản 3 Điều 138 và Khoản 1 Điều 254 Luật Đất đai 2024).\n"
    "• Không phạt hành chính, không ép công chứng lại: Bác không bị xử phạt vi phạm hành chính đối với giao dịch giấy viết tay trước năm 2008, và cơ quan đăng ký đất đai không được yêu cầu hợp đồng công chứng chuyển nhượng (Khoản 1 Điều 254 Luật Đất đai 2024).\n"
    "• Nơi tiếp nhận hồ sơ: Bác có thể nộp tại Bộ phận Một cửa cấp huyện, Chi nhánh Văn phòng Đăng ký đất đai huyện Ba Vì, hoặc nộp trực tiếp tại UBND xã Yên Bài nếu bác muốn nộp gần nhà (Điều 21 Nghị định 101/2024/NĐ-CP).\n"
    "• Hồ sơ cần chuẩn bị: Đơn đăng ký đất đai theo Mẫu 04/ĐK, giấy tờ mua bán viết tay năm 2005, biên lai nộp thuế đất hàng năm, bản sao CCCD và sơ đồ hiện trạng thửa đất (Điều 28 Nghị định 101/2024/NĐ-CP).\n"
    "• Khuyến nghị: Bác liên hệ công chức địa chính xã Yên Bài để kiểm tra hồ sơ địa chính và xin xác nhận tình trạng không tranh chấp trước khi nộp để quy trình cấp sổ diễn ra thuận lợi."
)


def generate_live_chatbot_response(model_name: Optional[str] = None) -> str:
    """Gọi trực tiếp DeepSeek V4.1 Flash để sinh câu trả lời tư vấn cho người dân dựa trên RAG context."""
    model = model_name or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    
    context_text = "\n\n".join(RETRIEVAL_CONTEXT)
    sys_prompt = (
        "Bạn là Chuyên gia Cố vấn Pháp lý cho người dân Việt Nam. Trả lời bằng tiếng Việt hành chính - pháp lý chuẩn mực, đi thẳng vào trọng tâm.\n"
        "Cách trả lời:\n"
        "1. Câu đầu tiên trả lời thẳng câu hỏi (được hay không, cần gì...).\n"
        "2. Sau đó tối đa 3 đến 5 gạch đầu dòng ngắn, căn cứ trong ngoặc cuối dòng, ví dụ: (Khoản 1 Điều 188 Luật Đất đai 2024). Tổng dưới 200 chữ, không nhắc lại câu hỏi, không mở bài, kết bài, chào hỏi.\n"
        "3. Chỉ dùng thông tin trong các trích đoạn bên dưới, bỏ qua đoạn không liên quan, không bịa. Số Điều, Khoản, tên luật chỉ chép từ dòng tiêu đề [1], [2]... của trích đoạn.\n"
        "4. Tuyệt đối không dùng từ Hán-Việt dịch thô từ tiếng Trung hoặc tiếng Anh, dùng đúng thuật ngữ pháp lý Việt Nam.\n\n"
        "Trích đoạn pháp luật:\n" + context_text
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": CITIZEN_QUERY}
        ],
        temperature=0.2,
        max_tokens=1500,
        extra_body={"reasoning": {"enabled": False}} if "deepseek" in model.lower() else None
    )
    return resp.choices[0].message.content or ""


# ======================================================================================
# 3. HÀM CHẠY ĐÁNH GIÁ CHẤT LƯỢNG RAG BẰNG DEEPEVAL
# ======================================================================================
def run_rag_evaluation():
    """
    Thực hiện chấm điểm RAG bằng DeepEval với 3 tiêu chí:
    1. AnswerRelevancyMetric (Độ liên quan câu trả lời)
    2. FaithfulnessMetric (Tính trung thực / Không ảo giác căn cứ)
    3. GEval (Độ chính xác trích dẫn pháp lý & Tính thực tiễn cho người dân)
    """
    print("\n" + "=" * 80)
    print(" BÀI TEST CHẤM ĐIỂM CHẤT LƯỢNG RAG (LLM-AS-A-JUDGE / DEEPEVAL)")
    print(" LĨNH VỰC: TƯ VẤN PHÁP LUẬT ĐẤT ĐAI CHO NGƯỜI DÂN VIỆT NAM")
    print("=" * 80)
    
    print("\n[BƯỚC 1: ĐÓNG VAI NGƯỜI DÂN VÀ ĐẶT CÂU HỎI]")
    print(f"-> Người hỏi: Bác Nguyễn Văn An (Ba Vì, Hà Nội)")
    print(f"-> Hoàn cảnh: Mua 450m2 đất nông nghiệp bằng giấy viết tay năm 2005, canh tác ổn định, nộp thuế đầy đủ.")
    print(f"-> Nội dung câu hỏi:\n{CITIZEN_QUERY}")
    
    print("\n[BƯỚC 2: TRÍCH XUẤT CĂN CỨ PHÁP LÝ (RAG RETRIEVAL CONTEXT)]")
    for i, chunk in enumerate(RETRIEVAL_CONTEXT, 1):
        print(f"--- [Trích đoạn {i}] ---\n{chunk.strip()}\n")
        
    model_name = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")
    print(f"[BƯỚC 3: CÂU TRẢ LỜI CỦA TRỢ LÝ AI (DEEPSEEK V4.1 FLASH - LIVE GENERATION)]")
    try:
        actual_output = generate_live_chatbot_response(model_name=model_name)
    except Exception as e:
        print(f"-> Không thể gọi trực tiếp API ({e}), sử dụng câu trả lời mẫu:")
        actual_output = AI_SYSTEM_RESPONSE
    print(actual_output)
    print("\n" + "-" * 80)

    # Khởi tạo Judge LLM
    print(f"\n[BƯỚC 4: KHỞI TẠO LLM-AS-A-JUDGE ({model_name}) TRÊN DEEPEVAL]")
    judge = OpenRouterJudgeLLM(model_name=model_name)
    print(f"-> Model Giám khảo: {judge.get_model_name()}")

    test_case = LLMTestCase(
        input=CITIZEN_QUERY,
        actual_output=actual_output,
        retrieval_context=RETRIEVAL_CONTEXT
    )

    results = {}

    # 1. Answer Relevancy Metric
    print("\n-> Đang chấm Tiêu chí 1: Answer Relevancy (Độ liên quan & Trúng trọng tâm câu hỏi)...")
    relevancy_metric = AnswerRelevancyMetric(threshold=0.7, model=judge)
    relevancy_metric.measure(test_case)
    results["answer_relevancy"] = {
        "score": relevancy_metric.score,
        "success": relevancy_metric.is_successful(),
        "reason": relevancy_metric.reason
    }
    print(f"   Score: {relevancy_metric.score:.2f} | Đạt: {relevancy_metric.is_successful()}")
    print(f"   Nhận xét: {relevancy_metric.reason}")

    # 2. Faithfulness Metric
    print("\n-> Đang chấm Tiêu chí 2: Faithfulness (Tính trung thực & Không ảo giác ngoài trích đoạn luật)...")
    faithfulness_metric = FaithfulnessMetric(threshold=0.7, model=judge)
    faithfulness_metric.measure(test_case)
    results["faithfulness"] = {
        "score": faithfulness_metric.score,
        "success": faithfulness_metric.is_successful(),
        "reason": faithfulness_metric.reason
    }
    print(f"   Score: {faithfulness_metric.score:.2f} | Đạt: {faithfulness_metric.is_successful()}")
    print(f"   Nhận xét: {faithfulness_metric.reason}")

    # 3. Custom Legal GEval Metric
    print("\n-> Đang chấm Tiêu chí 3: GEval Legal Actionability & Citation Accuracy...")
    legal_geval = GEval(
        name="Legal Actionability & Citation Accuracy",
        criteria=(
            "Đánh giá chất lượng tư vấn pháp lý dành cho người dân theo các tiêu chí:\n"
            "1. Tính chính xác tuyệt đối của các điều khoản luật được trích dẫn (Điều, Khoản, Tên luật/Nghị định) so với văn bản gốc.\n"
            "2. Giải tỏa đúng nỗi lo lắng của người dân (có được cấp sổ không, có bị phạt tiền hay thu hồi không).\n"
            "3. Tính khả thi và rõ ràng của các bước hướng dẫn nộp hồ sơ (nơi nộp, giấy tờ cần chuẩn bị).\n"
            "4. Văn phong chuẩn mực, tôn trọng, dễ hiểu với người lớn tuổi ở nông thôn."
        ),
        evaluation_params=[EvaluationParams.INPUT, EvaluationParams.ACTUAL_OUTPUT, EvaluationParams.RETRIEVAL_CONTEXT],
        model=judge,
        threshold=0.7
    )
    legal_geval.measure(test_case)
    results["legal_geval"] = {
        "score": legal_geval.score,
        "success": legal_geval.is_successful(),
        "reason": legal_geval.reason
    }
    print(f"   Score: {legal_geval.score:.2f} | Đạt: {legal_geval.is_successful()}")
    print(f"   Nhận xét: {legal_geval.reason}")

    # Bảng tổng kết Scorecard
    print("\n" + "=" * 80)
    print(" BẢNG TỔNG KẾT ĐIỂM SỐ CHẤT LƯỢNG RAG (SCORECARD)")
    print("=" * 80)
    print(f"{'Tiêu chí đánh giá':<42} | {'Điểm (0.0 - 1.0)':<18} | {'Trạng thái':<10}")
    print("-" * 80)
    print(f"{'1. Answer Relevancy (Độ liên quan)':<42} | {results['answer_relevancy']['score']:<18.2f} | {'PASS' if results['answer_relevancy']['success'] else 'FAIL':<10}")
    print(f"{'2. Faithfulness (Không bịa đặt)':<42} | {results['faithfulness']['score']:<18.2f} | {'PASS' if results['faithfulness']['success'] else 'FAIL':<10}")
    print(f"{'3. Legal Actionability & Citation (Thực thi)':<42} | {results['legal_geval']['score']:<18.2f} | {'PASS' if results['legal_geval']['success'] else 'FAIL':<10}")
    print("-" * 80)
    
    avg_score = (results['answer_relevancy']['score'] + results['faithfulness']['score'] + results['legal_geval']['score']) / 3.0
    print(f"-> ĐIỂM TRUNG BÌNH TOÀN DIỆN: {avg_score:.2f} / 1.00 ({avg_score * 100:.1f}%)")
    print("=" * 80)

    return results


# Tích hợp Pytest
def test_citizen_land_law_rag_quality():
    """Hàm test chuẩn cho pytest"""
    results = run_rag_evaluation()
    assert results["answer_relevancy"]["score"] >= 0.7, "Answer Relevancy dưới ngưỡng yêu cầu!"
    assert results["faithfulness"]["score"] >= 0.7, "Faithfulness dưới ngưỡng yêu cầu!"
    assert results["legal_geval"]["score"] >= 0.7, "Legal Actionability GEval dưới ngưỡng yêu cầu!"


if __name__ == "__main__":
    try:
        run_rag_evaluation()
        sys.exit(0)
    except Exception as exc:
        print(f"\n[LỖI THỰC THI]: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
