# Kho hợp đồng từ Drive

Ngày 11/10/2026. Đã kiểm kê danh sách 30 file trong Drive và đọc trực tiếp bảy file liên quan hợp đồng/văn bản pháp lý. Bộ chuẩn bị này gồm **ba hợp đồng**; không có nghĩa toàn bộ 30 file đã được chuyển đổi hay kiểm tra nội dung. Các đơn/tờ khai, văn bản thừa kế và giấy ủy quyền chưa được tự publish như hợp đồng.

## Trạng thái trên Contract Supabase

Project `dvpqrwvfuiujvvnccpox`, bucket private `contract-files`. Không sửa file Drive. Không tạo nháp user hay job giả trên cloud.

| Mẫu | Trường | Version ID | Trạng thái |
| --- | ---: | --- | --- |
| Thuê mặt bằng kinh doanh | 27 | `da916549-3cc9-5fee-acdb-04a66d2ae6c5` | Published, hiện trong catalog user |
| Tặng cho quyền sử dụng đất | 78 | `08b6018b-b50d-588f-b6f3-3bb3ffcdc17f` | Draft/ẩn; nội dung và layout chưa đạt gate |
| Thế chấp quyền sử dụng đất | 83 | `9a505da6-cefe-51f5-b44f-4ccc5070f6d9` | Draft/ẩn; nội dung và layout chưa đạt gate |

Mẫu thuê có template ID `ff94ef6b-c507-5ca0-8fac-2bc1876e8d45`. Hai mẫu đất đai không cho publish qua API dù admin gọi trực tiếp; cần chuẩn bị một phiên bản mới sau khi nhóm duyệt [đề xuất sửa](templates/DE_XUAT_SUA_MAU_DAT_DAI.md).

Không coi file “Giay_Uy_Quyen_Kinh_Doanh.docx” là ủy quyền kinh doanh: nguồn thực tế là ủy quyền BHXH. “Mẫu đề nghị hợp tác.docx” thực tế là giấy đăng ký chi nhánh/văn phòng/địa điểm kinh doanh của hợp tác xã. Hai văn bản thừa kế có giả định người thừa kế/tỷ lệ di sản riêng, chưa tự biến thành hợp đồng tổng quát.

## Luồng FE và Postman

Base qua Gateway: `http://localhost:8080/api/contracts`. Nếu chạy Compose kèm `docker-compose.jwt-local.yml`, gọi trực tiếp `http://127.0.0.1:8006/api/v1/contracts`; Compose mặc định không publish cổng Contract ra host. Chỉ dùng `http://localhost:8000/api/v1/contracts` khi tự chạy uvicorn trên cổng 8000. Bearer lấy từ đăng nhập User; không dùng key Supabase làm access token. User `/internal/v1/users/{id}/status` và INTERNAL_TOKEN cùng nhóm vẫn cần hoạt động khi tạo nháp.

1. `GET {{base_url}}/template-catalog`: lấy mẫu visible/published, không dùng endpoint `/templates` demo.
2. `GET {{base_url}}/template-catalog/{{template_id}}/versions`, hoặc `GET {{base_url}}/template-versions/{{version_id}}`.
3. FE dựng form từ `data.fields` (name/label/type/required); nếu tên field có trong `data.form_options`, dựng dropdown từ lựa chọn trả về. Metadata nội bộ và Storage key không được đưa ra API.
4. `POST {{base_url}}/drafts`: chọn version thuê ở trên, lấy owner từ JWT, không gửi user_id. Để `procedure_id` null/không truyền nếu không dùng checklist; phần xuất cuối gắn thủ tục vẫn chưa được implement.
5. Sửa/lưu field_values bằng API nháp. `POST /drafts/{id}/preview` không body, header Idempotency-Key UUID mới, trả job PDF; xem trước chưa đủ required vẫn được phép.
6. `POST /drafts/{id}/generations`, body `{"format":"docx"}` hoặc `{"format":"pdf"}`, Idempotency-Key UUID mới. Final bắt buộc điền đủ 27 trường và hợp lệ các ràng buộc mẫu thuê.
7. Theo dõi `/generations/{id}` rồi lấy `/generations/{id}/download`. Signed URL chỉ 300 giây, không lưu vào Git/log/Postman export.

Ví dụ field_values **chỉ là dữ liệu giả**, không dùng để ký:

```json
{
  "f01": 11, "f02": 10, "f03": 2026, "f04": "Thành phố Mẫu",
  "f05": "Nguyễn Văn Mẫu A", "f06": "GIẤY-TỜ-MẪU-A",
  "f07": "2025-01-01", "f08": "Cơ quan cấp mẫu",
  "f09": "12 Đường Mẫu, Phường Mẫu", "f10": "ĐIỆN-THOẠI-MẪU-A",
  "f11": "Nguyễn Thị Mẫu B", "f12": "GIẤY-TỜ-MẪU-B",
  "f13": "34 Đường Thử, Phường Thử", "f14": "ĐIỆN-THOẠI-MẪU-B",
  "f15": "56 Đường Mẫu, Phường Mẫu", "f16": "Kinh doanh thử nghiệm",
  "f17": "12", "unit": "tháng", "f18": "2026-11-01", "f19": "2027-10-31",
  "f20": 10000000, "f21": "Mười triệu đồng", "pay": "chuyển khoản",
  "f22": 5, "f23": 20000000, "f24": 2, "f25": 1
}
```

Body tạo nháp bọc field_values này cùng template_version_id và title; không gửi object trên như một payload cấp ngoài.

Ngày nhập dạng ISO; xuất hiển thị dd/MM/yyyy. Tiền nhập number nguyên không âm, xuất có dấu phân nhóm VND. `f17` là chuỗi số nguyên dương không kèm đơn vị. `unit` chỉ tháng/năm; `pay` chỉ tiền mặt/chuyển khoản. Kiểm tra ngày ký có thật, ngày kết thúc không trước bắt đầu, ngày cấp không sau ký, ngày thanh toán 1–31, số bản tổng = hai lần số bản mỗi bên. User vẫn phải xác nhận tiền bằng số/bằng chữ, thời hạn so với các ngày và tính đúng của thỏa thuận; chưa có engine thẩm định điều khoản hoặc quy đổi tiền bằng chữ tự động.

Các field identity_key vẫn null: không tự điền CCCD chủ tài khoản cho cả hai bên. Điền/copy thông tin đúng người và kiểm tra lại trước xuất.

## Renderer và triển khai

Luồng mới chỉ nhận snapshot DOCX kho chung do script đã review đăng ký, không biến API C4 thành upload file tùy ý. Snapshot bất biến/hash; worker kiểm tra key thuộc chính template/version, không thuộc owner user như C8. Kết quả vẫn đặt trong prefix owner của job; auth/owner/idempotency/lease C5–C7 giữ nguyên.

Giữ native paragraphs, tables, styles, footer và các package part không sửa; chỉ thay đúng marker, không Jinja/eval. System snapshot cho phép Word reflow tự nhiên, có kiểm tra source/mapping/size/PDF 1–100 trang; không hứa mọi dữ liệu dài hay mọi font có bố cục giống Word desktop. Mẫu riêng C8 vẫn giữ guard width/page-count riêng, không được nới theo mẫu system. Preview có nhãn chưa hoàn thiện; không tạo chữ ký/dấu/lời chứng công chứng.

**Cần rebuild API và worker** để dùng snapshot system/form_options mới:

```powershell
docker compose --profile contract-worker up -d --build contract-service contract-worker
```

Lệnh này chưa được chạy để bật production trong lượt chuẩn bị mẫu. User/Gateway cần chạy riêng theo nhóm. Không migration mới; dùng 001+002 hiện có. API trả 202 mà worker tắt thì chưa có file. Chưa test E2E đăng nhập/User-status/Gateway/FE trên máy nhóm.

## Tái đăng ký có kiểm soát

Các script register/prepare/check và file nguồn bên dưới là công cụ local, không nằm trong nhánh bàn giao gọn `HIEN/contract_service`. API/worker dùng snapshot đang có trên Supabase, không đọc các file này lúc chạy. Nếu nhóm cần triển khai lại catalog trên một project mới, cần bàn giao riêng bộ nguồn/mapping đã duyệt trước khi đăng ký; không dùng trạng thái QA cũ cho file thay đổi.

`scripts/register_drive_contracts.py --project-ref dvpqrwvfuiujvvnccpox` chỉ kiểm tra read-only prerequisites. `--apply` đăng ký các snapshot đúng hash/mapping; thêm `--publish-safe` chỉ có thể publish mẫu thuê đã đạt gate. Hai mẫu đất đai luôn hidden/draft ở phiên bản này. Replay kiểm tra lại DB/Storage, không upload trùng hoặc ghi đè version, cũng không tự hiện lại mẫu đã bị admin ẩn.

Script chỉ nạp bốn biến CONTRACT từ backend/.env, kiểm tra đúng project/TLS/bucket private. Không fallback Document/User, không xuất exception chứa secret, không lưu JWT. Source cloud là DOCX chưa điền; ảnh QA và dữ liệu thử ở `.local-c2/` ignored. File gốc không đổi. Nếu commit DB không rõ kết quả, giữ snapshot để đối soát, không xóa mù; retention/orphan sweep vẫn là phần vận hành còn lại.

Tái tạo kỹ thuật cần bản gốc đúng hash trong `.local-c2/drive-originals`, DOCX chuyển đổi thế chấp trong `.local-c2/drive-converted`; chạy prepare_drive_contracts.py bằng bundled Python, rồi check_drive_contracts.py trong image server cô lập. Không tự tái sử dụng visual_qa_passed cho bytes/renderer/mapping đã thay đổi.

## Bằng chứng QA

### Chạy API thật trên máy local — 11/10/2026

Contract API 0.8.1 đã được bật/recreate riêng, không thay User Service hay các service khác. 8 HTTP check đạt 200: health, ready, catalog và version thuê qua cả cổng localhost 8006 lẫn Gateway 8080. Catalog có mẫu thuê; version trả đủ 27 trường và hai dropdown `unit`/`pay`. Kiểm tra chỉ đọc, dùng JWT dev ký trong RAM; không phải đăng nhập tài khoản thật, không tạo cloud user/draft/job.

Luồng tạo nháp/xuất file còn bị chặn: User Service đang chạy không có `/internal/v1/users/{user_id}/status` (HTTP 404), không có citizen-profile trong OpenAPI và chưa cấu hình INTERNAL_TOKEN. Contract UserGate trả đúng 503 USER_SERVICE_UNAVAILABLE, không bỏ guard. Cần nhóm chạy bản User có endpoint và cùng INTERNAL_TOKEN, rồi cung cấp tài khoản test/hồ sơ hợp lệ để thử create → preview → final → download. Worker chưa bật để tránh xử lý job khác khi chưa có nháp thử.

JWT_KEYS_DIR trong .env đang trỏ đường dẫn máy cũ; lượt chạy đã override biến này trong phiên PowerShell tới thư mục `.keys/keys` hiện có (cùng public key User đang mount). Không đổi nội dung key hay secret .env. Khi chạy lại trên máy này, đặt `$env:JWT_KEYS_DIR=(Resolve-Path .keys/keys).Path` trước lệnh Compose, hoặc sửa đường dẫn local .env cho phù hợp.

Hồi quy cuối: **486 test Contract pass, không skip**, gồm test PostgreSQL thật trên 7 database disposable và 33 test Drive mới. Có hai warning (TestClient deprecation, ZIP duplicate cố ý trong fixture), không lỗi. Đã sửa lỗi schema dropdown ở Version response rồi chạy lại toàn bộ. OpenAPI validator, build Docker, smoke image cô lập (health/auth/version) và cloud replay/readback đều pass. Không coi đây là E2E trên máy nhóm.

Preview native system hiện chèn nhãn chưa hoàn thiện ở đầu tài liệu, không watermark riêng từng trang; không dùng preview để ký.

Render thật cả ba mẫu và file điền/preview bằng server Docker network-none/rootless/read-only, không credentials. Lease: source prepared/final/preview đều 3 trang, đã mở xem toàn bộ 9 ảnh cuối; ngày, tiền, tiếng Việt và vùng ký không clipping/overlap. Gift và mortgage chỉ chuẩn bị kỹ thuật: đã đọc ảnh final, có lỗi nguồn/layout, gate false và không giao như mẫu dùng được. Packaged render_docx.py đã thử nhưng bundle Windows thiếu soffice.exe; dùng renderer production Docker, không gọi LibreOffice desktop.

Phát hiện footer bị serialize khi không sửa; đã fix fill_docx để giữ nguyên bytes phần không thay đổi và thêm regression test. Tặng cho có binary DOCX dù đuôi .doc; giữ nguyên package, không chuyển lại. Thế chấp Word cũ được chuyển trên bản sao; hash nguyên bản và hash working DOCX được lưu tách biệt.
