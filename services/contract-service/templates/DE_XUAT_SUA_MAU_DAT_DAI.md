# Đề xuất sửa hai mẫu đất đai để nhóm duyệt

Ngày rà soát: 11/10/2026. Đây là đề xuất về các lỗi nhìn thấy trong file nguồn, chưa được áp dụng vào bản published và không phải kết luận mẫu đáp ứng pháp luật hiện hành. Giữ nguyên bản Drive; chỉ sửa trên một phiên bản mới sau khi nhóm duyệt.

## Hợp đồng thế chấp

Nguồn: [Hợp đồng thế chấp quyền sử dụng đất.doc](https://docs.google.com/document/d/1Bn2uDcANhiuvP9fTjoFHGcjBNlUdFyly/edit).

| Vị trí trong nguồn | Vấn đề | Đề xuất để nhóm quyết định |
| --- | --- | --- |
| Đoạn trước Điều 1 | Ghi “thế chấp căn hộ nhà chung cư”, trong khi tiêu đề và Điều 2 mô tả quyền sử dụng đất, tài sản gắn liền với đất | Nếu nhóm chọn mẫu thế chấp đất: thay đối tượng ở đoạn này bằng “thế chấp quyền sử dụng đất và tài sản gắn liền với đất (nếu có)”. Nếu là căn hộ, cần một mẫu riêng có nội dung phù hợp, không chỉ đổi tên. |
| Điều 6.2 | Ghi lệ phí “thế châp căn hộ” | Sau khi chốt đối tượng, dùng cùng cách gọi như tiêu đề/Điều 2; kiểm tra lại bên chịu trách nhiệm nộp. |
| Tiêu đề sau Điều 9 Cam đoan | Lại ghi “ĐIỀU 9: HIỆU LỰC CỦA HỢP ĐỒNG” | Đổi thành Điều 10 và kiểm tra các tham chiếu trong toàn bộ bản sửa. |
| Lời chứng công chứng viên | Tiếp tục ghi “thế chấp bằng căn hộ nhà chung cư” | Để người phụ trách mẫu/công chứng xác nhận wording đúng đối tượng. Backend không tự điền hoặc tự tạo lời chứng. |
| Thông tin Bên A | Cùng lúc có nhánh cá nhân và nhánh đồng chủ sở hữu | Chọn biến thể trước khi điền, hoặc tách hai mẫu; chỉ yêu cầu thông tin của nhánh được chọn. Không bắt user khai cả hai nhánh. |
| Phần giấy tờ sở hữu | Có các dòng trống không gắn nhãn | Nhóm xác nhận danh sách giấy tờ/format cần khai trước khi tạo form chính thức. |

## Hợp đồng tặng cho

Nguồn: [Hợp đồng tặng cho quyền sử dụng đất.doc](https://docs.google.com/document/d/19EqvC5d5X6pJqoI6x7XBR5El8QxvhC1F/edit). File có đuôi .doc nhưng binary thực tế là DOCX; không chuyển đổi lại để tránh mất format.

| Vị trí trong nguồn | Vấn đề | Đề xuất để nhóm quyết định |
| --- | --- | --- |
| Thứ tự các điều | Đang là 1 → 3 → 4 → 3 → 4 → 5 → 6 → 7 → 8 → 9 | Giữ nguyên thứ tự nội dung, đánh lại số từ 1 đến 10; sửa các số mục con tương ứng và kiểm tra tham chiếu. Nhóm xác nhận có bị thiếu một Điều 2 trong nguồn hay chỉ đánh số sai, trước khi áp dụng. |
| Đoạn giấy tờ sở hữu trước Bên B | Ghi giấy tờ được cấp cho Bên B, trong khi phần đối tượng mô tả quyền sử dụng đất của Bên A | Đề xuất đổi thành Bên A nếu đây đúng là giấy tờ của bên tặng cho; cần nhóm xác nhận, không tự đổi vai các bên. |
| Điều đăng ký và lệ phí / Điều trách nhiệm thuế, lệ phí | Một đoạn cố định Bên A chịu lệ phí, đoạn sau để trống bên chịu thuế/lệ phí | Nhóm xác định có hai loại chi phí khác nhau hay bị mâu thuẫn; giữ wording rõ ràng và form khớp quyết định. Không tự gán toàn bộ chi phí cho một bên. |
| Thông tin các bên | Có nhánh cá nhân/đồng sở hữu và hai người nhận | Tách biến thể hoặc thêm lựa chọn nhánh trước khi điền; xác định số người nhận cần hỗ trợ. |
| Phần công chứng | Có ô ngày, tên công chứng viên, số chứng thực và lời chứng | Giữ nguyên, không đưa vào form user, không tự tạo chữ ký/dấu hoặc xác nhận đã công chứng. |

## Cách duyệt

Nhóm có thể phản hồi từng dòng: đồng ý / wording thay thế / gửi bản nguồn mới. Sau khi nhận quyết định: tạo version mới, rà soát lại mapping theo đúng hash mới, render Word/PDF và kiểm tra mọi trang, rồi mới publish/hiện mẫu. Không sửa trực tiếp version published, không ghi đè hợp đồng đã sinh.

Hai mẫu hiện được chuẩn bị kỹ thuật để rà soát, nhưng không cho tạo nháp từ catalog user cho đến khi giải quyết các blocker này. File “Giay_Uy_Quyen_Kinh_Doanh.docx” cũng không được đưa vào kho hợp đồng kinh doanh: nội dung thực tế là ủy quyền liên quan BHXH. Hai văn bản thừa kế không được coi là hợp đồng và chưa tự publish cùng bộ ba hợp đồng này.
