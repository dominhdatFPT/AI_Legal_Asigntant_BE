# Test Document Service bằng Postman

Không cần Frontend để test Document. **Service đang tắt trên máy **; người chạy test phải bật backend trên máy của mình hoặc dùng môi trường test được nhóm cung cấp. `localhost` luôn là máy đang gửi request

## 1. Cần nhận những gì?

**Mới: User gate bắt buộc cho upload/OCR/profile PUT.** Xem [README_USER_GATE.md](README_USER_GATE.md). Backend cần USER_SERVICE_URL/INTERNAL_TOKEN khớp User, và User phải có GET /internal/v1/users/{user_id}/status. API status chưa thấy trong User code local/develop lúc kiểm tra. Nếu còn thiếu404, Document sẽ503 USER_SERVICE_UNAVAILABLE; không phải lỗi Supabase/OCR. can_create_dossier=false vì PROFILE_NOT_SUBMITTED vẫn cho chuẩn bị CCCD; ACCOUNT_LOCKED chặn403.

- Code backend phiên bản mới, gồm folder này, `libs/common_auth`, Gateway, Compose và `docker-compose.document-worker.yml`. Chỉ nhận riêng folder Document hoặc README sẽ không đủ build theo cấu hình monorepo.
- Docker Desktop và Postman. Gateway/User Service của nhóm đã chạy để đăng nhập lấy access token.
- Cấu hình riêng trong `backend/.env`, không lấy secret từ README hoặc commit `.env` lên Git.
- Tài khoản test và file giả: ưu tiên DOCX/PDF có chữ để thử đầu tiên; sau đó PNG/JPEG/PDF scan. Không dùng CCCD người khác hoặc gửi ảnh thật vào nhóm chat.

Các biến backend cần có: `DOCUMENT_DATABASE_URL` (TLS, Direct/Session port5432, không6543), `DOCUMENT_SUPABASE_URL`, `DOCUMENT_SUPABASE_SERVICE_ROLE_KEY`. Bucket mặc định `document-files`; `DOCUMENT_DATABASE_POOLER_HOST` chỉ thêm nếu cần Session pooler. `JWT_KEYS_DIR` phải trỏ tới bộ key của môi trường nhóm: Document chỉ mount jwt_public.pem; User Service cấp token bằng private key tương ứng. Không tự sinh một bộ key khác rồi dùng token cũ.

Project Document hiện tại là `srrxrnohunfyyolbfsew`, schema001/003/004/005 và private bucket đã setup. **Không chạy lại SQL tạo bảng trên project hiện có.** Nếu dùng project khác, người phụ trách backend setup theo [database/README.md](database/README.md) trước. Không dùng Supabase key, Firebase ID token hoặc INTERNAL_TOKEN thay access token Document.

## 2. Bật service

Mở terminal tại folder **backend**, không phải services/document-service:

```powershell
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml build document-service document-worker
docker compose up -d --no-deps document-service
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml up -d document-antivirus document-worker
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml ps
```

Gateway và User Service phải được nhóm bật/config đúng trên môi trường này. API/antivirus cần healthy, worker cần running. Antivirus khởi động lần đầu có thể cần chờ tải/nạp signatures. Không có worker thì upload vẫn trả202 nhưng queue không được xử lý. Luồng **hai ảnh CCCD** dùng Tesseract tại API, không cần worker Paddle; vẫn cần API/DB/Storage.

Không bật document-notification-worker khi test này: receiver Notification chưa implement, delivery mặc định tắt. Không restart toàn nhóm để thử Document. Kiểm tra DB/private Storage/schema chỉ đọc nếu máy host đã có dependencies Python của Document:

```powershell
python services/document-service/scripts/check_release.py
```

Health200 không thay readiness. Các lệnh trên build/start thật, không phải test giả lập.

## 3. Chuẩn bị Postman

Có thể Import file [openapi.yaml](openapi.yaml), chọn server Gateway `http://localhost:8080/api`. Hoặc tự tạo Collection theo các request bên dưới.

Tạo Environment riêng và chọn nó khi gửi request:

| Biến | Giá trị |
|---|---|
| base_url | http://localhost:8080 |
| access_token | Access token User Service cấp, giữ local/secret |
| upload_key | UUID cố định cho một thao tác upload |
| identity_key | UUID khác, cố định cho một phiên hai ảnh |
| document_id | Điền từ response upload |
| session_id | Điền từ response OCR CCCD |
| profile_revision | Điền từ GET/PUT profile, hoặc error.current_revision khi GET404 |

Collection Authorization: **Bearer Token**, Token=`{{access_token}}`; request health/login chọn No Auth. Không thêm chữ Bearer vào giá trị access_token. Gửi X-Request-ID nếu muốn đối chiếu lỗi, ví dụ `postman-document-001`.

Để sinh UUID một lần, dùng pre-request script trên request upload/CCCD:

```javascript
if (!pm.environment.get('upload_key')) {
  pm.environment.set('upload_key', pm.variables.replaceIn('{{$guid}}'));
}
if (!pm.environment.get('identity_key')) {
  pm.environment.set('identity_key', pm.variables.replaceIn('{{$guid}}'));
}
```

Giữ nguyên key khi retry cùng file/form. Muốn upload file khác hoặc tạo phiên khác thì xóa/đổi biến key tương ứng trước. **Không đặt trực tiếp {{$guid}} vào Idempotency-Key cho mỗi lần gửi**, vì retry sẽ thành thao tác mới.

### Lấy access token

Ưu tiên đăng nhập bằng luồng nhóm đang dùng rồi lấy **access_token của User Service**, không lấy Firebase ID token để gọi Document. Nếu có tài khoản password test hợp lệ:

```http
POST {{base_url}}/api/users/auth/login
Content-Type: application/json
```

Body raw/JSON, tự điền thông tin local, không chia sẻ password:

```json
{"username":"<tai-khoan-test>","password":"<mat-khau-test>"}
```

Nếu nhóm dùng Firebase thì POST `/api/users/auth/firebase`, body `{"id_token":"<Firebase ID token của tài khoản test>"}`. Cả hai trả access_token tại `data.access_token`. Không tự tạo user/token để bỏ qua đăng nhập.

Script sau response login200:

```javascript
if (pm.response.code === 200) {
  const body = pm.response.json();
  pm.expect(body.data.access_token).to.be.a('string');
  pm.environment.set('access_token', body.data.access_token);
}
```

Token hiện có TTL15 phút. TOKEN_EXPIRED401 thì đăng nhập/refresh qua User Service và cập nhật token. Để kiểm tra đúng chế độ Bearer hoặc khác user, xóa/disable cookie access_token cũ trong Postman; đừng vô tình dùng cookie tài khoản A khi đang test tài khoản B. Cookie write cần Origin được backend cho phép; các ví dụ này dùng Bearer để không phụ thuộc CSRF cookie.

## 4. Luồng file: upload → xử lý → xem/tải → xóa

### A. Health và token

- GET `{{base_url}}/api/documents/health`, No Auth:200, `{"status":"ok","service":"document-service"}`.
- GET `{{base_url}}/api/documents/me`, Bearer:200, `data.user_id`/`data.role` của mình. Không có token/cookie:401.

### B. Upload file

POST `{{base_url}}/api/documents/` — **giữ dấu / cuối**.

Header `Idempotency-Key: {{upload_key}}`. Body **form-data**:

| Key | Loại trong Postman | Ví dụ |
|---|---|---|
| file | File | Chọn mẫu DOCX hoặc PDF |
| purpose | Text | contract_template |
| document_type | Text | contract |

Không tự đặt Content-Type multipart: Postman phải tạo boundary. Không gửi thêm owner_user_id hoặc field ngoài ba field trên.

Giấy tờ khác: purpose=`supporting_document`, document_type=`other`; CCCD ảnh/PDF một file: supporting_document/identity_card. contract_template chỉ nhận DOCX/PDF; supporting_document nhận DOCX/PDF/JPEG/PNG nhưng loại identity_card không nhận DOCX. Giới hạn9MiB/file, PDF100trang, multipart10MiB; không DOCM/PDF mã hóa/ký số/file hỏng. Tên file không dùng đường dẫn kiểu C:\... hoặc ../.

Kỳ vọng202, `data.document_id`, `data.file_status=pending_check`, `data.processing_status=queued` lúc mới nhận.202 **không nghĩa OCR xong**. Script lưu ID sau response:

```javascript
pm.test('Upload accepted', () => pm.response.to.have.status(202));
if (pm.response.code === 202) {
  pm.environment.set('document_id', pm.response.json().data.document_id);
}
```

Gửi lại cùng key/bytes/purpose/type/filename: cùng ID. Cùng key nhưng đổi file hoặc filename:409 IDEMPOTENCY_CONFLICT. Một file mới phải dùng key mới.

### C. Danh sách, metadata và processing

- GET `{{base_url}}/api/documents/?page=1&page_size=20`:200, data.items và data.pagination. Có thể thêm purpose hoặc document_type.
- GET `{{base_url}}/api/documents/{{document_id}}`:200, metadata và trạng thái hiện tại; không có storage_key.
- GET `{{base_url}}/api/documents/{{document_id}}/processing`:200 ngay cả job failed. Gửi lại khoảng2–3 giây/lần tới completed/failed; không poll vô hạn. Xem data.error_code nếu failed.

Nếu completed: thường file_status=available. available+failed vẫn có thể tải file nhưng không có extraction thành công. rejected+failed bị chặn tải. Scan unavailable giữ pending/retry, không bỏ scan để demo.

### D. Kết quả chữ/OCR

GET `{{base_url}}/api/documents/{{document_id}}/extraction`:

- completed/available:200, data.text/pages/warnings/source_sha256/job_id.
- Chưa xong:409 EXTRACTION_NOT_READY; failed:409 EXTRACTION_FAILED; rejected:409 FILE_REJECTED.

DOCX pages=[]; PDF native text/scan/mixed theo trang, ảnh một trang. T6 extracted_fields=[] là đúng, không tự đoán CCCD/phone/email/giá tiền. PDF cùng trang text+raster có warning vùng ảnh chưa OCR. T6 dùng PaddleOCR; luồng hai ảnh CCCD bên dưới dùng Tesseract.

### E. Content và link download

- GET `{{base_url}}/api/documents/{{document_id}}/content`:200 **binary**, không JSON; dùng chức năng lưu response ra file trong Postman. Headers cần có Content-Disposition attachment, X-Content-Type-Options nosniff, X-Content-SHA256, Cache-Control no-store.
- GET `{{base_url}}/api/documents/{{document_id}}/download`:200 JSON, `data.download_url` và expires_at, link tối đa300 giây. Mở link bằng request riêng **No Auth**, không chuyển Bearer JWT sang Supabase; ai giữ link có thể tải trong TTL, không chia sẻ/log link.

Pending/rejected không được tải. Chưa thử DELETE nếu còn muốn xem/tải.

### F. Xóa file thử

DELETE `{{base_url}}/api/documents/{{document_id}}`:204, **body rỗng**. Gọi lại đúng chủ:204; đọc lại:404. Queued/processing:409 DOCUMENT_BUSY; nguồn profile đang dùng:409 PROFILE_SOURCE_IN_USE, cần unlink/thay profile trước.

Đây là soft-delete, không purge object/result/outbox hoặc snapshot Contract. Link đã cấp có thể còn dùng tới hết TTL. Test trên tài khoản/file thử, không xóa file đang dùng của người khác.

## 5. Luồng CCCD hai ảnh → user review → profile

### A. OCR hai mặt

POST `{{base_url}}/api/documents/identity-ocr`, Bearer, header `Idempotency-Key: {{identity_key}}`. Body form-data **chỉ hai field File**: front và back. JPEG/PNG<=4MiB/ảnh, hai ảnh khác nhau, rõ nét/đúng chiều; không gửi thêm file/purpose/type. Không tự đặt Content-Type. Đặt timeout Postman đủ chờ OCR, chẳng hạn120 giây; timeout client không chứng minh server đã hủy hoặc cho phép đổi key mù.

Thành công201: `data.session_id`, status=completed, fields, warnings, verification_status=`not_performed`. Lưu session_id:

```javascript
if (pm.response.code === 201) {
  pm.environment.set('session_id', pm.response.json().data.session_id);
}
```

GET `{{base_url}}/api/documents/identity-ocr/{{session_id}}`:200, chỉ chủ xem; không raw JSON/storage key. Ảnh trắng/không đọc đủ tên và số CCCD có thể422 OCR_UNREADABLE, không phải lỗi kết nối. Phiên lỗi có thể trả session_id trong error.details để đọc lại. Replay cùng key trả kết quả/lỗi cũ, không OCR lại; processing/crash409 cần đối soát. OCR không xác minh CCCD/KYC và không tự lưu profile.

OCR parser v3 giữ nguyên sáu trường trong `data.fields` để tương thích Document profile/Contract. Có thêm `data.additional_fields`: `sex` (`male`/`female`), `nationality`, `place_of_origin`, `expiry_date` (ISO date). Đây là gợi ý cho form User, có thể null và cần kiểm tra; không gửi chúng vào `fields` khi PUT Document identity-profile. FE hiện chỉ ánh xạ sáu trường cũ, cần thêm ánh xạ các trường bổ sung để tự điền giới tính/quốc tịch/quê quán/hạn thẻ. Không suy ra số điện thoại, email, dân tộc hoặc mã số thuế từ ảnh CCCD.

Engine thử ảnh gốc trước, bổ sung lượt phóng lớn/tăng tương phản và cách phân vùng chữ khác khi còn thiếu trường, trong cùng giới hạn 55 giây/hai mặt. Parser nối địa chỉ nhiều dòng và dùng độ tin cậy riêng của từ chứa ngày tháng. Khi các lượt bổ sung cho kết quả khác nhau, trường thiếu vẫn null; không đoán/sửa chữ số. Thử lại bản parser mới bằng yêu cầu có Idempotency-Key mới; phiên đã xử lý vẫn giữ nguyên kết quả cũ.

### B. Đọc revision trước khi lưu

GET `{{base_url}}/api/documents/identity-profile/me`:

- 200: lấy data.revision làm profile_revision.
- 404 IDENTITY_PROFILE_NOT_FOUND: lấy error.current_revision. Chưa từng có profile thì0; đã unlink thì revision vẫn tăng, **không reset về0**.

Script sau response:

```javascript
const body = pm.response.json();
if (pm.response.code === 200) {
  pm.environment.set('profile_revision', body.data.revision);
} else if (pm.response.code === 404 && body.error.code === 'IDENTITY_PROFILE_NOT_FOUND') {
  pm.environment.set('profile_revision', body.error.current_revision);
}
```

### C. User xác nhận/sửa sáu field rồi lưu

PUT `{{base_url}}/api/documents/identity-profile/me`, Body raw/JSON, Content-Type application/json. Ví dụ dưới đây là dữ liệu giả: **thay bằng thông tin đã review tương ứng phiên test**, không copy đè profile thật:

```json
{
  "source_session_id": "{{session_id}}",
  "expected_revision": {{profile_revision}},
  "fields": {
    "full_name": "NGUYỄN VĂN AN",
    "date_of_birth": "2000-01-01",
    "identity_number": "000000000001",
    "issue_date": null,
    "issue_place": null,
    "permanent_address": null
  }
}
```

Gửi đủ sáu key, thiếu dữ liệu dùng null, không chuỗi rỗng; ít nhất một field được xác nhận. identity_number là **string12 số**, ngày YYYY-MM-DD, không ngày tương lai. expected_revision là số, không có dấu nháy. Chọn đúng một nguồn: source_session_id từ phiên completed, hoặc source_document_id từ file identity_card/supporting_document available+latest completed; không gửi cả hai.

Kỳ vọng200, data.fields và revision mới. Lưu data.revision vào profile_revision. Gửi lại revision cũ:409 REVISION_CONFLICT, GET lại trước khi review/lưu tiếp. PUT là thay bộ sáu field, không PATCH; không tự chấp nhận OCR thay user.

### D. Xem/gỡ profile thử

GET `/api/documents/identity-profile/me` trả200 sau lưu. DELETE `{{base_url}}/api/documents/identity-profile/me?expected_revision={{profile_revision}}` trả204 body rỗng. GET sau unlink404 có error.current_revision mới. Unlink không purge ảnh/JSON và không cập nhật User KYC hay hợp đồng cũ.

## 6. Các test âm nên thử

Thêm test User gate: dùng tài khoản khóa với token cũ còn hạn → upload/OCR/PUT profile403 ACCOUNT_LOCKED; tài khoản chưa nộp hồ sơ vẫn đi tiếp ba luồng nếu input hợp lệ; User status thiếu/lỗi/token nội bộ sai →503 trước side effect. Postman **không** gửi X-Internal-Token, Document backend tự gửi. Profile Document không tự cập nhật trạng thái nộp Citizen Profile của User.

| Tình huống | Kỳ vọng |
|---|---|
| Không Bearer và không cookie /me |401|
| Token hết hạn |401 TOKEN_EXPIRED|
| User B GET/content/delete file/session của A |404, không lộ chủ file|
| Admin không phải chủ xem file A |404, không bypass|
| Upload thiếu/sai UUID Idempotency-Key |422|
| Cùng key nhưng đổi bytes/filename |409|
| Sai MIME/loại file |415 hoặc422 theo loại lỗi|
| File/body vượt giới hạn |413|
| Extraction khi đang queued/processing |409|
| Xóa khi job active |409 DOCUMENT_BUSY|
| Profile revision cũ |409 REVISION_CONFLICT|
| Profile gửi hai nguồn hoặc field ngoài schema |422|

Không cần gửi malware thật/EICAR lên Supabase để thử; antivirus đã có bài test riêng trên DB/Storage tổng hợp.

## 7. Lỗi thường gặp và báo kết quả

- Connection refused/502: kiểm tra Docker/Gateway/Document đúng máy và trạng thái service; không đổi sang localhost8000 vì Compose không publish port Document riêng.
- Queued mãi: worker hoặc antivirus chưa chạy, hoặc worker không truy cập DB/Storage; kiểm tra trạng thái và readiness, không tự sửa DB thành completed.
-401: kiểm tra access token User Service, cùng bộ key, hết hạn/cookie cũ; không dùng Firebase/Supabase token.
-403 khi cookie write: Origin/CSRF; dùng Bearer đúng hoặc cấu hình Origin theo nhóm, không tắt bảo vệ.
- CONFIGURATION_ERROR/DATABASE_UNAVAILABLE/STORAGE_*: người phụ trách backend kiểm tra .env/project/TLS/bucket/quyền; không gửi key/DSN/password vào chat.
- OCR_UNREADABLE/field null: ảnh/model chưa đọc đủ, cần user review; chưa khẳng định độ chính xác trên CCCD thực tế.

Response nghiệp vụ JSON dùng `{data,error,request_id}`; health là ngoại lệ, /content là binary, DELETE204 không JSON. Khi báo lỗi gửi method/path/status/error.code/request_id và mô tả bước tái hiện. Che token/CCCD/text OCR/tên thật/link signed/secret trong ảnh chụp hoặc export Postman; không export Environment có access_token/password lên Git hoặc chia sẻ public.

**Phạm vi test hiện tại:**15 API Document; không kiểm tra Contract import/sinh hợp đồng hoặc popup Notification vì hai phần receiver/import vẫn chưa triển khai. T7 đã lưu outbox nhưng chưa gửi event thật. Trước bàn giao có424 test backend pass/không skip, native synthetic smoke pass; đây vẫn không thay lần test Postman thật của nhóm.

Test tạo dữ liệu trên Supabase thật, không phải sandbox tự dọn. Dùng tài khoản test riêng; file/session/ảnh/JSON có thể được giữ lại, DELETE/unlink chỉ xóa mềm. Không thử hàng loạt bằng dữ liệu cá nhân. Dọn vật lý chỉ theo chính sách maintenance được nhóm chốt.

Xong thử thì từ backend tắt riêng Document, không down toàn nhóm:

```powershell
docker compose -f docker-compose.yml -f docker-compose.document-worker.yml stop -t 30 document-worker document-service document-antivirus
```

### OCR chữ tiếng Việt bổ sung (parser v16)

Parser v17 chọn lượt có nhãn sử dụng được thay vì lượt nhiều mảnh nền nhất; giá trị số CCCD có điểm từ 90% không bị loại chỉ vì nhãn song ngữ yếu. Vùng ngày sinh được đọc lại riêng bởi Tesseract/VietOCR, không tự dựng ngày từ MRZ. Nếu lượt crop đạt ngưỡng và trùng ngày trên MRZ có checksum hợp lệ, API trả ngày được đối chiếu trong `fields.date_of_birth` và warning `OCR_BIRTH_RECONCILED_WITH_MRZ`.

V17 bổ sung crop theo nhãn ở nhiều lượt phân vùng, gồm ngày cấp, hạn thẻ, giới tính, quốc tịch và các địa chỉ còn thiếu. Nhãn Tesseract yếu chỉ dùng định vị ảnh; không được coi là giá trị tin cậy. Giá trị VietOCR vẫn phải đạt 75% và đúng kiểu dữ liệu (ngày hợp lệ, giới tính hợp lệ); không cắt bỏ cuối địa chỉ khi hết ngân sách crop. `completed`/`matched` không có nghĩa mọi trường đã được đọc, không phải xác thực giấy tờ thật. Kiểm thử ảnh thực tế vẫn có trường null; không cam kết OCR chính xác 100%. FE cần cho sửa/xác nhận trước khi lưu. Số điện thoại, dân tộc, mã số thuế và email không được suy ra từ ảnh CCCD.

Kiểm thử v17 trên 5 cặp ảnh đã lưu (chỉ đọc, không ghi đè hồ sơ): cả 5 đối chiếu được hai mặt; 3 cặp trả đủ 10 trường form, 1 cặp 8/10, 1 cặp 6/10. Cặp 8/10 thiếu ngày sinh/nơi cấp; cặp 6/10 thiếu thường trú/quê quán/quốc tịch/hạn thẻ. Thời gian khoảng 15–25 giây/cặp. Đây là mức đầy đủ dữ liệu, **không phải phép đo độ chính xác 100% hay chứng nhận mọi dấu tiếng Việt đúng**. Bộ test hồi quy: 135 passed, 16 skipped (test tích hợp có điều kiện môi trường). Lượt lọc mực tối và crop cơ quan cấp vẫn dùng chữ thật/điểm thật; không thêm mặc định khi không đọc được.

V18 sửa crop dòng tiếp theo bị hoa văn chen vào, tách các lượt crop độc lập thay vì nối thành địa chỉ kép, loại crop chỉ chứa nhãn "Quê quán / Place..." và không nhận "CỤC CẢNH SÁT" đứng riêng là tên cơ quan cấp đầy đủ. Với thẻ cũ đã cắt sát, nhận diện bằng cả nhãn quê quán và thường trú, bổ sung vùng theo bố cục in cho quốc tịch/quê quán/hạn thẻ và hai dòng thường trú. Đây là tọa độ bố cục, không phải dữ liệu cá nhân gán sẵn; không áp dụng cho mặt sau, thẻ mẫu mới hoặc ảnh dọc. Giá trị vẫn do VietOCR đọc và phải đạt ngưỡng. Cặp trước chỉ trả 6 trường nay trả đủ 10 trường, nơi cấp khớp đầy đủ tên cơ quan; đối chiếu vẫn thấy khác dấu ở địa chỉ nên chưa bảo đảm mọi chữ chính xác. Bộ test v18: 142 passed, 16 skipped. Bấm OCR với Idempotency-Key mới; kết quả phiên cũ không tự đổi sau khi build.

V19 sửa mất dòng ấp/thôn/số nhà sau nhãn Nơi cư trú trên mặt sau mẫu mới khi Tesseract đọc hỏng nhãn song ngữ. Fallback chỉ dùng khi nhãn ngày cấp/hạn thẻ và tỷ lệ ảnh nhận diện bố cục được hỗ trợ; dò mực để mở rộng tới địa chỉ dài, giữ khoảng trắng quanh chữ để tránh sinh từ giả. Cả dòng chi tiết và dòng địa giới phải đạt ngưỡng trước khi nối. Nếu một lượt chỉ đọc địa giới, chỉ dùng lượt đủ hai dòng khi phần địa giới thống nhất; khác chữ vẫn phải review. Không lấy nơi khai sinh để bù chi tiết cư trú, không chèn địa danh từ từ điển hay gán sẵn giá trị cá nhân. Ca mất dòng được so sánh chính xác cả dấu với ảnh người dùng cung cấp. Bộ test v19: 145 passed, 16 skipped; không bảo đảm chính xác mọi ảnh/mẫu trong tương lai. Tạo yêu cầu OCR mới để dùng v19, phiên cũ không tự đổi.

V20 thay quy tắc nối dòng: chỉ thêm khoảng trắng, không tạo dấu phẩy tại chỗ xuống dòng và không xóa dấu phẩy đã đọc. FE vẫn tách trước dấu phẩy đầu/sau dấu phẩy cuối; phần chi tiết có thể gồm cả tên xã nếu đó là cách in trên thẻ. Quy tắc này thay các ngoại lệ dấu phẩy ở các phiên bản trước. Crop dòng thường trú thẻ cũ được mở thêm phần dưới để giữ dấu thấp/descender; không sửa tên địa danh bằng bảng thay từ. Hai ca người dùng báo đã kiểm tra trực tiếp: đúng vị trí dấu phẩy gốc và đọc đúng dấu nặng còn thiếu. Test hồi quy v20: 146 passed, 16 skipped; hàm FE tách ba ô đúng theo dấu phẩy gốc. Tạo yêu cầu OCR mới; dữ liệu phiên cũ không được ghi đè.

V21 sửa hai lỗi trong luồng OCR: token Tesseract có vùng chữ rộng/cao bằng 0 được bỏ qua, không làm mất các dòng hợp lệ hoặc gây `OCR_BAD_RESPONSE` cho cả ảnh. Vùng âm/vượt ảnh, điểm tin cậy không hợp lệ và giới hạn số dòng/dung lượng vẫn bị chặn. Khi lượt MRZ toàn vùng chưa đọc đủ, dùng vị trí các dòng phát hiện trên chính ảnh đó (có quy đổi offset/tỷ lệ crop), đọc lại một batch ba dòng và lọc nền/ánh sáng cục bộ để giữ nét dấu `<`. Tối đa hai biến thể trong giới hạn 55 giây; không chèn ký tự đệm, sửa chữ/số, ghép kết quả phiên khác hoặc lấy thông tin mặt trước để dựng MRZ. Giữ nguyên toàn bộ checksum và đối chiếu số/tên/ngày giữa hai mặt.

Kiểm thử v21: 174 passed, 16 skipped (các test tích hợp thiếu cấu hình môi trường). Chạy đọc lại ảnh đã lưu, không tạo phiên/ghi đè hồ sơ: cặp ảnh mới nhất trước lỗi `IDENTITY_PAIR_UNVERIFIABLE` nay đối chiếu được hai mặt, khoảng 25 giây, có 9/10 trường form (giới tính vẫn null). Ba cặp từng thành công vẫn đối chiếu được và có đủ 10 trường. Một cặp khác trước lỗi `OCR_BAD_RESPONSE` nay đọc tiếp được nhưng MRZ vẫn chưa đủ để đối chiếu, nên tiếp tục trả `IDENTITY_PAIR_UNVERIFIABLE`, không cho qua giả. Đây không phải cam kết đúng 100% trên mọi ảnh hoặc chứng nhận thẻ thật. Document container đã build lại v21; tạo yêu cầu OCR mới với Idempotency-Key mới, kết quả phiên cũ không tự cập nhật.

V22 sửa trường hợp ảnh ngang nhưng chưa cắt đúng vùng thẻ: tọa độ mẫu cố định chỉ đọc được số nhà/tên đường, mất dòng địa giới. Khi nhận được nhãn cư trú và vị trí giá trị trên cùng ảnh, crop hai dòng theo nhãn, mở rộng bên trái để giữ từ đầu Tesseract bỏ sót và đọc tiếng Việt ở tỷ lệ phù hợp. Chỉ nối khi cả dòng chi tiết và dòng địa giới đạt ngưỡng; một dòng chi tiết đơn lẻ không được đánh dấu là địa chỉ đầy đủ. Không lấy địa chỉ khai sinh để bù nơi cư trú.

API bổ sung trường **tùy chọn** `additional_fields.residence_address_components = {detail, ward, province}` khi hai dòng cư trú đã đọc đầy đủ, các lượt đọc thống nhất và khớp chuỗi `residence_address` được chọn. FE mẫu mới ưu tiên ánh xạ này; một dấu phẩy giữa số nhà và tên đường trong dòng chi tiết không biến tên đường thành tỉnh/thành phố. Chuỗi địa chỉ OCR gốc vẫn giữ dấu phẩy; quy tắc v20 với địa chỉ cũ không đổi. Không gửi object bổ sung này vào sáu field Document identity-profile. Nếu API không trả components và chỉ có một/hai phần, FE giữ nguyên đoạn đọc được ở ô chi tiết, để trống xã/tỉnh và nhắc kiểm tra, không gán phần cuối thành tỉnh. Không có dữ liệu cá nhân/địa danh gán cứng trong thuật toán.

Kiểm thử v22: 186 test BE passed, 16 skipped; 5 test ánh xạ FE passed và kiểm tra cú pháp FE passed. Đọc lại đúng cặp ảnh báo lỗi: ba phần địa chỉ khớp cả chữ/dấu và hai mặt vẫn matched, khoảng 24 giây (giới tính vẫn null). Hai cặp thẻ cũ/mới từng đọc đúng vẫn matched, đủ 10 trường và giữ đúng dấu phẩy/dấu địa chỉ đã kiểm tra trước đó. Đọc kiểm tra không tạo phiên mới hoặc ghi đè hồ sơ. Document container đã build lại v22; tải lại FE và tạo yêu cầu OCR mới, không dùng kết quả phiên cũ. Đây không phải cam kết mọi ảnh sẽ chính xác 100%.

Nếu tên toàn ảnh không thống nhất, chỉ nhận tên từ crop mặt trước đạt ngưỡng và khớp tên MRZ, trả warning `OCR_NAME_RECONCILED_WITH_MRZ`; không lấy tên từ MRZ khi crop chưa đọc được. Tên/số/ngày sinh in mặt trước không bị chữ nền hoặc tên người ký ở mặt sau ghi đè. Dòng tên MRZ có chữ chen sau chuỗi ký tự đệm bị loại và đọc lại; không sửa ký tự để ép checksum. Ảnh dọc trên nền bàn trung tính được thử tìm vùng thẻ theo màu/hình dạng; đây là heuristic bảo thủ, không bảo đảm mọi nền/ảnh nghiêng đều tìm được. Các trường chưa đọc chắc vẫn null và người dùng nhập tay.

Docker cài model MRZ best từ [DoubangoTelecom/tesseractMRZ](https://github.com/DoubangoTelecom/tesseractMRZ), giấy phép BSD-3-Clause lưu tại `/opt/mrz/LICENSE`. Revision `1e7adfecda5f3c9ae1fb12cf6b4b8c3958c63e46`, SHA-256 `e44f5b7a6bdd3f382ef3bfa84ee0057f5897946a84a094c26910e0a124f3a9bd`. Tải và kiểm tra model khi build, không tải trong request và không gửi ảnh ra dịch vụ ngoài. Fallback tìm các dòng MRZ trên ảnh đã lọc nền, đọc dòng tên bằng model MRZ, vẫn yêu cầu đủ checksum và khớp thông tin hai mặt. Đây không phải xác thực thật/giả.

Docker Document cài sẵn VietOCR `0.3.13`, model CPU `vgg_seq2seq`, PyTorch CPU và trọng số khoảng 90 MB từ nguồn chính thức. Model được tải khi build, cố định SHA-256/config revision và kiểm tra hash trước khi đọc; không tải model, pretrained backbone hoặc cấu hình qua mạng trong request. Không cần train hoặc key eKYC.

`DOCUMENT_VIETOCR_ENABLED=1` (mặc định trong Compose) bật lượt đọc riêng tên, địa chỉ, quê quán, quốc tịch/cơ quan cấp khi tìm được vùng chữ. `DOCUMENT_VIETOCR_MODEL_DIR=/opt/vietocr`. Cấu hình `0` chỉ dùng luồng Tesseract hiện có. Tesseract tiếp tục phát hiện dòng và đọc số/ngày/MRZ; VietOCR bổ sung chữ tiếng Việt. Khi hai bộ đọc thống nhất chữ không dấu, ưu tiên dấu/cách viết VietOCR; khi chữ thực sự khác nhau, giữ kết quả đã có hoặc để người dùng kiểm tra, không đoán/sửa số CCCD. Điểm mô hình là tín hiệu lọc, không phải xác suất giấy tờ chính xác/thật.

VietOCR chạy trong subprocess CPU, không nhận JWT/key/DSN, chỉ nhận crop qua stdin và trả JSON. Giữ giới hạn 55 giây/hai mặt; timeout kill/reap child. Raw private lưu model và vùng được đọc; API vẫn chỉ trả các trường gợi ý hiện có. Lần build đầu tải thêm thư viện CPU/model; lần sau dùng cache. Tham khảo [VietOCR chính thức](https://github.com/pbcquoc/vietocr).

### Hai bố cục thẻ (parser v14)

Với các crop riêng của mẫu mới, parser v14 phân biệt dòng số nhà/đường ngay sau nhãn Nơi cư trú với dòng địa giới bên dưới. Dấu phẩy sau số nhà trong cùng crop được chuẩn hóa thành khoảng trắng (ví dụ `25, Đường A` → `25 Đường A`); ranh giới giữa hai thành phần được biểu diễn bằng dấu phẩy để FE tách đúng. Quy tắc này không áp dụng cho xuống dòng thông thường của địa chỉ CCCD cũ.

Hỗ trợ nhãn/bố cục CCCD gắn chip cũ và thẻ căn cước mới: nhãn dài, số thứ tự trước nhãn, ngày tháng và giới tính/quốc tịch ở dòng dưới. Với thẻ mới, `additional_fields.residence_address` giữ nghĩa Nơi cư trú, `registered_birth_place` giữ nghĩa Nơi đăng ký khai sinh. Không tự sao chép chúng thành `permanent_address` hoặc `place_of_origin`: hai trường đó có thể không in trên thẻ mới. FE cần form phù hợp hoặc người dùng nhập bổ sung thông tin hồ sơ.

Các lượt đọc vùng riêng cho mẫu mới áp dụng ảnh thẻ ngang đã cắt sát, được nhận diện bằng nhãn ngày cấp và hạn thẻ ở mặt sau. Trường có độ tin cậy thấp vẫn null; không hạ ngưỡng để điền sai địa chỉ. Bộ nhận diện chưa bảo đảm mọi ảnh/thẻ đều đọc đủ trường.

Địa chỉ mẫu mới đọc cả phần số nhà/đường sau nhãn Nơi cư trú và dòng địa giới phía dưới. Nếu lượt đầu còn yếu, thử ngưỡng lọc nền thứ hai trong cùng giới hạn thời gian; vẫn giữ ngưỡng chấp nhận 75%. Theo lựa chọn của nhóm ở FE, khi thẻ mới không có trường cũ, form điền địa chỉ từ `residence_address` và quê quán từ `registered_birth_place` để người dùng sửa/xác nhận trước khi lưu. BE vẫn trả hai trường theo đúng tên nguồn, không đổi ý nghĩa trong API. FE giữ số nhà/đường ở ô chi tiết, xã/phường cùng quận/huyện ở ô giữa, tỉnh/thành phố ở ô cuối; không biến quận/huyện thành số nhà.

Parser v13 không ưu tiên dấu VietOCR chỉ vì hai chuỗi giống nhau khi bỏ dấu. Địa chỉ vùng Tesseract mạnh được giữ dấu đã đọc, VietOCR bổ sung từ còn thiếu dấu. Xuống dòng được nối bằng khoảng trắng, không tự thêm dấu phẩy. FE tách phần trước dấu phẩy đầu thành chi tiết, sau dấu phẩy cuối thành tỉnh/thành phố, toàn bộ phần giữa thành xã/phường. Tên có thể nhận lượt VietOCR cùng chuỗi chữ với điểm ít nhất 88%. Nếu hai địa chỉ in độc lập có cùng ba thành phần địa giới khi bỏ dấu, dùng dấu của lượt vùng mạnh hơn cho phần địa giới chung, không sao chép số nhà giữa các trường. Các quy tắc này không bảo đảm đúng mọi tên riêng: người dùng vẫn phải kiểm tra trước khi lưu.

### Đối chiếu hai mặt CCCD (parser v6 trở lên)

BE chỉ hoàn tất phiên OCR mới sau khi đọc được ba dòng MRZ mặt sau theo bố cục CCCD gắn chip `IDVNM` đang hỗ trợ, vượt qua checksum số giấy tờ/ngày sinh/hạn thẻ/tổng, rồi đối chiếu số CCCD 12 chữ số và các trường đọc được ở mặt trước. Không thay O/Q thành 0 hoặc ghép các dòng từ các lượt OCR khác nhau để ép checksum đúng.

- Cùng thẻ, thông tin khớp: `data.card_match_status=matched`.
- Khác số, tên, ngày sinh hoặc hạn thẻ/giới tính đã đọc: 422 `IDENTITY_PAIR_MISMATCH`.
- Nếu một lượt OCR hạn thẻ đọc sai nhưng có lượt đọc trực tiếp khác trên mặt trước khớp với MRZ có checksum hợp lệ, dùng ngày được hai nguồn đối chiếu và thêm warning `OCR_EXPIRY_RECONCILED_WITH_MRZ`. Ngày sửa được trả về trong `additional_fields.expiry_date`; không bỏ qua số CCCD lệch hoặc tự dựng ngày chỉ từ MRZ.
- MRZ thiếu, sai checksum hoặc bố cục chưa hỗ trợ: 422 `IDENTITY_PAIR_UNVERIFIABLE`; chọn lại ảnh mặt sau rõ ba dòng MRZ. Không tự bỏ qua kiểm tra.
- Phiên đã có trước tính năng này: `card_match_status=not_checked`; thử bằng yêu cầu mới với Idempotency-Key mới. Kết quả phiên cũ không tự đổi.

`verification_status` vẫn là `not_performed`: đối chiếu OCR không xác minh thẻ thật/giả, chữ ký chip, ảnh chân dung hay danh tính người gửi. FE hiện đã hiển thị `error.message`, nên lỗi kiểm tra hiển thị trực tiếp mà không cần sửa FE. Định dạng TD1/checksum tham khảo [ICAO Doc 9303 Part 5](https://www.icao.int/publications/Documents/9303_p5_cons_en.pdf).

Tham khảo: [OpenAPI](openapi.yaml), [bàn giao T7/T8](../../docs/api/DOCUMENT_RELEASE.md), [luồng FE](../../docs/api/FRONTEND_DOCUMENT_CONTRACT_NOTIFICATION.md).
