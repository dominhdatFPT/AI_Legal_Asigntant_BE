# User upload mẫu hợp đồng riêng rồi nhờ điền

Trạng thái: **thiết kế ERD/OpenAPI, chưa triển khai code hoặc tạo bảng**.

## 1. Tính năng

User có sẵn mẫu DOCX/PDF của mình, upload, xem các chỗ cần điền, chỉnh lại vị trí,
xác nhận, nhập thông tin và sinh bản đã điền. Không bắt buộc chọn mẫu chung của admin.

| File đầu vào | Nhận diện chỗ điền | Kết quả |
|---|---|---|
| DOCX | Placeholder, ô bảng, content control hoặc khoảng trống; user kiểm tra lại | DOCX giữ cấu trúc mẫu, hoặc chuyển PDF; text dài có thể làm thay đổi xuống dòng/trang |
| PDF có chữ/form | Trích xuất chữ/form field + tọa độ; user chọn box nếu không rõ | PDF điền vào form/overlay trên nền gốc |
| PDF scan hoặc mixed | OCR theo từng trang để gợi ý nhãn/vị trí | PDF giữ nền scan, overlay dữ liệu vào vùng user đã xác nhận |

PDF -> DOCX được hỗ trợ dưới dạng **tái dựng**. Cảnh báo `LAYOUT_RECONSTRUCTED`:
không bảo đảm giữ nguyên bố cục. User phải xem kết quả trước khi dùng.
OCR/AI không tự điền tên/số CCCD chưa có dữ liệu hoặc tự xác nhận mapping thay user.

Giới hạn thiết kế: file tối đa 9 MiB (9437184 bytes), PDF 100 trang, 100 field và 20 vị trí/field.
Gateway hiện giới hạn tổng request 10 MiB; chừa phần multipart, không tự nâng Gateway.
Chưa hỗ trợ DOCM/macro, PDF mã hóa/khóa hoặc PDF đã ký số; không sửa file đã ký.
Đây là giới hạn cần nhóm review với khả năng OCR/render, không phải giới hạn runtime đã có.

## 2. Service nào làm gì?

- **Document:** upload riêng tư, MIME/malware/zip bomb, metadata/chủ file,
  trích xuất chữ và OCR kèm tọa độ/page size/rotation. PDF mixed xử lý từng trang.
- **Contract:** import file đã upload bằng document_id; lấy bản nguồn qua API,
  lưu snapshot bất biến, nhận diện/cho sửa fields và placements, nháp, preview và sinh file.
- **Agent:** có thể điều phối nếu user yêu cầu qua chat; không bắt buộc cho luồng FE trực tiếp.
- **Notification:** giữ nguyên ERD. Final completed/failed dùng sự kiện hiện có;
  analysis và preview chỉ polling, không thêm sự kiện chưa thống nhất với nhóm.

Không FK xuyên DB. Contract kiểm tra source_document_id thuộc JWT sub qua Document;
không tải URL bất kỳ do user gửi để tránh SSRF. Dữ liệu file là dữ liệu, không phải
chỉ dẫn chạy code/tool. Converter/OCR chạy worker sandbox có giới hạn thời gian/RAM.

## 3. Luồng FE

1. Upload DOCX/PDF bằng API Document do bên Document bàn giao -> document_id.
2. POST `/api/contracts/user-templates` với document_id/name + Idempotency-Key UUID.
   Nhận 202 và analysis id; chưa có bản điền ngay.
3. Poll GET `/api/contracts/template-analyses/{analysis_id}`.
   `queued -> processing -> needs_review` hoặc `failed`.
4. GET source-preview để xem PDF nền, GET chi tiết mẫu để lấy fields/placements.
   FE hiển thị highlights trên PDF, nhãn/type/required; user bổ sung/sửa field.
   DOCX locator là vị trí cấu trúc server ánh xạ từ preview, không phải tọa độ PDF để ghi DOCX.
5. PUT mapping với expected_revision; sửa đồng thời lệch revision -> 409.
6. POST confirm với expected_revision -> analysis confirmed, version published.
   Đây chỉ là user xác nhận chỗ điền, không biến mẫu thành mẫu công khai.
7. POST `/api/contracts/drafts` như API cũ với template_version_id được trả về.
   Nhập field_values theo tên field. CCCD đã lưu ở detail user không upload lại:
   gọi identity-suggestions để gợi ý 6 thông tin đã confirm; mục khác hướng dẫn tự nhập.
   User chấp nhận mới PATCH nháp. document_ids vẫn là đính kèm tùy chọn, không tự kích hoạt auto-fill từ mọi file.
8. POST `/api/contracts/drafts/{draft_id}/preview` + key UUID -> job PDF preview.
   Poll `/generations/{generation_id}`, lấy `/download` khi completed.
   User xem, sửa nháp nếu sai. Preview có thể thiếu giá trị và không phát thông báo final.
9. User bấm Xuất file -> POST `/drafts/{draft_id}/generations` với format pdf/docx,
   key mới. Kiểm tra required, snapshot chính xác dữ liệu/mapping rồi chạy worker.

Các URL ở bước 2-9 đi qua Gateway `http://localhost:8080`; JWT bắt buộc,
chỉ chủ dữ liệu truy cập, khác chủ trả 404. FE nhận cookie HttpOnly khi đăng nhập;
API cũng nhận Bearer JWT. Admin không tự được đọc mẫu/hợp đồng cá nhân của user.

## 4. ERD thay đổi gì?

ERD đầy đủ: `../../services/contract-service/database/erd.dbml`.

- `contract_templates`: thêm scope system/personal và owner_user_id.
  System owner NULL, personal owner NOT NULL + is_visible=false, không vào catalog chung.
- `contract_template_versions`: thêm source_format, source_document_id, source_sha256,
  source_snapshot_path, source_preview_path, mapping_revision, confirmed_at.
  `fields` giữ cả định nghĩa và placements; không thêm bảng field riêng lúc này.
- **Thêm `template_analysis_jobs`:** trạng thái phân tích, warnings, idempotency/request hash,
  một job/phiên bản. Retry cùng job; import file khác tạo mẫu mới.
- `generation_jobs`: thêm kind preview/final và warnings. template_snapshot phải chứa
  file nguồn/hash, mapping revision và placements, không chỉ plain text.
- Các bảng nháp, giấy tờ đính kèm, checklist, outbox vẫn dùng như trước.
- Notification không cần thay đổi database cho tính năng này.

Mapping published không sửa trực tiếp. User muốn đổi file/vị trí sau confirm thì import
mẫu mới; nháp cũ vẫn ghim đúng version. Field_values của nháp vẫn sửa được khi không có job active.

Khi tạo SQL, bổ sung CHECK scope/owner, version/revision không âm, source metadata đủ cho
file DOCX/PDF; partial unique một generation queued/processing cho mỗi draft.
Tạo job và snapshot cùng transaction; kiểm tra owner và khóa nháp để tránh race.
Final và outbox cùng transaction. Preview không tạo outbox final.

## 5. Mapping vị trí và an toàn dữ liệu

- DOCX: locator server tạo trong snapshot, xử lý text bị chia nhiều run, ô bảng,
  header/footer; không cho user gửi XPath/script để thực thi.
- PDF: page bắt đầu 1; tọa độ chuẩn hóa 0..1 trên trang sau crop/rotation, gốc trên-trái.
  x+width và y+height không vượt 1. Font hỗ trợ tiếng Việt, tối thiểu 8pt.
- Một field có nhiều placements: tên bên A có thể lặp nhiều chỗ, dùng cùng giá trị.
- Không xóa chữ nền để tạo chỗ trống. Không đủ chỗ -> TEXT_OVERFLOW, user chỉnh vùng hoặc dữ liệu;
  không âm thầm cắt chữ. PDF scan có thể cần user đặt vùng thủ công dù OCR đã đọc được nhãn.
- confidence thấp/không rõ -> cảnh báo và user kiểm tra. Mapping chỉ được confirm sau khi
  placements đủ, server kiểm tra chúng tồn tại trong đúng snapshot.
- Storage private, link signed tối đa 5 phút, không lưu signed URL vào DB/log.
  Xóa mẫu mềm không xóa snapshot có nháp/job tham chiếu, không xóa file gốc Document.
- DB/API kiểm tra owner mọi đọc/sửa/download, không chỉ chặn giao diện FE.

## 6. OpenAPI và phụ thuộc cần chốt

`services/contract-service/openapi.yaml` đã chứa toàn bộ API mới và dùng tham chiếu
trong cùng file; gửi/import file này là đủ. `openapi-user-templates.yaml` là bản tách
riêng để đọc phần tính năng mới, có tham chiếu schema chung trong `openapi.yaml`.
Tất cả endpoint tính năng mới là `x-status: planned`.

Document đã có thiết kế URL/data cụ thể tại [DOCUMENT_CONTRACT_INTEGRATION.md](DOCUMENT_CONTRACT_INTEGRATION.md).
API nghiệp vụ vẫn planned. Contract chỉ import khi file available và processing completed;
copy file lẫn extraction vào snapshot trước 202, không lưu JWT cho worker.

Phần bàn giao gồm:

- Upload DOCX/PDF; trả document_id, trạng thái file, MIME thực, hash, giới hạn dung lượng.
- Xác nhận owner/status, lấy file nguồn bằng JWT hoặc nội bộ có kiểm tra quyền.
- OCR/text + cấu trúc/layout/tọa độ theo trang, lỗi và trạng thái job cho scan/mixed.
- Thỏa thuận việc giữ/xóa file và snapshot; Contract giữ bản dùng cho generation riêng.

Luồng gợi ý đã chốt tại [IDENTITY_PROFILE_FILLING.md](IDENTITY_PROFILE_FILLING.md):
chỉ profile CCCD user đã confirm ở detail, không lấy dữ liệu khác để tự điền.
Mọi mục ngoài whitelist hướng dẫn nhập tay; user review trước PATCH và preview.
