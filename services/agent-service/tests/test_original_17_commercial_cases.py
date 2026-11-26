import pytest
from app.services import prompts, quota
from app.core.config import get_settings

ORIGINAL_17_QUESTIONS = [
    {"id": 1, "q": "Quan hệ pháp luật tranh chấp giữa An Phát, Minh Long và Đông Á là gì?"},
    {"id": 2, "q": "Tư cách tố tụng của từng chủ thể và việc có cần đưa Minh Long vào tham gia tố tụng hay không?"},
    {"id": 3, "q": "Thẩm quyền giải quyết tranh chấp của Tòa án và Trọng tài; hiệu lực và phạm vi của điều khoản trọng tài?"},
    {"id": 4, "q": "Việc Đông Á nhận chuyển nhượng khoản phải thu có làm thay đổi điều khoản trọng tài hoặc quyền, nghĩa vụ tố tụng của các bên hay không?"},
    {"id": 5, "q": "Tính hợp lệ của hợp đồng được ký bằng chữ ký điện tử, việc người ký là nhân viên đã nghỉ việc và giá trị của nghị quyết ủy quyền?"},
    {"id": 6, "q": "An Phát có được yêu cầu: Hủy bỏ hoặc chấm dứt hợp đồng; Hoàn trả 20 tỷ đồng; Phạt vi phạm; Bồi thường 12 tỷ đồng; Khấu trừ hoặc từ chối thanh toán 10 tỷ đồng cho Đông Á hay không?"},
    {"id": 7, "q": "Việc chuyển nhượng khoản phải thu có hợp pháp không; Đông Á có quyền khởi kiện độc lập không; An Phát có thể viện dẫn các phản đối đối với Minh Long để chống lại Đông Á đến mức nào?"},
    {"id": 8, "q": "Thời hiệu khởi kiện, thời điểm bắt đầu tính thời hiệu và việc email thừa nhận nghĩa vụ, sửa chữa và giám định có ảnh hưởng đến thời hiệu hay không?"},
    {"id": 9, "q": "Giá trị chứng minh của Email, Dữ liệu hệ thống quản lý hợp đồng, Kết luận giám định tự thuê, Tài liệu kỹ thuật, Lời khai nhân viên, Nghị quyết ủy quyền?"},
    {"id": 10, "q": "An Phát có thể yêu cầu giám định lại hoặc giám định bổ sung không; trách nhiệm tạm ứng và phân bổ chi phí giám định như thế nào?"},
    {"id": 11, "q": "Điều kiện áp dụng biện pháp khẩn cấp tạm thời phong tỏa tài khoản của Đông Á; rủi ro pháp lý nếu yêu cầu này bị coi là không có căn cứ?"},
    {"id": 12, "q": "Khả năng nhập các yêu cầu đối với Minh Long vào vụ án do Đông Á khởi kiện; trường hợp nào được coi là yêu cầu phản tố, yêu cầu độc lập?"},
    {"id": 13, "q": "Cách xác định thiệt hại thực tế, lợi nhuận bị mất, thiệt hại do ngừng sản xuất và thiệt hại do bồi thường cho khách hàng?"},
    {"id": 14, "q": "Trách nhiệm chứng minh đối với tỷ lệ lỗi 70% của Minh Long và 30% của An Phát?"},
    {"id": 15, "q": "Các phương án tố tụng và chiến lược giải quyết tối ưu cho An Phát, Minh Long và Đông Á?"},
    {"id": 16, "q": "Đề xuất luật sư cho An Phát: yêu cầu trong bản tự bảo vệ, tài liệu chứng cứ cần thu thập, đề nghị tố tụng và phương án thương lượng dự phòng?"},
    {"id": 17, "q": "Đánh giá khả năng An Phát thắng toàn bộ, thắng một phần, bị buộc thanh toán cho Đông Á hoặc đồng thời được yêu cầu Minh Long bồi thường?"},
]


def test_original_17_questions_count():
    assert len(ORIGINAL_17_QUESTIONS) == 17


@pytest.mark.parametrize("item", ORIGINAL_17_QUESTIONS)
def test_out_of_scope_does_not_charge_quota(item, monkeypatch):
    """Đảm bảo tất cả 17 câu hỏi khi rơi vào out_of_scope đều được miễn trừ, không bị trừ quota ngày."""
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.values = {}
        def get(self, key):
            return self.values.get(key)
        def eval(self, script, numkeys, key, delta, ttl):
            self.calls.append((key, delta))
            self.values[key] = self.values.get(key, 0) + delta
            return self.values[key]

    redis = FakeRedis()
    monkeypatch.setattr(quota.cache, "_get_client", lambda: redis)
    monkeypatch.setattr(quota.cache, "_use_redis", lambda settings: True)
    
    settings = get_settings()
    settings.daily_token_quota_enabled = True

    # 1. Kiểm tra helper is_exempt
    assert quota.is_exempt({"type": "out_of_scope", "tokens_used": 6575}, prompts.OUT_OF_SCOPE_ANSWER) is True

    # 2. Gọi add_daily_tokens với kết quả out_of_scope: không ghi nhận vào Redis
    quota.add_daily_tokens(
        user_id="user-test-123",
        tokens_used=6575,
        target_date="2026-11-17",
        prompt=item["q"],
        completion=prompts.OUT_OF_SCOPE_ANSWER,
        metadata={"type": "out_of_scope", "tokens_used": 6575}
    )
    assert not redis.calls
