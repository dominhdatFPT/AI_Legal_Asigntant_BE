# Triển khai Document theo task nhỏ

Ngày bắt đầu: 08/10/2026. Phạm vi: Document và cấu hình liên quan; không tự sửa nghiệp vụ service của người khác.

| Task | Phạm vi | Trạng thái |
| --- | --- | --- |
| 1 | JWT, response/error, config DB/Storage, kiểm tra kết nối, Docker env | Test offline và kiểm tra cloud chỉ đọc đã pass sau khi bổ sung `.env`. |
| 2 | Upload/list/detail, chống trùng, giới hạn file và owner | Đã có code; 40 test Task 2 pass với PostgreSQL tạm + Storage giả lập; chưa nghiệm thu upload cloud thật. |
| 3 | Content/download/delete, điều kiện chặn xóa | Đã có code/test SQL và kiểm tra adapter Storage cloud thật; worker vẫn chưa triển khai. |
| 4 | Hai ảnh CCCD → OCR local → ảnh/JSON riêng tư → trường gợi ý | Chuyển Tesseract 5 pretrained vie+eng CPU ngày 09/10/2026; không train/không VNPT. Xem DOCUMENT_IDENTITY_OCR.md. |
| 5 | User duyệt sáu field profile, revision/thay/xóa | GET/PUT/DELETE, session T4 + legacy source, CAS/owner/CSRF đã triển khai; xem IDENTITY_PROFILE_FILLING.md. |
| 6 | Worker, DOCX/PDF text, PaddleOCR pretrained cho scan/ảnh | Đã triển khai/test; từng bật thật với queue rỗng, hiện tắt theo user. |
| 7 | Contract handoff và outbox/retry sự kiện Notification | Phía Document có adapter/test handoff và outbox/dispatcher005. Delivery tắt; Contract import/Notification receiver chưa triển khai. |
| 8 | Docker/e2e/security, tài liệu và bàn giao | Có hardening, readiness, regression/native smoke và DOCUMENT_RELEASE.md. Full cloud/file thật/receiver/import e2e chưa nghiệm thu. |

## T7/T8 — trạng thái mới nhất

[DOCUMENT_RELEASE.md](DOCUMENT_RELEASE.md) là bàn giao hiện tại. Document thật vẫn tắt theo user; không restart service khác. Outbox005 đã apply/verify đúng project, không backfill/sửa file/profile/bucket. Delivery mặc định false. Không đổi API Contract/Notification planned thành implemented.

Bộ nghiệm thu cuối trên DB rỗng: **424 test pass/không skip**, trong đó45 test T7/T8 (30 outbox,11 handoff,4 release). Native rootless/read-only/offline PaddleOCR/Tesseract và JWT/HTTP/ClamAV/native extraction/SQL riêng/content/hash/owner/EICAR pass với005; Storage stub/Notification mock. Uvicorn rootless thật health200/auth401; full Document OpenAPI/Compose/cú pháp pass. Cloud readiness chỉ đọc schema001/003/004/005/RLS/privileges/FK/unique/check/SECURITY DEFINER/search_path/TLS/private bucket pass; không full cloud e2e.

Fresh migration test phát hiện fixture dùng now() từ transaction trước lúc upload commit qua connection khác, làm completed_at sớm hơn created_at; sửa reset transaction fixture rồi chạy lại DB rỗng pass. Không nới constraint timestamp runtime. Test no-backfill lịch sử terminal, backend role enqueue/không DELETE, crash sau receiver commit replay cùng ID và retry12 vòng đều pass. Container DB/API/antivirus test riêng được dọn sau nghiệm thu, chỉ xóa dữ liệu tổng hợp; image/model cache giữ để dùng lại. Các mốc T1–6 dưới đây là lịch sử.

Task 4 local ngày 09/10/2026: 285 test hồi quy liên quan pass (29 test local OCR). Model Tesseract thật chạy Docker không mạng đọc sáu trường ảnh tổng hợp; API + JWT + SQL tạm pass (Storage stub). Migration 003 cloud đúng project/RLS/quyền backend-browser đã kiểm tra. Chưa test ảnh CCCD thật hoặc toàn luồng OCR với Storage cloud; chưa restart container nhóm. T5 đã hoàn tất phần backend bên dưới; T6–8 chưa triển khai.

## Task 5 — kết quả ngày 09/10/2026

T6 được triển khai sau các mốc T1–5 bên dưới: worker + processing/extraction, không migration mới/không sửa User/Contract/Notification. Engine model thực được build riêng; hướng dẫn/trạng thái nghiệm thu hiện tại ở [DOCUMENT_WORKER.md](DOCUMENT_WORKER.md). T7–8 chưa làm.

Cập nhật vận hành 09/10/2026: đã recreate riêng Document API và bật worker/ClamAV kết nối Supabase thật. Gateway health200/auth401; DB/private Storage/ClamAV/model local kiểm tra đạt, queue đang rỗng. Chưa nghiệm thu file thật trên cloud. Không restart service khác; các ghi nhận chưa bật worker bên dưới là lịch sử trước lần triển khai này.

Nghiệm thu T6: **379 test liên quan pass/không skip** DB rỗng, gồm36 test T6. Image API/worker build, OpenAPI/Compose/syntax pass. DOCX/PDF text/scan/mixed/ảnh native offline pass; full JWT/HTTP → ClamAV thật → PaddleOCR/parser thật → SQL tạm → extraction/content/hash/owner và EICAR rejection pass, Storage stub. Filesystem read-only/tmpfs/cap-drop pass; T4 Tesseract vẫn pass. Không bật queue/cloud worker/restart nhóm; DB/antivirus test riêng dọn sau nghiệm thu. Xem giới hạn mixed-page/retention/production signature vận hành ở DOCUMENT_WORKER.md.

Ghi nhận T5 ngày09/10: 69 test T5 + design pass trên DB mới (bảo toàn legacy migration); hồi quy 340 pass/1 skip migration đã chạy. Docker actual OCR → JWT/HTTP/SQL tạm → user review/profile/CAS/owner/unlink/recreate pass, ảnh tổng hợp/Storage stub. DELETE204 theo API_GUIDELINES; migration004 additive/XOR/terminal immutability; không sửa User/Contract/Notification, không restart nhóm. Unlink không purge; T6–8 chưa làm. Hướng dẫn: IDENTITY_PROFILE_FILLING.md. Các đoạn T4 bên dưới là lịch sử triển khai, trạng thái profile hiện tại theo T5.

Lượt cuối trên DB rỗng, bổ sung test Swagger và setup verifier: **343 test liên quan pass, không skip**. Image cuối build + actual OCR/API/profile smoke pass. Cảnh báo dependency deprecation không làm fail test.

Migration004 đã apply trên đúng Supabase Document srrxrnohunfyyolbfsew; kiểm tra chỉ đọc lại constraints/FK/trigger/RLS/browser revoke/backend privileges pass. Không sửa profile cũ/bucket.

## Task 1 — đối chiếu với nhóm

- Đối chiếu `docs/API_GUIDELINES.md`, `libs/common_auth`, User/Contract/Notification, OpenAPI, Gateway và Compose.
- Giữ internal `/api/v1/documents`, FE `/api/documents`, health JSON cũ và envelope nghiệp vụ `{data,error,request_id}`.
- Dùng common_auth RS256/issuer/access type/exp; thêm UUID owner và role whitelist citizen/lawyer/admin. Không tự cấp token, không đọc DB User.
- Thêm `/me` diagnostic như Contract/Notification. Cập nhật OpenAPI/test từ 12 thành 13 operations; chỉ health/me implemented, nghiệp vụ khác planned.
- Cookie mutation kiểm tra exact Origin; Bearer cho server-to-server. Response module riêng Document, không thay shared format của nhóm.
- Config DB/Storage cùng project, TLS/pooler, timeout, bucket private/MIME/9 MiB; không dùng fallback Supabase chung.
- Docker nhận env Document, chỉ mount public key. Sửa Gateway thiếu allow Idempotency-Key theo regression test đã có.
- Không gọi VNPT, không tải model và không thay dữ liệu/migration/cloud bucket.

## Kiểm tra và giới hạn

- Vòng cuối: 115 test foundation/design/Contract/Notification/config/Gateway pass (trong đó 53 test nền tảng Document); 38 test shared JWT pass; 9 kiểm tra OpenAPI Document/Contract/Notification pass; 1 health Document pass. Validator OpenAPI đầy đủ và kiểm tra Compose env/public-key-only mount pass.
- Test JWT ban đầu gặp lỗi quyền thư mục temp/cache của pytest; chạy lại với thư mục temp riêng và tắt cache đã pass, không sửa thư viện shared auth.
- Cloud: sau khi user bổ sung `.env`, `scripts/check_runtime.py` pass DB TLS, bốn bảng RLS và bucket private/MIME/9 MiB. Chỉ đọc, không sửa cloud. Database URL không thay API credential Storage.
- Lỗi Gateway CORS Idempotency-Key đã được test phát hiện và sửa trong phạm vi Document.
- OpenAPI Location có lỗi YAML cũ ở description chứa `VD: LAND_GIFT`; chưa sửa service ngoài phạm vi. Không tuyên bố toàn bộ test của nhóm xanh khi lỗi đó còn tồn tại.
- Ownership/upload đã test với PostgreSQL tạm. Upload cloud và eKYC thật chưa test; các task sau phải bổ sung trước bàn giao.

## Task 2 — upload/list/detail

- `POST /api/documents/`: multipart đúng ba field, JWT/Origin, UUID Idempotency-Key, giới hạn file 9 MiB và tổng body 10 MiB cả chunked. Trả 202 pending_check/queued; chưa có worker nên không chuyển available/completed.
- `GET /api/documents/`: pagination/filter theo enum, owner-only, không hiện deleted; count/items cùng snapshot, sort created_at/id giảm dần.
- `GET /api/documents/{document_id}`: owner-only; admin không bypass. Response whitelist đúng OpenAPI, không có storage_key/request_hash/uploaded_sha256.
- Admission: nội dung PDF/DOCX/JPEG/PNG, MIME/đuôi/purpose/type khớp; từ chối PDF mã hóa/quá trang/trường signature, DOCX macro/zip unsafe/XML entity; kiểm tra cấu trúc ảnh. Đây không thay malware scan/EXIF cleanup của worker Task 6.
- Quarantine riêng tư, object path do backend sinh. DB lock advisory theo owner/key chống retry đồng thời; metadata+job cùng transaction. Replay cùng request trả cùng file, khác fingerprint 409, deleted 410.
- Nếu Storage/SQL lỗi: rollback và bù trừ object khi DB xác nhận không có metadata. Commit không rõ kết quả/DB mất kết nối thì không xóa nhầm object đã commit. Nếu bù trừ không thực hiện được, log cảnh báo an toàn; task maintenance sau cần dọn orphan private có kiểm tra DB và thời gian grace, không tự xóa theo prefix rộng.
- 40 test Task 2 + 53 foundation pass. Có SQL thật trên PostgreSQL localhost/container riêng: upload, phân trang/owner/admin, replay/concurrent 4 request, lỗi Storage, lỗi SQL sau upload, mất kết nối sau commit, soft-deleted replay. Không dùng ảnh thật hay ghi Supabase nhóm.
- Vòng hồi quy cuối: 155 test liên quan pass, 9 kiểm tra OpenAPI ba service pass; Document full validator và kiểm tra YAML không trùng key pass. Sửa phát hiện thêm: cleanup multipart chunked vượt giới hạn, phân biệt lỗi Storage 502/503/504, pagination page rất lớn không gây overflow SQL và tránh xóa object khi commit không rõ kết quả.
- Build image Document riêng pass; container HTTP kiểm tra health, thiếu JWT, upload chưa đăng nhập và `/me` với RS256 pass. Không restart container nhóm hoặc dùng secret thật trong container thử.
- Container PostgreSQL/HTTP tạm được dọn sau test; dữ liệu test giả bị xóa cùng DB tạm, không tác động Supabase thật. Image test được giữ lại để tái dùng.

## Task 3 — xem/tải/xóa mềm

- `GET /api/documents/{document_id}/content`: owner-only, file available; binary sau khi đọc đủ size/hash (tối đa 9 MiB) mới trả 200. Attachment filename an toàn, nosniff, no-store, Content-Length/X-Content-SHA256/X-Request-ID. Không redirect hoặc trả partial như thành công; lỗi JSON 502/503/504 trước binary.
- `GET /api/documents/{document_id}/download`: owner-only/available, link Supabase HTTPS signed tối đa 300 giây, query download filename được encode. Validate host/path/token để không trả URL public/ngoài project/ngoài object. Không lưu hoặc log signed URL.
- `DELETE /api/documents/{document_id}`: khóa file → kiểm tra active job/profile → soft-delete. Queued/processing: 409 DOCUMENT_BUSY; profile active: 409 PROFILE_SOURCE_IN_USE. Owner gọi lại: 204 không body; khác chủ/admin hoặc không tồn tại: 404. Không hard-delete Storage/result/snapshot Contract.
- Chặn pending/rejected, còn file available + OCR failed vẫn tải được. Sau soft-delete không cấp link mới/đọc nội dung; link đã cấp là bearer capability có thể còn dùng trong TTL. Không hứa thu hồi tức thì hoặc xóa bản user đã tải. Chính sách retention/purge phải chốt ở task maintenance.
- FOR SHARE lúc đọc/ký link và FOR UPDATE lúc xóa serialize thao tác trên cùng file. Kiểm tra đường dẫn Storage còn khớp owner/document ID; không tin storage_key bị gán sang file của owner khác.
- Gateway expose các header download cho browser FE đọc; CORS origin/credentials giữ nguyên chuẩn nhóm. Object mới lưu với Cache-Control no-store/max-age=0; worker sau phải giữ policy này cho bản safe. Các API/ảnh probe không bỏ qua availability guard của public API.
- 41 test Task 3 pass trên PostgreSQL localhost riêng + Storage mock: OCR failed, pending/rejected, owner/admin, profile active/unlink, concurrent delete, download/delete race, nội dung thiếu/dư/hash sai/MIME/encoding/timeout/redirect, filename và CSRF.
- Vòng cuối: 197 test liên quan pass (41 Task 3), 9 test OpenAPI ba service pass, validator OpenAPI đầy đủ/không trùng YAML/cú pháp Python pass. Lỗi encoding Storage ban đầu bị phân loại 503, đã sửa thành 502 và test lại pass. Còn warning deprecation từ dependency test hiện có, không phải lỗi runtime hoặc test fail.
- Docker build, HTTP (JWT/protected routes/lỗi config an toàn), nginx -t và docker compose config --quiet pass. Không restart dịch vụ nhóm; container PostgreSQL/HTTP tạm được dọn sau khi test, image test giữ để tái dùng.
- Probe cloud: một PNG tổng hợp 8×8, không CCCD hay file user; authenticated read + hash, link signed 60 giây, bytes/attachment/nosniff đều pass. Object probe đã dọn, không ghi DB/đổi bucket. Script không có --run-probe thì không ghi cloud.
- Nguồn đối chiếu protocol: [Supabase createSignedUrl](https://supabase.com/docs/reference/javascript/file-buckets-createsignedurl), [download filename trong Storage SDK](https://github.com/supabase/storage-js/blob/master/src/packages/StorageFileApi.ts). Retention và giới hạn link/caching không thay thế kiểm tra quyền trên API.
- Worker Task 6 chưa có: upload thật vẫn pending_check/queued, không tự gán available để demo. Task 3 không phải xác nhận toàn luồng upload → OCR → tải đã hoàn thiện.

### Kiểm tra Task 3

Từ backend với DOCUMENT_TEST_DATABASE_URL là DB PostgreSQL tạm localhost:

```powershell
python -m pytest tests/test_document_download_delete.py -q
python services/document-service/scripts/check_storage_download.py
# Opt-in: tạo một PNG giả private, kiểm tra adapter rồi xóa đúng object đó.
python services/document-service/scripts/check_storage_download.py --run-probe
```

## Task 4 — kiểm tra đầu tiên trước khi nhận code mẫu (lịch sử)

- Kiểm tra cấu hình cục bộ không in secret: provider VNPT, base HTTPS `api.idg.vnpt.vn`, hai biến token có dữ liệu, validation đang false. Token hiện tại không có tiền tố Bearer và không có cấu trúc JWT; đây **không đủ** để kết luận là Token-key hay Access Token, cũng không xác nhận còn hạn/quyền truy cập.
- Repo chưa có đặc tả API/Postman VNPT của tài khoản. Không suy đoán endpoint v1/v2, tên header, field JSON hoặc tự dùng một token cho hai vai trò. Cần tài liệu đã che secret hoặc nhãn loại token từ tài khoản VNPT trước khi viết adapter thật.
- Module `app/services/identity_images.py` chỉ xử lý ảnh, chưa gắn endpoint công khai, không gọi mạng, không ghi DB/cloud, không tạo profile hay đổi trạng thái KYC của User. Các API Task 1–3 giữ nguyên.
- Giới hạn ứng dụng đề xuất: mỗi ảnh JPEG/PNG tối đa 4 MiB, 25 triệu pixel, một frame; hai ảnh phù hợp tổng multipart 10 MiB hiện có của Gateway. Đây không phải giới hạn công bố của VNPT, cần đối chiếu đặc tả trước khi nối API.
- Giải mã toàn bộ ảnh, sửa chiều theo EXIF, chuyển sang JPEG từ pixel mới, bỏ metadata/EXIF/GPS và dữ liệu đính kèm; từ chối ảnh hỏng/MIME sai, ảnh nhiều frame, ảnh chuẩn hóa quá lớn và hai mặt trùng ảnh. Không downscale hoặc coi bước này là kiểm tra giấy tờ thật.
- Giữ hash nội dung chuẩn hóa và fingerprint byte đầu vào có thứ tự mặt trước/sau để dùng cho idempotency ở bước sau; chưa triển khai lưu/replay phiên eKYC.
- Thiết kế tiếp theo: phiên hai ảnh riêng, owner-only/RLS; JSON gốc provider lưu riêng tư trong DB (bucket hiện không cho MIME JSON); sáu field chỉ là gợi ý, user xác nhận ở Task 5. Không tự nâng OCR thành xác minh danh tính hoặc sửa DB User/Contract.
- Test chỉ dùng ảnh giả. Lần đầu có một assertion mong mã MIME khác quy ước admission hiện có; sửa test để khớp `UNSUPPORTED_FILE_TYPE` và chạy lại. Không khẳng định VNPT thật hoạt động khi chưa có đặc tả hoặc kiểm thử nhà cung cấp.

```powershell
python -m pytest tests/test_document_identity_images.py tests/test_document_foundation.py tests/test_document_contract_design.py -q
```

## Chạy kiểm tra chung

Cập nhật 09/10/2026: Task 4 chuyển Tesseract 5 CPU local theo yêu cầu, xem DOCUMENT_IDENTITY_OCR.md. VNPT phía trên chỉ là lịch sử. Giữ API/owner/RLS/idempotency/sáu field và not_performed; profile T5 đã triển khai, worker tổng quát T6 chưa có.

Từ `backend`, cài dependencies test của nhóm rồi chạy:

```powershell
python -m pytest tests/test_document_foundation.py tests/test_document_contract_design.py tests/test_personal_template_design.py tests/test_contract_notification.py tests/test_config.py tests/test_gateway_config.py -q
python services/document-service/scripts/check_runtime.py
```

Giữ password/key trong `.env` được ignore; không gửi cho FE hay push GitLab. Bucket mặc định document-files.

## Tích hợp User status — 09/10/2026

- Document gọi GET `/internal/v1/users/{JWT-sub}/status` với X-Internal-Token trước upload, OCR hai mặt và PUT profile. Không dùng user_id từ FE, không forward JWT/cookie, không cache quyết định cho phép. Compose bổ sung USER_SERVICE_URL/INTERNAL_TOKEN cho API, không worker.
- ACCOUNT_LOCKED: 403 và message User; PROFILE_NOT_SUBMITTED: vẫn cho chuẩn bị giấy tờ/profile, không thay guard tạo hồ sơ của Contract. User lỗi/thiếu endpoint/token/JSON không đúng: 503 trước side effect. Không đọc DB User hoặc tự ghi citizen-profile/KYC.
- Đối chiếu shared auth, envelope, Gateway và User origin/develop `eefe634`: citizen-profile có, status chưa tìm thấy. `.env` hiện chưa có INTERNAL_TOKEN. Chưa thể xác nhận liên thông thật; cần người phụ trách User deploy status và cung cấp cấu hình nội bộ đúng.
- Vòng cuối trên PostgreSQL tạm mới: **471 test pass, không skip**, gồm 47 case User gate và bộ hồi quy Document/Contract design/Notification/config/Gateway. Sáu warning deprecation từ test/dependency hiện có, không có test fail. Sửa test latest-attempt để chỉ nhận job của nó, tránh lấy nhầm retry job từ fixture khác; không đổi worker production để che lỗi.
- Build Document API/worker, Compose config, OpenAPI đầy đủ và cú pháp Python pass. Native Tesseract vie+eng + HTTP/JWT/SQL/profile/replay/ownership pass với ảnh giả và Storage/User adapter giả; không phải test User hay Supabase end-to-end.
- Database test tạm và dữ liệu giả được dọn sau kiểm thử. Không bật lại stack Document `backend-document-*`, không restart service nhóm hoặc ghi dữ liệu Supabase thật. Các stack cũ khác không nằm trong lượt thay đổi này.
- Bàn giao: `services/document-service/README_USER_GATE.md` giải thích rule, response mẫu mô phỏng, cấu hình, checklist và phụ thuộc User; `README_POSTMAN.md` hướng dẫn gọi Document bằng access token của User. Không gửi INTERNAL_TOKEN/secret cho FE/Postman/Git.
