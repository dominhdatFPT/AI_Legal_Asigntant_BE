# Document T7/T8 — bàn giao

Cập nhật User gate09/10: [README_USER_GATE.md](../../services/document-service/README_USER_GATE.md). API ghi upload/OCR/profile PUT kiểm tra User status; khóa403, chưa nộp hồ sơ được chuẩn bị CCCD, upstream lỗi503. Compose bổ sung USER_SERVICE_URL/INTERNAL_TOKEN cho Document API, không cho OCR worker. Endpoint status chưa thấy trong User code local/develop đã kiểm tra; chưa nghiệm thu live integration. Không tự sửa User Service hoặc tạo citizen-profile từ Document.

Ngày09/10/2026. **Document API/worker/antivirus thật đang tắt theo user**, build/test không bật lại. Không restart service khác.

## T7 — phía Document

Document giữ15 API public. Adapter tham khảo `contracts/examples/document_handoff.py` gọi metadata → processing → extraction → content bằng Bearer user. Kiểm tra owner/purpose/type/DOCX-PDF, available/latest completed, document/job/hash/size/schema_version/source_kind; bytes và Content-Length/X-Content-SHA256/MIME/nosniff. File<=9MiB, JSON<=16MiB, GET timeout5s/tối đa3 attempt/backoff0.5-1s, không redirect/proxy. Snapshot không giữ JWT. Contract phải validate schema layout đầy đủ theo OpenAPI, copy bytes+JSON vào snapshot private và transaction import của mình. **Adapter không phải endpoint import Contract đã triển khai.**

Migration005 đã apply/verify đúng Supabase Document `srrxrnohunfyyolbfsew`. Terminal job transition tạo một outbox event/job cùng transaction: document.ocr_completed/document.ocr_failed, owner recipient, payload={}. Không backfill lịch sử; không OCR/filename/CCCD/token trong event; không sửa file/profile/bucket/DB service khác. Retry queued không phát event. RLS/browser revoke; service_role SELECT/UPDATE, không INSERT/DELETE; trigger backend-only enqueue, event body bất biến. Đây là file job T6, không phải phiên hai ảnh T4/KYC.

Dispatcher: POST `{NOTIFICATION_SERVICE_URL}/internal/v1/notifications/events`, X-Internal-Token, request ID theo event UUID. Timeout5s, tối đa3 HTTP attempt/vòng; SQL backoff10s tăng gấp đôi tới3600s, tối đa12 vòng rồi failed. Mạng/timeout/429/5xx/200 sai schema giữ pending; 3xx/4xx failed. Response<=16KiB, phải đúng event_id/count1 hoặc duplicate/count0; không redirect/proxy. Session lock ngăn gửi đồng thời; không giữ SQL transaction qua HTTP. Crash sau receiver commit vẫn replay đúng body/ID; không hứa exactly-once. Notification offline không đổi OCR completed thành failed.

**Delivery mặc định false và chưa bật** vì Notification receiver còn skeleton. Contract import cũng chưa implement. Không gửi vào404 hoặc sửa nghiệp vụ service khác để giả lập xong. Sent/failed giữ để kiểm tra; chưa có API replay failed/purge, không reset DB hoặc phát UUID mới mù.

Chỉ sau receiver được nhóm triển khai/kiểm tra và INTERNAL_TOKEN nằm trong .env local, đặt DOCUMENT_NOTIFICATIONS_ENABLED=true rồi:

```powershell
docker compose -f docker-compose.yml -f docker-compose.document-notifications.yml build document-notification-worker
docker compose -f docker-compose.yml -f docker-compose.document-notifications.yml up -d --no-deps document-notification-worker
# Tắt riêng dispatcher:
docker compose -f docker-compose.yml -f docker-compose.document-notifications.yml stop document-notification-worker
```

## T8 — hardening và nghiệm thu

API/worker/dispatcher UID65532, filesystem read-only, tmpfs bounded/noexec/nosuid, cap_drop ALL/no-new-privileges, PID/CPU/RAM limits; không publish port riêng. API chỉ mount public key read-only; worker không mount key. Antivirus digest/health gate; production FreshClam bật mặc định. Tesseract child env allowlist không chuyển arbitrary API key/loader env; Paddle child không nhận DB/Storage/JWT secrets. Upload/profile/worker đều yêu cầu Direct/Session5432+TLS, từ chối transaction pooler6543.

Health không thay readiness. Kiểm tra riêng chỉ đọc DB schema/RLS/quyền/bucket, không tiêu queue/ghi document/event. --worker kiểm tra thêm model files/ClamAV bằng bytes giả:

```powershell
python services/document-service/scripts/check_release.py
python services/document-service/scripts/setup_document_outbox.py --project-ref srrxrnohunfyyolbfsew
# Chỉ khi worker đã được nhóm bật:
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml exec -T document-worker python scripts/check_release.py --worker
```

Khi muốn bật lại Document (hiện vẫn tắt), build code mới rồi recreate riêng Document:

```powershell
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml build document-service document-worker
docker compose up -d --no-deps document-service
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml up -d document-antivirus document-worker
```

Số test cuối ở DOCUMENT_IMPLEMENTATION_TASKS.md. Native rootless/read-only/offline PaddleOCR DOCX/PDF text/scan/mixed/ảnh và Tesseract sáu field tiếng Việt tổng hợp pass. Full JWT/HTTP → ClamAV thật → native extraction → SQL disposable → content/extraction/owner/hash và EICAR rejection pass với005; **Storage in-memory/Notification mock**, không gọi Contract thật. Cloud readiness001/003/004/005/TLS/private bucket pass chỉ đọc. .env/secrets Git ignore.

## Giới hạn chưa nghiệm thu production

- Chưa CCCD/file thật xuyên suốt Storage cloud; dữ liệu tổng hợp không chứng minh độ chính xác/KYC.
- Contract import/Notification receiver/FE chưa triển khai: chưa snapshot/popup thật.
- Retention/quota/orphan-quarantine cleanup/purge CCCD-profile-outbox/backup chưa chốt. Soft-delete/unlink không xóa mọi bản sao; không tự purge dữ liệu nhóm.
- Không phải sandbox OS tuyệt đối: native parser/admission/API còn bề mặt tấn công; Python network guard không chặn mọi syscall native. Cần kernel/network-policy/dependency/load review trước production.
- PDF cùng trang native text+raster giữ text và warning, chưa OCR vùng raster đó; DOCX text không dựng layout/header/footer. Contract phải parse bytes thật.

FE render escaped text/textContent, không innerHTML; hiển thị pending/failed đúng, không coi confidence là xác thực.
