# Token optimization và quota công bằng

## Quyết định kiến trúc

Chi phí provider và quota người dùng là hai khái niệm độc lập. `metadata.tokens_used`
giữ số provider trả về, kể cả 6.575 token ở lượt bị từ chối. Khi server trả
`metadata.type == "out_of_scope"` hoặc nội dung đúng bằng `OUT_OF_SCOPE_ANSWER`
(bỏ khoảng trắng đầu/cuối), lượng cộng vào Redis bằng **0**. Không sửa usage thành
0, không hoàn quota bằng phép trừ có thể gây đua dữ liệu, không gọi luật sư tự động.

`quota.is_exempt` là chính sách dùng chung. `add_daily_tokens` kiểm tra chính sách
trước cả bước ước lượng token khi provider không trả usage. Tin người dùng chứa
câu fallback hoặc cờ `quota_exempt` đơn lẻ không đủ điều kiện miễn.

API lưu thêm `quota_exempt=true`, `quota_exemption_reason="out_of_scope"` trên
câu trả lời được miễn. Không đổi schema database: đây là metadata đã có sẵn.
Câu trả lời bình thường giữ cấu trúc metadata cũ.

| Đường đi | Nơi ghi nhận | Kết quả không có căn cứ |
| --- | --- | --- |
| POST messages | Sau xử lý agent | Không tăng Redis |
| SSE từ kết quả đã chuẩn bị | `finally` của generator | Không tăng Redis |
| SSE từ LLM | `finally` của generator | Kiểm tra kết quả và nội dung đã ghép |
| SSE rà soát tài liệu | Cùng `finally` | Cùng chính sách |
| SSE ngắt/lỗi giữa chừng | `finally`, ước lượng khi thiếu usage | Giữ cách tính hiện có nếu chưa xác định được miễn |

Đã bỏ lượt cộng trước khi mở SSE để một kết quả không bị ghi nhận hai lần.
Hạn mức vẫn kiểm tra trước RAG/LLM; tài khoản đã hết quota vẫn nhận 429.
Thay đổi không hoàn lại quota đã trừ ở các lượt trong quá khứ.

## Ngân sách relevance

- Mặc định code, fallback khi cấu hình <=0, hai `.env.example` và Docker Compose:
  `RELEVANCE_CHUNK_CHARS=800`.
- Với 12 ứng viên dài 2.500 ký tự: phần nội dung dùng chấm giảm từ 30.000 xuống
  9.600 ký tự, **68%**. Đây là giảm ký tự phần nội dung, không phải cam kết giảm
  68% tổng token hay hóa đơn.
- Tiêu đề văn bản, số hiệu, Điều/Khoản được giữ riêng. Chunk gốc không bị sửa;
  prompt trả lời vẫn nhận đầy đủ nội dung của các đoạn đã chọn.
- Giữ candidate K và quy tắc lựa chọn hiện có để không đồng thời giảm khả năng
  tìm thấy luật. Không thêm lượt LLM để tóm tắt từng chunk.
- Prompt chấm nhận biết đây là bản xem trước. Log `relevance usage` gồm số ứng viên,
  số ký tự prompt và usage, không log câu hỏi hay nội dung luật.

Đánh đổi: quy định nằm hoàn toàn sau ký tự 800 có thể bị bỏ sót. Cần đo tỷ lệ
`chặn nhầm`/`mất đoạn đúng` bằng `scripts/eval_relevance.py` trước triển khai rộng.
Nếu recall không đạt, điều chỉnh giới hạn bằng cấu hình hoặc thiết kế bản xem trước
theo từng khoản; việc cắt ngắn tự nó không khắc phục thiếu dữ liệu thương mại trong kho.

## OpenRouter reasoning

Generate và stream dùng cùng `_openrouter_reasoning_args`:

| Ngân sách hiệu lực | Payload | Giới hạn đầu ra |
| --- | --- | --- |
| 0 hoặc âm | `reasoning: {"effort":"none"}` | Ngân sách câu trả lời |
| Dương | `reasoning: {"max_tokens":N}` | Ngân sách câu trả lời + N |

Tham số mỗi lượt gọi ưu tiên hơn cấu hình chung. Lượt relevance mặc định dùng
`LLM_AUX_REASONING_MAX_TOKENS=0`. DeepSeek Flash/chat với cấu hình mặc định được
gửi `effort=none` rõ ràng. Ngân sách dương là lựa chọn bật reasoning có chủ đích;
không tự ghi đè lựa chọn đó chỉ vì tên model chứa `deepseek`.

Không dùng `exclude=true` để tiết kiệm: nó chỉ ẩn reasoning và token vẫn bị tính phí.
Model bắt buộc reasoning có thể từ chối `none`; client giữ cơ chế báo lỗi/chuyển
provider hiện có, không âm thầm thử lại cùng model với reasoning trả phí được bật.
Tham khảo [tài liệu OpenRouter](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
Kiểm thử giả lập xác minh payload, không chứng minh provider thực tế luôn trả 0 reasoning token.

## Bộ đánh giá thương mại

Chưa tìm thấy bộ 17 câu gốc và câu trả lời tương ứng trong workspace. Vì vậy chưa
có bằng chứng để kết luận câu nào DeepSeek trả lời xuất sắc hoặc nhầm Điều.
`scripts/commercial_cases.json` là **17 câu hồi quy bổ sung**, ghi rõ nguồn gốc,
gồm cả hai biến thể An Phát–Minh Long 30 tỷ; không thay thế bộ câu gốc.

Căn cứ để xây dựng kiểm tra số Điều:
[Luật Thương mại hợp nhất 113/VBHN-VPQH](https://datafiles.chinhphu.vn/cpp/files/vbpq/2025/9/113-vbhn-vpqh.pdf).
Ca tính phạt TM16 đặt rõ phần nghĩa vụ vi phạm 6 tỷ: trần 8% cho kết quả 480 triệu,
tránh lấy toàn bộ 30 tỷ làm cơ sở tính. Dữ liệu này là kiểm thử, không phải tư vấn
cho một hồ sơ chưa được cung cấp.

`scripts/eval_commercial.py` phân loại từng câu:

| Nhãn | Ý nghĩa |
| --- | --- |
| `not_run` | Chưa có kết quả, không bịa điểm |
| `out_of_scope` | Hệ thống trả câu không đủ căn cứ |
| `retrieval_gap` | Có bằng chứng truy xuất nhưng thiếu Điều kỳ vọng |
| `invalid_output` | Nội dung rỗng hoặc lượt gọi lỗi |
| `citation_review` | Thiếu số Điều kỳ vọng, cần kiểm tra viện dẫn |
| `content_review` | Thiếu dấu hiệu nội dung/số tiền cần kiểm tra |
| `needs_expert_review` | Qua sàng lọc tự động, chưa được chuyên gia xác nhận |
| `excellent` | Qua sàng lọc và có thẩm định chuyên gia được ghi tên |

Regex không xác minh được tính đúng của kết luận, ngữ cảnh phủ định, Khoản, hiệu lực
hay cặp số Điều–tên luật. Không dùng kết quả sàng lọc làm chứng nhận pháp lý.
File kết quả có thể thêm `retrieved_articles` khi đã thu thập được bằng chứng thực;
runner live chưa xuất danh sách này, nên không tự suy luận `retrieval_gap`.

Định dạng đầu vào `--results` là danh sách:

```json
[
  {
    "id": "TM16",
    "content": "Trần 8% × 6 tỷ = 480 triệu đồng (Điều 301 Luật Thương mại).",
    "metadata": {"model": "deepseek/deepseek-v4.1-flash", "tokens_used": 800}
  }
]
```

Số token trong ví dụ là dữ liệu minh họa. Chỉ thêm
`expert_review: {"verdict":"excellent","reviewer":"Tên người thẩm định"}` sau khi
đã thẩm định thật. Các dòng không được cung cấp vẫn hiện `not_run`.

Chạy tại thư mục `services/agent-service`:

```powershell
python -m pytest -q -p no:cacheprovider tests/test_quota.py tests/test_relevance.py tests/test_llm_client.py tests/test_llm_stream.py tests/test_conversations_api.py tests/test_commercial_evaluation.py
python -m pytest -q -p no:cacheprovider tests
python scripts/eval_commercial.py --results answers.json --output commercial-report.json
```

Chạy model thật (có phí provider; không đi qua endpoint quota người dùng):

```powershell
$env:LLM_PROVIDER = 'openrouter'
$env:OPENROUTER_MODEL = 'deepseek/deepseek-v4.1-flash'
$env:LLM_BYPASS = 'false'
python scripts/eval_commercial.py --live --output commercial-live-report.json
```

Cần cấu hình RAG URL, internal token và API key; không lưu key vào báo cáo.
Runner buộc provider/model đúng, tránh tính câu trả lời của provider dự phòng thành
thành tích DeepSeek. `tokens_used` phản ánh metadata hiện có; ở lượt trả lời thành
công hiện chưa bao gồm mọi lượt phụ, nên không dùng tổng cột này làm hóa đơn toàn pipeline.

## Kiểm chứng và triển khai

Các test bổ sung kiểm tra miễn quota với usage 6.575/None/0; fallback có khoảng
trắng; không miễn từ prompt hay cờ tùy ý; bốn đường đi HTTP và lưu metadata trong
SQLite; agent + relevance thật với RAG/provider giả; phần stream bị ngắt; payload
DeepSeek generate/stream; giữ nguyên chunk gốc; số tiền phạt và phân biệt thời hạn
khiếu nại với thời hiệu khởi kiện.

Phiên chỉnh sửa này chưa chạy được pytest: shell không tìm thấy Python/pytest,
runtime Python cài dưới thư mục người dùng và WSL bị từ chối truy cập, tải runtime
từ python.org cũng không kết nối được. Không có kết quả benchmark live.
`git diff --check` theo phạm vi thay đổi được dùng để kiểm tra whitespace.

Khi triển khai, kiểm tra biến môi trường thực tế: giá trị 2500 đã đặt trong `.env`
hoặc hệ thống deploy vẫn ưu tiên hơn mặc định 800. Đặt lại biến này, giữ hai ngân
sách reasoning bằng 0, rồi khởi động lại agent-service. Không thay quota Redis cũ.
Theo dõi usage relevance, tỷ lệ out_of_scope, lỗi provider và recall trước/sau.

Ngoài phạm vi bản sửa: chặn vượt quota tuyệt đối khi nhiều request đồng thời cần
reservation/settlement; hiện check và increment là hai thao tác riêng. Redis hỏng
vẫn fail-open theo thiết kế sẵn có. Những giới hạn này không làm lượt out_of_scope
bị tính quota nhưng cần tính đến khi quản lý chi phí vận hành.
