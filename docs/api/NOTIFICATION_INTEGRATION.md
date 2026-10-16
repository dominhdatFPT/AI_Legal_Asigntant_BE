# Quy ước nhận sự kiện và tích hợp Notification

Đối chiếu `develop` commit `dd79b1a`, ngày 07/10/2026. Phương án đề xuất để nhóm review.
Trạng thái: contract/tài liệu và ví dụ đã có; endpoint/DB/outbox/worker/FE polling chưa triển khai.

## 1. Chọn cách nào?

Đề xuất nhóm dùng API nội bộ: service có việc vừa xảy ra gọi Notification để báo việc đó.
Ví dụ Contract sinh PDF xong → gửi event → Notification lưu thông báo → FE lấy về và hiển thị.

Không cần RabbitMQ cho phương án này. Biến RABBITMQ_URL cũ chưa được sử dụng.
Một nội dung gửi qua API gọi là event (sự kiện), ví dụ “hợp đồng đã tạo xong”.
Tên event và mẫu JSON cố định để mọi người dùng cùng một cách.

Đề xuất này cần cả nhóm review trước khi xem là quyết định chung.
Nếu sau này chọn Pub/Sub, giữ event schema, thay phần vận chuyển; producer không gửi đồng thời cả hai cách để tránh trùng.

## 2. Ai làm phần nào?

| Bên | Việc phải làm |
|---|---|
| Service tạo sự kiện (producer) | Xử lý nghiệp vụ, xác định đúng người nhận, lưu event chờ gửi, gọi API Notification |
| Notification | Xác thực nội bộ, kiểm tra event, chống trùng, tạo nội dung thông báo, lưu read/mute/suppress |
| FE | Gọi API thông báo của user đăng nhập, hiện danh sách/badge/popup, đánh dấu đã đọc |
| User Service | Cấp JWT và quản lý tài khoản/quyền; không chứa bảng thông báo |

Producer không truy cập Notification DB.
Notification không sửa hợp đồng, file hoặc trạng thái phòng.
FE không gọi API gửi event và không được giữ token nội bộ.

## 3. Địa chỉ và header

Service gọi trong mạng Docker:

```http
POST http://notification-service:8000/internal/v1/notifications/events
Content-Type: application/json
X-Internal-Token: <INTERNAL_TOKEN>
X-Request-ID: req-demo-001
```

- URL gốc lấy từ NOTIFICATION_SERVICE_URL, không viết cố định ở nhiều file.
- Đây là URL đầy đủ /internal/v1; không ghép thêm /api/v1.
- INTERNAL_TOKEN là bí mật của Backend, đặt trong môi trường chạy, không đưa vào Git/FE.
- Dùng đúng cơ chế X-Internal-Token theo API_GUIDELINES.md. JWT user không thay thế token hệ thống.
- Gateway không có route /internal; FE chỉ gọi /api/notifications/....
- OpenAPI dùng server riêng tại operation nội bộ; khi import Postman cần kiểm tra URL này.
- Nếu chạy từ máy host, tên notification-service không resolve được. Ví dụ Python phải chạy trong container cùng mạng Docker hoặc dùng URL private được cấu hình phù hợp.

Mọi service được cấp cùng INTERNAL_TOKEN hiện nằm trong một miền tin cậy. source_service trong body chỉ là nhãn, không tự chứng minh danh tính producer.
Notification đối chiếu source/event/resource theo bảng ở mục 5 và chỉ cho các backend tin cậy gọi.
Nếu cần phân quyền producer chặt hơn, nhóm phải cấp token riêng và map token với service; không suy ra danh tính từ body.

## 4. Mẫu JSON thống nhất

Ví dụ Contract sinh file xong:

```json
{
  "event_id": "77777777-7777-4777-8777-777777777777",
  "event_type": "contract.generated",
  "source_service": "contract-service",
  "occurred_at": "2026-10-06T02:00:00Z",
  "recipient_user_ids": ["11111111-1111-4111-8111-111111111111"],
  "resource_type": "contract",
  "resource_id": "55555555-5555-4555-8555-555555555555",
  "payload": {
    "generation_id": "66666666-6666-4666-8666-666666666666"
  }
}
```

| Trường | Nghĩa | Quy tắc |
|---|---|---|
| event_id | ID của việc xảy ra | UUID do producer tạo một lần, giữ nguyên khi gửi lại |
| event_type | Việc gì xảy ra | Chọn đúng tên trong bảng dưới |
| source_service | Service báo việc | Phải khớp event_type |
| occurred_at | Thời gian việc xảy ra | ISO 8601 UTC, giữ nguyên khi retry |
| recipient_user_ids | User cần nhận | 1–100 UUID, không trùng; producer lấy từ nghiệp vụ có quyền |
| resource_type | Loại tài nguyên liên quan | document/contract/consultation/lawyer/user |
| resource_id | ID tài nguyên | Contract dùng draft_id; document dùng file_id; consultation dùng phòng |
| payload | Dữ liệu tối thiểu thêm | Chỉ generation_id hoặc is_warning/resource_version khi cần |

Không gửi toàn bộ hợp đồng, nội dung chat, OCR text, số giấy tờ hoặc token trong payload.
Notification tự tạo title/message từ event_type, không hiển thị HTML producer gửi.
Event không có người nhận thì producer không gọi. Danh sách hơn 100 thì chia batch, mỗi batch event_id riêng và được lưu trước khi gửi.
Người nhận room requested do Consultation chọn theo luật định tuyến nhóm; Notification không tự broadcast tới mọi luật sư.

## 5. Những event nhóm có thể dùng

| Event | Producer | resource_type / resource_id | Người nhận | payload |
|---|---|---|---|---|
| document.ocr_completed | document-service | document / file_id | Chủ file | {} |
| document.ocr_failed | document-service | document / file_id | Chủ file | {} |
| contract.generated | contract-service | contract / draft_id | Chủ nháp | generation_id bắt buộc |
| contract.generation_failed | contract-service | contract / draft_id | Chủ nháp | generation_id bắt buộc |
| consultation.requested | consultation-service | consultation / phòng | Luật sư được chọn để nhận yêu cầu | {} |
| consultation.accepted | consultation-service | consultation / phòng | Citizen yêu cầu | {} |
| consultation.message_created | consultation-service | consultation / phòng | Người còn lại, không phải người gửi | {} |
| consultation.warning_changed | consultation-service | consultation / phòng | Các thành viên phòng | is_warning + resource_version bắt buộc |
| lawyer.approved | lawyer-service | lawyer / lawyer_profile_id | User của hồ sơ | {} |
| lawyer.rejected | lawyer-service | lawyer / lawyer_profile_id | User của hồ sơ | {} |
| user.blocked | user-service | user / user_id bị khóa | Admin cần nhận theo quy định nhóm | {} |

Mọi trường hợp phải kiểm tra producer/type/resource khớp. Sai trả 422; source không được phép trả 403.
Event mới phải cập nhật OpenAPI, bảng này và ví dụ trước khi tích hợp.
Các event từ Document/Lawyer/User/Consultation là giao diện để người phụ trách các service đó tích hợp, không có nghĩa Hiền làm thêm các service này.
Tên và câu popup không chứa dữ liệu riêng. Ví dụ message_created chỉ hiện “Bạn có tin nhắn tư vấn mới.”

### Mute và cảnh báo phòng

Mute là lựa chọn của từng user: vẫn lưu thông báo nhưng không popup.
Suppress là trạng thái warning của phòng do Consultation quyết định: lưu nhưng ẩn khỏi danh sách mặc định và badge.

Ví dụ cảnh báo:

```json
{
  "event_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "event_type": "consultation.warning_changed",
  "source_service": "consultation-service",
  "occurred_at": "2026-10-06T02:00:00Z",
  "recipient_user_ids": ["11111111-1111-4111-8111-111111111111"],
  "resource_type": "consultation",
  "resource_id": "99999999-9999-4999-8999-999999999999",
  "payload": { "is_warning": true, "resource_version": 3 }
}
```

Event này cập nhật suppression, không tạo popup mới: created_count=0.
Consultation tăng resource_version mỗi lần thay đổi warning. Notification chỉ áp dụng version mới hơn đã lưu, bỏ bản đến muộn.
Gỡ warning gửi is_warning=false với version mới hơn.
Notification áp dụng cờ hiện tại cho mọi thông báo của phòng khi đọc danh sách/badge; is_suppressed không phải bản snapshot cố định từ lúc tạo.
Nếu phòng chưa có state ở Notification, lấy trạng thái warning hiện tại từ Consultation bằng API nội bộ sẽ do người phụ trách Consultation bàn giao. Trong lúc chưa lấy được, giữ thông báo suppressed để không popup nhầm, rồi đồng bộ lại.
Lưu ý code/spec Consultation mới nhất chưa có `is_warning`, `resource_version` hoặc API nội bộ này. Không suy ra warning từ `status`: các status hiện là waiting/in_progress/closed/cancelled. Đây là phụ thuộc cần nhóm xác nhận trước khi triển khai suppress.
Khi gỡ warning, thông báo cũ có thể hiện lại nhưng FE không phát lại popup cũ.

## 6. Thành công, trùng và lỗi

Notification trả 200 sau khi đã commit dữ liệu:

```json
{
  "data": {
    "event_id": "77777777-7777-4777-8777-777777777777",
    "is_duplicate": false,
    "created_count": 1
  },
  "error": null,
  "request_id": "req-demo-001"
}
```

Gửi lại đúng event/body: 200, is_duplicate=true, created_count=0.
200 nghĩa đã ghi bền vững; không có nghĩa FE đang online hay popup đã hiện.

Cùng event_id nhưng đổi recipient/payload/thời gian trả 409 EVENT_ID_CONFLICT.
Không tự sửa body rồi dùng lại event_id. Việc mới dùng UUID mới.

| Lỗi | Producer làm gì |
|---|---|
| 400/422 | Sửa JSON/dữ liệu; giữ record lỗi để kiểm tra |
| 401/403 | Kiểm tra token/quyền/source; không retry liên tục |
| 409 EVENT_ID_CONFLICT | Kiểm tra producer dùng lại ID sai; không phát event ID mới mù |
| Timeout/mất kết nối/429/5xx | Gửi lại đúng event/body/ID; giữ event chờ nếu vẫn lỗi |

Mỗi attempt timeout 5 giây. Một vòng gửi tối đa 3 attempt, chờ 0,5 rồi 1 giây.
Nếu chưa gửi được, worker gửi lại vòng sau, có khoảng chờ tăng dần; không lặp vô hạn trong request user.

## 7. Chống mất sự kiện và chống trùng

Cách đơn giản: producer có bảng outbox trong DB của chính nó.
Outbox là danh sách “việc đã xảy ra nhưng chưa gửi thông báo xong”.

1. Trong cùng transaction nghiệp vụ, lưu kết quả (ví dụ generation completed) và một outbox record chứa event JSON.
2. Worker đọc record pending, gửi Notification.
3. Nhận 200 hợp lệ, kể cả duplicate, đánh dấu sent.
4. Lỗi tạm thời giữ pending; lỗi body/token/conflict đánh dấu failed, log request_id để sửa và gửi lại có kiểm soát.

Gợi ý outbox: id/event_id, payload JSON, status pending/sent/failed, attempts, next_attempt_at, last_error_code, created_at, sent_at.
Không đặt outbox dùng chung cho mọi service.

Phía Notification: một transaction lưu processed_events + tất cả notifications của event.
Unique event_id ở processed_events và unique(event_id, recipient_user_id) ở notifications.
Nếu có request đồng thời cùng ID, constraint DB phải ngăn trùng; không chỉ “SELECT rồi INSERT”.
So sánh body đã chuẩn hóa: thứ tự key JSON và thứ tự người nhận không làm thành event khác.
Chỉ trả 200 sau transaction. Lỗi giữa chừng rollback tất cả; không xử lý nửa danh sách rồi báo thành công.
Lưu is_muted/is_suppressed không làm mất thông báo; gửi lại không tạo thêm bản ghi.
Không hứa exactly-once trên mạng: gửi ít nhất một lần, chống trùng ở bên nhận.
File/PDF đã sinh thành công vẫn thành công dù Notification tạm offline; outbox giữ phần thông báo để gửi sau.

## 8. Ví dụ code service gọi Notification

File mẫu: `contracts/examples/send_notification_event.py`.
Hàm nhận event đã lưu trong outbox, gọi bằng httpx, timeout 5 giây và retry có giới hạn.
Mẫu chỉ là adapter gọi API; không tự tạo outbox hay sửa DB.
Các service hiện dùng FastAPI; nên gọi hàm sync trong worker nền/thread phù hợp, không chặn event loop.

File JSON mẫu: `contracts/notification-event.example.json`.

Trong môi trường Docker có cấu hình NOTIFICATION_SERVICE_URL và INTERNAL_TOKEN:

```python
from send_notification_event import send_notification_event

# event phải lấy từ outbox, giữ nguyên event_id/body qua mọi lần thử.
result = send_notification_event(event, request_id="req-demo-001")
# Chỉ khi hàm trả thành công mới đánh dấu outbox sent.
```

Khi chạy trực tiếp file mẫu từ terminal, nó đọc JSON và thật sự gửi request.
Endpoint hiện chưa implement nên ví dụ chưa gửi thành công tới skeleton; đây là mẫu tích hợp cho lúc code API.

## 9. FE lấy và hiển thị thế nào?

Phương án ban đầu: polling mỗi 10 giây khi tab đang mở; dừng khi tab ẩn, chạy lại khi user quay về.
FE dùng JWT, gọi GET /api/notifications và GET /api/notifications/unread-count.
FE muốn xem lại tất cả dùng phân trang; lịch sử không mất vì user offline.

Popup chỉ hiện thông báo mới, chưa đọc, không muted, không suppressed.
Lần tải đầu chỉ hiển thị danh sách/badge, không popup toàn bộ lịch sử.
Dùng notification.id để không popup lại cùng bản ghi trong phiên.
Polling có thể bỏ lỡ popup nếu có nhiều hơn page_size thông báo giữa hai lần gọi; danh sách vẫn đủ qua phân trang. Không hứa popup cho từng event như realtime push.
Khi nhận 401, dừng polling và xử lý đăng nhập lại.
User bấm thông báo: FE chọn màn hình theo resource_type và resource_id, rồi gọi service sở hữu kiểm tra quyền.
Popup không tự cấp quyền xem file/hợp đồng/phòng.

Không cần device_tokens, email hay mobile push cho phương án web polling.
Nếu nhóm cần realtime ngay, thêm SSE/WebSocket và hợp đồng xác thực riêng; API nhận event giữa service vẫn giữ nguyên.

## 10. Bàn giao và kiểm thử sau khi implement

| Tình huống | Kết quả cần đạt |
|---|---|
| Một event cho hai user | Hai thông báo, chỉ user đúng thấy |
| Cùng event gửi ba lần hoặc đồng thời | Chỉ một thông báo/người nhận |
| Cùng event_id khác payload | 409; dữ liệu cũ không đổi |
| Notification commit xong nhưng producer timeout | Retry đúng event nhận duplicate, không trùng |
| Notification offline | Nghiệp vụ vẫn thành công; outbox pending |
| User mute phòng | Không popup, vẫn thấy trong danh sách |
| Phòng warning | Không popup/badge mặc định; vẫn lưu |
| Warning version 3 tới sau version 4 | Không ghi đè version 4 |
| Xóa quyền thành viên phòng | Không còn đổi preference; click phải kiểm tra lại quyền |
| Sai JWT/sai internal token | 401/403; không lưu event |
| FE gọi endpoint nội bộ qua Gateway | Không có route |
| User A đọc notification của B | 404 |
| Event chứa field ngoài schema | 422 |

Nhóm duyệt phương án API nội bộ, tên event, người nhận, polling và mute/suppress trước.
Sau đó từng người code outbox/producer của service mình; Hiền code nhận event và API đọc Notification.
