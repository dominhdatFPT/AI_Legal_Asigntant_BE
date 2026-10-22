# Biểu mẫu Procedure dùng trong Contract

> Cập nhật 20/10/2026: đã có kho 30 bản nguồn chỉ xem, tách khỏi luồng điền/xuất bên dưới. Xem [bàn giao](../../docs/api/REFERENCE_LIBRARY_RELEASE.md). Không tự publish các mẫu còn blocker trong catalog cá nhân.

Trạng thái kiểm kê tại workspace ngày 17/10/2026. Mục tiêu là điền thông tin trên web rồi xuất file mới, giữ gần như nguyên bố cục nguồn. Không coi việc có file trong thư mục Procedure là đã có mẫu dùng được trong Contract.

## Nguồn và trạng thái

- Hai thư mục `Template các thủ tục đất đai` và `Template các thủ tục kinh doanh` có 30 file khác nhau theo SHA-256. Các thư mục con `IMG đất đai` và `Template kinh doanh` chứa bản sao trùng, nên tổng số đường dẫn là 60.
- Trong 30 file nguồn: 15 `.doc`, 6 `.docx`, 8 `.pdf`, 1 `.png`. Tám PDF không có AcroForm field để điền trực tiếp.
- Đã chuẩn bị **30 PDF làm việc / 114 trang** tại `templates/.local-c2/procedure-prepared/`, giữ nguyên file gốc và ghi SHA-256 trong `manifest.json`. Chạy lại script cho kết quả 30/30, không ghi đè. Đây là bản cục bộ phục vụ QA, không có trong image/runtime Contract.
- Đã render 114 trang sang ảnh tại `templates/.local-c2/procedure-qa/` và xem contact sheet toàn bộ. 27 trang không có lớp chữ (ảnh/scan hoặc trang PDF không trích xuất chữ), nên không thể dùng text extraction để gắn ô nhập tự động cho toàn bộ bộ mẫu.
- Ba file hợp đồng tặng cho, thế chấp và thuê mặt bằng trùng SHA-256 với ba nguồn đã kiểm kê trong `README_DRIVE_CATALOG.md`; không nhập lần hai. Chỉ mẫu thuê đã published/visible theo bản kiểm kê đó. Hai mẫu đất đai giữ hidden/draft do blocker nội dung/bố cục.
- Các biểu mẫu khác chưa có mapping toàn bộ ô nhập, QA sau điền hoặc bản đăng ký trong Contract catalog. Không bật visible/published khi chưa đủ các bước này.

## Tên theo nội dung thật

Người dùng đã chọn dùng tên/phân loại theo nội dung trong file, không theo tên file nếu chúng lệch nhau. Giữ nguyên bytes file nguồn.

| File nguồn trong Procedure | Nội dung thực tế cần gắn nhãn trong Contract |
| --- | --- |
| `Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp.docx` | Giấy đề nghị cấp lại Giấy chứng nhận đăng ký **hộ kinh doanh** |
| `Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp.docx` | Giấy đề nghị đăng ký thay đổi nội dung đăng ký **hộ kinh doanh** |
| `Thủ tục thông báo tạm ngừng - Cấp đổi thông tin ĐKDN.docx` | Giấy đề nghị đăng ký hoạt động chi nhánh/văn phòng đại diện hoặc thông báo địa điểm kinh doanh của **hợp tác xã** |

Không liên kết ba file này với mã thủ tục doanh nghiệp chỉ dựa vào tên file. Mỗi mẫu/biến thể cần được nhóm kiểm tra nội dung trước khi publish.

## Đường kỹ thuật

Contract đã có system DOCX snapshot cho mẫu nguồn được review. `catalog_source.py` nay nhận thêm system PDF snapshot: các ô trắng dùng `pdf_box`; dòng chấm văn bản đã được đánh dấu dùng `pdf_dots`. Renderer chỉ che vùng toàn dấu chấm/space/underscore, từ chối vùng chạm chữ, hình hoặc nét vẽ; sau đó dùng renderer PDF hiện có để xuất PDF. Không xuất DOCX từ nguồn PDF. Tất cả mẫu PDF vẫn cần mapping thủ công và QA từng trang; PDF scan/ảnh chưa được tự nhận diện ô nhập.

Đã thử một ô của mẫu đơn chuyển mục đích sử dụng đất bằng dữ liệu giả; ảnh xuất cục bộ cho thấy nhãn và bố cục còn nguyên. Đây không phải mapping đầy đủ hay mẫu đã publish. Tệp PDF/PNG QA tạm không dùng làm nguồn production.

## Gate để đưa từng mẫu vào catalog dùng thật

1. Ghim SHA-256 nguồn, tên theo nội dung thật và loại thủ tục liên quan; xác nhận nội dung/phiên bản pháp lý với người phụ trách mẫu.
2. Đã chuyển `.doc` cũ/ảnh sang PDF làm việc trên bản sao; không sửa file gốc. Cần kiểm tra độ đúng của từng bản chuyển cùng nguồn trước khi dùng thật.
3. Tạo trường form, vị trí điền, kiểm tra dữ liệu ngắn/dài, render preview và final, xem mọi trang DOCX/PDF; không điền chữ ký, dấu hay lời chứng.
4. Đăng ký snapshot/version ẩn trong kho Contract; chỉ publish/visible sau khi cả nội dung và layout được duyệt. Không ghi đè version đã published.

Chưa chạy import cloud, chưa khởi động service, chưa tạo nháp/job của người dùng và chưa push Git.

## Công cụ QA cục bộ

- `scripts/prepare_procedure_sources.py`: loại bản sao trùng, chuyển nguồn sang PDF, kiểm tra parser/số trang và ghi manifest. Không nhập catalog.
- `scripts/audit_procedure_sources.py`: render tất cả trang và thống kê trang thiếu lớp chữ.
- `scripts/contact_sheets.py`: ghép ảnh thành 10 sheet để kiểm tra nhanh; ảnh toàn trang vẫn được giữ để review chi tiết.
- `tests/test_prepare_procedure_sources.py`: 4 kiểm thử nguồn trùng, kiểm tra PDF, bảo toàn nguồn, ảnh → A4. Đã pass trong Docker network-none.

Chưa có mapping đã duyệt cho 30 mẫu, chưa có form web hoặc luồng import ẩn cho bộ Procedure này. Không coi bước chuyển 30 PDF là hoàn tất yêu cầu điền/xuất. Các mẫu scan, ô bảng, checkbox, vùng chữ ký và chú thích yêu cầu người review xác nhận từng ô; một số nguồn có watermark, placeholder màu đỏ hoặc nội dung giả định riêng, nên không được tự công bố chỉ vì render được.
