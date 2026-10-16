# Document Task 4 — OCR CCCD hai mặt, chạy local

Cập nhật 09/10/2026: chuyển khỏi VNPT theo yêu cầu. Tesseract 5 với hai model LSTM pretrained `vie` và `eng` (tessdata_fast 4.1.0). Dùng model pretrained, không train. Model tải khi build Docker; inference dùng model đã cài, không upload ảnh ra dịch vụ OCR. Supabase vẫn lưu ảnh/JSON riêng tư, nên toàn luồng API không phải ứng dụng hoàn toàn offline.

## Hợp đồng nhóm và màn hình FE

JWT RS256 chung, owner từ `sub`; cookie mutation cần Origin tin cậy. Admin không bypass owner. FE không nhận key Supabase/eKYC.

1. Chọn hai ảnh JPEG/PNG, mỗi ảnh <=4 MiB, <=25 triệu pixel, một frame; tổng multipart <=10 MiB. Chụp rõ, đúng chiều, đủ nhãn và nội dung. Chưa sửa perspective/ảnh lộn ngược thiếu EXIF.
2. `POST /api/documents/identity-ocr` (internal `/api/v1/documents/identity-ocr`), multipart đúng `front`/`back`, header `Idempotency-Key` UUID. Không gửi owner, URL hoặc JSON OCR tự tạo.
3. `201`: phiên completed, sáu trường full_name, identity_number, date_of_birth, issue_date, issue_place, permanent_address; ngày ISO, CCCD string 12 số. Thiếu/không chắc: null + REVIEW_*; xung đột hai mặt: null + CONFLICT_*. Luôn có OCR_REQUIRES_USER_REVIEW.
4. FE hiển thị form cho user kiểm tra/sửa. T5 đã có PUT identity-profile/me với source_session_id, expected_revision và sáu field; xem IDENTITY_PROFILE_FILLING.md. Không tự lưu profile, đổi KYC/role User hoặc sửa nháp Contract.
5. `GET /api/documents/identity-ocr/{session_id}` chỉ owner đọc processing/completed/failed; phiên lạ/người khác 404. Raw JSON/storage keys không trả FE.

Envelope `{data,error,request_id}`, no-store. verification_status luôn not_performed: OCR không kiểm tra CCCD thật/giả, chip, khuôn mặt hoặc danh tính người gửi. Confidence không phải chứng nhận xác thực.

## Parser và chất lượng

Parser ghép nhãn Việt/Anh cùng dòng hoặc giá trị dòng kế tiếp, confidence >=0.75. Hỗ trợ nhãn tên/ngày sinh CCCD cũ và căn cước mới. Không đổi O thành 0, không lấy ngày hết hạn làm ngày cấp, không đoán nơi cấp từ địa chỉ. Nhãn/bố cục chưa hỗ trợ cần user nhập lại. Địa chỉ nhiều dòng chưa tự nối để tránh lấy nhầm nội dung sau. Không cam kết mọi ảnh điện thoại/loại thẻ đều đọc đúng.

Không lấy Nơi cư trú hoặc nhãn tiếng Anh Place of residence đứng riêng làm địa chỉ thường trú; chỉ ghép khi có nhãn Nơi/Địa chỉ thường trú hoặc Permanent address rõ ràng. Các trường vẫn cần user review; FE render text, không chèn OCR vào HTML không escape.

Không có một mặt đọc được cả số CCCD và họ tên: `422 OCR_UNREADABLE`, lưu JSON đã đọc, phiên failed. Ảnh trắng không báo completed. Đây chỉ là kiểm tra đủ dữ liệu gợi ý, không chứng minh giấy tờ thật.

OCR input giảm tối đa cạnh 2400 pixel trong RAM; ảnh lưu giữ độ phân giải chuẩn hóa ban đầu. Raw JSON engine có tên/version model, kích thước, mọi dòng nhận diện cùng confidence/polygon theo ocr_size, tối đa 400 dòng/1 MiB. Không ghi ảnh temp hoặc in chữ OCR vào log. Đây không phải JSON response VNPT.

## Lưu trữ và chống chạy trùng

Migration additive `003_identity_ocr_sessions.sql` tạo phiên owner-only/RLS/revoke PUBLIC/anon/authenticated; không sửa bốn bảng nền tảng hoặc DB User. `scripts/setup_identity_ocr.py --project-ref ...` guard đúng project, mặc định chỉ đọc; --apply chỉ tạo bảng thiếu.

Hai JPEG giải mã/sửa EXIF/bỏ metadata lưu private `document-files/quarantine/<owner>/identity/<session>/front.jpg`, back.jpg. JSON engine từng mặt lưu JSONB riêng trước chuẩn hóa nghiệp vụ. Reserve metadata trước ghi Storage/OCR; transaction SQL ngắn. Hash giữ thứ tự byte ảnh gốc và namespace engine/parser, không replay kết quả engine cũ.

Cùng owner/key một phiên; completed replay cùng phiên; khác ảnh/engine 409. Processing/crash: `409 EKYC_IN_PROGRESS` (giữ mã cũ để FE tương thích), cần GET/đối soát. Failed replay lỗi đã lưu + session_id, không chạy lại. FE không tự đổi key khi timeout; chỉ user chủ động thử lại dùng key mới.

Deadline cặp OCR 55 giây gồm hai subprocess native; timeout kill/reap child. Mỗi API process một inference đồng thời, không xếp hàng không giới hạn. Lỗi: OCR_BUSY/OCR_MODEL_UNAVAILABLE/OCR_UNAVAILABLE 503, OCR_BAD_RESPONSE 502, OCR_TIMEOUT 504. Lỗi sau reserve có error.details.session_id. Gateway read timeout Document 120 giây; DB/Storage giới hạn riêng.

Failed giữ ảnh private/JSON để đối soát; chưa có retention/purge phiên. Chốt thời hạn lưu/lifecycle trước production. Không xóa theo prefix rộng hay tự retry phiên crash/commit không rõ.

## Build và kiểm tra

```powershell
# Từ backend. Build riêng, không restart dịch vụ nhóm.
docker compose build document-service
# Model thật, ảnh tổng hợp; inference container tắt mạng:
docker run --rm --network none backend-document-service python scripts/check_local_ocr.py
# DSN test phải là PostgreSQL riêng localhost, không production:
python -m pytest tests/test_document_local_ocr.py tests/test_document_identity_ocr.py tests/test_document_identity_images.py -q
python services/document-service/scripts/setup_identity_ocr.py --project-ref srrxrnohunfyyolbfsew
```

Docker cài model/dependency CPU, không cần GPU/Access Token/VNPT key. DOCUMENT_OCR_CPU_THREADS=2 (1–4); DOCUMENT_OCR_MODEL_DIR mặc định /usr/share/tesseract-ocr/5/tessdata, có vie.traineddata và eng.traineddata. Compose dùng model root trong image. Python native phải cài binary Tesseract 5 trên PATH và hai model, đặt model root đúng máy; Docker là đường chạy đã thiết kế/kiểm thử.

Tham khảo chính thức: [Tesseract LSTM/TSV](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html), [model pretrained tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast).

## Nghiệm thu 09/10/2026

- Vòng cuối: 285 test liên quan Document/Contract/Notification/Gateway/config pass, gồm 29 test local OCR; Python AST, full OpenAPI validator, Compose và build image pass. Warning deprecation từ dependency test không làm test fail. Không tuyên bố mọi service trong repo đã hết lỗi (Location YAML vẫn là vấn đề riêng đã ghi ở task log).
- Đã build image riêng codex-document-local-ocr-test. Model thật đọc đúng sáu trường trên ảnh tổng hợp tiếng Việt và ảnh trắng không sinh fields; Docker --network none. Đây không phải đánh giá độ chính xác CCCD thật.
- Smoke HTTP ASGI dùng JWT RS256 sinh trong RAM + OCR native + PostgreSQL tạm: 401 thiếu JWT, POST 201/replay, GET owner 200/khác chủ 404, lưu JSON hai mặt đều pass. Storage trong smoke này là stub; adapter cloud đã kiểm tra riêng ở Task 3, chưa phải nghiệm thu end-to-end OCR với Storage cloud.
- Migration 003 đã apply đúng project srrxrnohunfyyolbfsew; kiểm tra schema/RLS, revoke anon/authenticated và backend SELECT/INSERT/UPDATE pass. Không thay bảng nền tảng/bucket/User DB.
- Test thật phát hiện Paddle model Latin vừa tải không có nhiều ký tự dấu Việt; đổi sang Tesseract vie+eng, không train/không đoán bù chữ. Model Paddle thử nghiệm không còn trong Dockerfile/runtime.
- Đã sửa schema error.details để chấp nhận session_id object lẫn danh sách validation errors của nhóm; kiểm thử lỗi 504 theo OpenAPI. Test hard-timeout dùng child thật xác nhận kill/reap, không để CPU chạy sau timeout.
- Container nhóm chưa restart; code mới cần build/recreate riêng Document khi nhóm muốn dùng. Profile T5 đã triển khai; worker T6, retention và ảnh CCCD thực tế còn cần làm/kiểm thử.

## Lịch sử VNPT — không còn runtime

Adapter theo Java mẫu probe /v1/ocr/id-card nhận 404/405; thử upload luồng khác với hai token nhận 401. Không đủ bằng chứng VNPT OCR hoạt động. vnpt.py/check_vnpt.py và test cũ chỉ giữ đối chiếu lịch sử, không được router/Compose sử dụng. EKYC cũ trong .env cá nhân không bị xóa nhưng engine mới bỏ qua chúng. Không dùng probe VNPT để kiểm tra local OCR.
