# Bàn giao kho tài liệu chỉ xem: Contract → Procedure → FE

Cập nhật 20/10/2026. Đã đóng gói và chạy trên web cục bộ đủ 30 tài liệu / 114 trang từ thư mục Drive được cung cấp. Chỉ xem bản nguồn, không có ô điền, tạo nháp, upload hay generation.

## Kết quả

- 18 tài liệu đất đai, 10 doanh nghiệp/hộ kinh doanh, 1 hợp tác xã, 1 BHXH.
- Tên hiển thị theo nội dung thật. Tên file Drive giữ trong metadata để đối chiếu; không hiển thị trên trang xem.
- 25 mục phân loại trong Procedure: 16 mã dự án đã có và 9 nhóm tài liệu không tự gán mã/UUID thủ tục hành chính.
- Một tài liệu có thể liên kết nhiều thủ tục (đơn biến động đất đai hoặc đơn đề nghị đa mục đích).
- Cả 30 mẫu đều xem được. Trạng thái và ghi chú rà soát được giữ trong metadata; giao diện không hiện nhãn trạng thái, ghi chú nguồn hay bộ lọc trạng thái.
- Hai hợp đồng tặng cho/thế chấp vẫn không được tự publish trong catalog điền/xuất cũ trên Supabase. Không thay đổi dữ liệu cloud hay file Drive.

## API

Qua Gateway `http://localhost:8080`:

- GET `/api/contracts/reference-catalog`: kho bản nguồn công khai chỉ xem. Query: domain, procedure, q, status, page, page_size (tối đa 100).
- GET `/api/contracts/reference-catalog/{id}`: chi tiết một mẫu.
- GET `/api/contracts/reference-assets/{filename}`: chỉ file PNG/PDF nằm trong whitelist manifest; không đọc private Storage hay đường dẫn tùy ý.
- GET `/api/procedures/reference-classifications`: danh mục phân loại không phụ thuộc database.

FE `http://localhost:3000/kho-mau-don-tham-khao` đọc `/api/reference-catalog` của server FE. Server ưu tiên Contract qua Gateway; khi backend không sẵn sàng, dùng snapshot được xuất từ cùng bộ Contract. Header `X-Reference-Catalog-Source` phân biệt `contract-api` và `bundled-contract-snapshot`. Không chuyển cookie/token người dùng đến API kho bản nguồn.

Ảnh trang tài liệu được render từ PDF ở 216 DPI (3×), đóng gói cùng FE. API Contract cũng cung cấp đúng các asset đó cho các client khác. Tên ảnh chứa hash nội dung để tránh dùng lại cache ảnh QA cũ; bản PDF nguồn không bị sửa và manifest ghi hash PDF/PNG.

## Chuẩn hóa và nguồn dữ liệu

- Contract sở hữu `services/contract-service/templates/reference-catalog/catalog.json` và 144 asset (30 PDF + 114 PNG).
- Procedure sở hữu `services/procedure-service/app/reference_classifications.json`.
- Bản FE nằm tại `frontend/AI_Legal/public/reference-templates/`.
- Script `services/contract-service/scripts/build_reference_catalog.py --sync-frontend` kiểm tra SHA-256 file gốc/PDF, số trang, phân loại và xuất cùng một snapshot sang FE. Các nguyên liệu source/QA nằm cục bộ `.local-c2`, không cần đưa vào runtime. Replay không sửa binary đã đóng gói; asset khác bytes bị chặn.
- Không dùng tên file để gắn DN-001 cho giấy ủy quyền BHXH; không gắn DN-010 cho mẫu HTX; không gắn DN-002 cho cấp lại hộ kinh doanh; không gọi tờ khai GTGT là đơn đăng ký thuế ban đầu.
- “Đơn xin giao đất02.png” được đặt lại theo tiêu đề: Đơn đề nghị chuyển hình thức giao đất, cho thuê đất. Đơn đo đạc không tự coi là thủ tục cấp đổi giấy chứng nhận.
- Giữ nguyên watermark, chữ ký trống, dấu trống, hướng dẫn, ghi chú màu đỏ và cả trang trắng theo nguồn. Không sửa điều khoản pháp lý.

## Chạy

Từ backend, cấu hình JWT_KEYS_DIR theo đường dẫn public key local có sẵn rồi:

```powershell
docker compose -f docker-compose.yml -f docker-compose.jwt-local.yml build contract-service procedure-service
docker compose -f docker-compose.yml -f docker-compose.jwt-local.yml up -d --no-deps contract-service procedure-service gateway
```

Gateway dùng upstream động nên các service không liên quan có thể tắt. Chỉ kho bản nguồn không cần DB hoặc Supabase; các API hợp đồng cá nhân vẫn cần xác thực/cấu hình cũ. Không bật Document hoặc worker để phục vụ trang này.

Từ frontend: `npm start`. Khi thay đổi route Express phải khởi động lại server.

## Kiểm tra

- 19 kiểm thử FE, bao gồm 11 kiểm thử hiện có; đủ 30 mẫu/114 trang, hash asset, lọc nhiều thủ tục, API ưu tiên Contract, snapshot fallback và không forward cookie/token.
- 6 kiểm thử Contract API trong container network-none, read-only, không credentials; bảo toàn 144 asset, phân trang, lọc, 404 whitelist và API riêng vẫn 401.
- 2 kiểm thử Procedure cho mã/nhóm phân loại; nhóm tài liệu chưa có mã giữ code/UUID null.
- Docker build Contract/Procedure đạt. Live Gateway Contract, Procedure và FE trả 200; FE báo nguồn `contract-api`.
- Đã xem 10 contact sheet bao phủ 114 trang nguồn. Đây là QA khả năng hiển thị và nhãn nội dung; không phải xác nhận hiệu lực pháp lý của từng biểu mẫu.

## Tên và phân loại cuối

| Tên hiển thị | Nhóm | Thủ tục/phân loại | Trạng thái |
| --- | --- | --- | --- |
| Hợp đồng cho thuê mặt bằng kinh doanh | business | DN-006 - Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký hộ kinh doanh | business | HKD-001 - Đăng ký thành lập hộ kinh doanh | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã | cooperative | Đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã | Bản nguồn chỉ xem |
| Thông báo về việc giải thể doanh nghiệp | business | DN-009 - Thủ tục đăng ký giải thể doanh nghiệp | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh | business | HKD-002 - Thay đổi nội dung đăng ký hộ kinh doanh | Bản nguồn chỉ xem |
| Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT) | business | Kê khai thuế giá trị gia tăng theo phương pháp khấu trừ | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần | business | DN-003 - Thủ tục đăng ký thành lập Công ty Cổ phần | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh | business | DN-008 - Thủ tục đăng ký thành lập Công ty Hợp danh | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên | business | DN-004 - Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn | Bản nguồn chỉ xem |
| Giấy đề nghị đăng ký doanh nghiệp tư nhân | business | DN-005 - Thủ tục đăng ký thành lập Doanh nghiệp tư nhân | Bản nguồn chỉ xem |
| Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh | business | Cấp lại Giấy chứng nhận đăng ký hộ kinh doanh | Bản nguồn chỉ xem |
| Giấy ủy quyền thực hiện thủ tục bảo hiểm xã hội | social_insurance | Ủy quyền thực hiện thủ tục bảo hiểm xã hội | Bản nguồn chỉ xem |
| Phương án sử dụng tầng đất mặt | land | DD-008 - Thủ tục chuyển mục đích sử dụng đất | Bản nguồn chỉ xem |
| Đơn đề nghị chuyển mục đích sử dụng đất (Mẫu số 02) | land | DD-008 - Thủ tục chuyển mục đích sử dụng đất | Bản nguồn chỉ xem |
| Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản DOCX) | land | Giao đất, thuê đất, chuyển mục đích sử dụng đất; giao đất và giao rừng, thuê đất và thuê rừng; DD-008 - Thủ tục chuyển mục đích sử dụng đất | Bản nguồn chỉ xem |
| Đơn yêu cầu đo đạc đất | land | Yêu cầu đo đạc đất | Bản nguồn chỉ xem |
| Đơn xin gia hạn sử dụng đất (Mẫu số 08) | land | DD-009 - Thủ tục gia hạn thời hạn sử dụng đất | Bản nguồn chỉ xem |
| Đơn xin xác nhận lại thời hạn sử dụng đất nông nghiệp (Mẫu số 09) | land | Xác nhận lại thời hạn sử dụng đất nông nghiệp | Bản nguồn chỉ xem |
| Đơn đề nghị chuyển hình thức giao đất, cho thuê đất (Mẫu số 02) | land | Chuyển hình thức giao đất, cho thuê đất | Bản nguồn chỉ xem |
| Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản có ghi chú cập nhật) | land | Giao đất, thuê đất, chuyển mục đích sử dụng đất; giao đất và giao rừng, thuê đất và thuê rừng; DD-008 - Thủ tục chuyển mục đích sử dụng đất | Bản nguồn chỉ xem |
| Đơn đề nghị hòa giải tranh chấp đất đai | land | Hòa giải tranh chấp đất đai | Bản nguồn chỉ xem |
| Đơn đề nghị tách thửa đất, hợp thửa đất (Mẫu số 21) | land | DD-003 - Tách thửa hoặc hợp thửa đất | Bản nguồn chỉ xem |
| Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (03a) | land | DD-011 - Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ) | Bản nguồn chỉ xem |
| Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất | land | DD-006 - Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở | Cần rà soát nội dung |
| Văn bản nhận tài sản thừa kế (người thừa kế duy nhất) | land | DD-006 - Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở | Tình huống riêng |
| Văn bản thỏa thuận phân chia di sản thừa kế (tình huống hai người, phần di sản 1/2) | land | DD-006 - Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở | Tình huống riêng |
| Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 18) | land | DD-001 - Đăng ký biến động quyền sử dụng đất do chuyển nhượng; DD-006 - Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở | Bản nguồn chỉ xem |
| Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền với đất | land | DD-010 - Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất | Cần rà soát nội dung |
| Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (01a) | land | DD-010 - Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất | Bản nguồn chỉ xem |
| Đơn đăng ký đất đai, tài sản gắn liền với đất (Mẫu số 15) | land | DD-004 - Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu | Bản nguồn chỉ xem |
