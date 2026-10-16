-- 002_seed_procedures.sql
-- Seed data for procedure_domains, procedures, and procedure_legal_bases
-- Aligned with:
-- 1. "Các thủ tục chi tiết hạng mục kinh doanh.md"
-- 2. "Các thủ tục chi tiết hạng mục đất đai.md"

-- 1. Domains
INSERT INTO procedure_domains (code, name, description, icon, order_index, is_active)
VALUES
('business', 'Doanh nghiệp & Hộ kinh doanh', 'Thủ tục thành lập, thay đổi thông tin, đăng ký thuế, tạm ngừng, giải thể doanh nghiệp và hộ kinh doanh cá thể.', 'building', 1, true),
('land', 'Đất đai & Bất động sản', 'Thủ tục đăng ký biến động, cấp đổi, cấp mới, tách thửa, thế chấp, sang tên sổ đỏ theo Luật Đất đai 2024.', 'map-pin', 2, true)
ON CONFLICT (code) DO UPDATE 
SET name = EXCLUDED.name, description = EXCLUDED.description, icon = EXCLUDED.icon, order_index = EXCLUDED.order_index;

-- 2. Procedures
INSERT INTO procedures (id, code, name, domain, description, processing_days, submission_channels, receiving_agency_level, fee, fee_note, effective_from, status)
VALUES 
-- Land procedures (DD-001 to DD-012)
('d5d9c792-5b9e-4e42-9a3b-2856247c7c30', 'DD-001', 'Đăng ký biến động quyền sử dụng đất do chuyển nhượng', 'land', 'Thủ tục đăng ký sang tên quyền sử dụng đất khi chuyển nhượng theo quy định pháp luật.', 10, ARRAY['direct', 'online'], 'district', 0, 'Lệ phí địa chính theo quy định của từng địa phương', '2024-01-01', 'active'),
('f4c3d251-7c2b-4e62-9e3d-4076269b9b52', 'DD-002', 'Cấp đổi Giấy chứng nhận quyền sử dụng đất', 'land', 'Thủ tục cấp đổi sổ đỏ, sổ hồng do bị rách nát, ố nhòe hoặc có nhu cầu đổi sang mẫu mới.', 7, ARRAY['direct', 'online'], 'district', 50000, 'Lệ phí cấp đổi Giấy chứng nhận', '2024-01-01', 'active'),
('a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d', 'DD-003', 'Tách thửa hoặc hợp thửa đất', 'land', 'Thủ tục phân chia một thửa đất thành nhiều thửa hoặc hợp nhiều thửa đất liền kề thành một thửa.', 15, ARRAY['direct', 'online'], 'district', 0, 'Phí đo đạc và lệ phí địa chính theo quy định địa phương', '2024-01-01', 'active'),
('b4c5d6e7-f8a9-4b0c-1d2e-3f4a5b6c7d8e', 'DD-004', 'Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu', 'land', 'Đăng ký đất đai, cấp Giấy chứng nhận quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất lần đầu cho cá nhân, hộ gia đình.', 20, ARRAY['direct', 'online'], 'district', 100000, 'Lệ phí cấp Giấy chứng nhận lần đầu theo quy định HĐND cấp tỉnh', '2024-08-01', 'active'),
('c5d6e7f8-a9b0-4c1d-2e3f-4a5b6c7d8e9f', 'DD-005', 'Thủ tục cấp lại Giấy chứng nhận do bị mất', 'land', 'Thủ tục niêm yết công khai và cấp lại Giấy chứng nhận quyền sử dụng đất cho người sử dụng đất khi bị mất sổ đỏ.', 10, ARRAY['direct', 'online'], 'district', 50000, 'Lệ phí cấp lại Giấy chứng nhận', '2024-08-01', 'active'),
('d6e7f8a9-b0c1-4d2e-3f4a-5b6c7d8e9f0a', 'DD-006', 'Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở', 'land', 'Thủ tục đăng ký biến động khi tặng cho hoặc thừa kế quyền sử dụng đất, quyền sở hữu tài sản gắn liền với đất.', 10, ARRAY['direct', 'online'], 'district', 0, 'Lệ phí địa chính và thẩm định hồ sơ theo quy định địa phương', '2024-08-01', 'active'),
('e7f8a9b0-c1d2-4e3f-a4b5-6c7d8e9f0a1b', 'DD-007', 'Thủ tục đính chính Giấy chứng nhận đã cấp do sai sót thông tin', 'land', 'Đính chính thông tin thửa đất, người sử dụng đất hoặc tài sản gắn liền với đất trên GCN đã cấp khi phát hiện có sai sót.', 10, ARRAY['direct', 'online'], 'district', 0, 'Miễn lệ phí nếu sai sót do lỗi của cơ quan nhà nước', '2024-08-01', 'active'),
('f8a9b0c1-d2e3-4f4a-b5c6-7d8e9f0a1b2c', 'DD-008', 'Thủ tục chuyển mục đích sử dụng đất', 'land', 'Thủ tục cho phép chuyển mục đích sử dụng đất nông nghiệp, đất vườn sang đất ở theo kế hoạch sử dụng đất cấp huyện.', 15, ARRAY['direct', 'online'], 'district', 0, 'Nộp tiền sử dụng đất chênh lệch theo Bảng giá đất và Thông báo nộp thuế', '2024-08-01', 'active'),
('a9b0c1d2-e3f4-4a5b-c6d7-8e9f0a1b2c3d', 'DD-009', 'Thủ tục gia hạn thời hạn sử dụng đất', 'land', 'Thủ tục gia hạn thời hạn sử dụng đất hoặc xác nhận tiếp tục sử dụng đất nông nghiệp khi hết thời hạn ghi trên GCN.', 10, ARRAY['direct', 'online'], 'district', 0, 'Lệ phí thẩm định và địa chính theo quy định địa phương', '2024-08-01', 'active'),
('b0c1d2e3-f4a5-4b6c-d7e8-9f0a1b2c3d4e', 'DD-010', 'Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất', 'land', 'Đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất khi vay vốn tại tổ chức tín dụng/ngân hàng.', 1, ARRAY['direct', 'online'], 'district', 80000, 'Lệ phí đăng ký giao dịch bảo đảm theo quy định', '2024-08-01', 'active'),
('c1d2e3f4-a5b6-4c7d-e8f9-0a1b2c3d4e5f', 'DD-011', 'Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ)', 'land', 'Xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất sau khi người sử dụng đất hoàn thành nghĩa vụ trả nợ vay.', 1, ARRAY['direct', 'online'], 'district', 20000, 'Lệ phí xóa đăng ký giao dịch bảo đảm', '2024-08-01', 'active'),
('d2e3f4a5-b6c7-4d8e-f9a0-1b2c3d4e5f6a', 'DD-012', 'Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính', 'land', 'Khai thác thông tin thửa đất, trích lục bản đồ địa chính, thông tin quy hoạch sử dụng đất từ Hệ thống thông tin đất đai.', 1, ARRAY['direct', 'online'], 'district', 50000, 'Phí khai thác và sử dụng tài liệu đất đai theo quy định HĐND cấp tỉnh', '2024-08-01', 'active'),

-- Business procedures (HKD)
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', 'HKD-001', 'Đăng ký thành lập hộ kinh doanh', 'business', 'Cấp giấy chứng nhận đăng ký hộ kinh doanh cá thể.', 3, ARRAY['direct', 'online'], 'district', 100000, 'Lệ phí đăng ký thành lập (miễn phí nếu nộp online)', '2021-01-04', 'active'),
('b2c3d4e5-f6a7-4b5c-9d0e-1f2a3b4c5d6e', 'HKD-002', 'Thay đổi nội dung đăng ký hộ kinh doanh', 'business', 'Thủ tục thay đổi ngành nghề, tên hộ kinh doanh, địa chỉ trụ sở hoặc vốn.', 3, ARRAY['direct', 'online'], 'district', 100000, 'Lệ phí thay đổi nội dung đăng ký', '2021-01-04', 'active'),

-- Business procedures (DN)
('0f86c6ca-128e-431a-80b7-b0f4117d0247', 'DN-001', 'Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp', 'business', 'Ủy quyền cho cá nhân, tổ chức khác thực hiện thủ tục đăng ký doanh nghiệp theo quy định của pháp luật.', 3, ARRAY['direct', 'online'], 'province', 0, 'Không thu lệ phí ủy quyền', '2021-01-04', 'active'),
('a1d7edb1-fa8a-41e2-b83a-f0aa93e79431', 'DN-002', 'Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp', 'business', 'Cấp lại Giấy chứng nhận đăng ký doanh nghiệp khi bị mất, cháy, rách, nát hoặc bị tiêu hủy dưới hình thức khác.', 3, ARRAY['direct', 'online'], 'province', 50000, 'Lệ phí cấp lại Giấy chứng nhận ĐKDN', '2021-01-04', 'active'),
('4be0988c-2da5-4f18-a641-a0ffd8de1f10', 'DN-003', 'Thủ tục đăng ký thành lập Công ty Cổ phần', 'business', 'Đăng ký thành lập mới doanh nghiệp dưới hình thức công ty cổ phần có ít nhất 03 cổ đông sáng lập.', 3, ARRAY['direct', 'online'], 'province', 100000, 'Lệ phí đăng ký thành lập (miễn phí nếu nộp online)', '2021-01-04', 'active'),
('4e4fb6f1-4420-46ce-824b-8e7a00f917e1', 'DN-004', 'Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn', 'business', 'Đăng ký thành lập mới công ty TNHH một thành viên hoặc công ty TNHH hai thành viên trở lên.', 3, ARRAY['direct', 'online'], 'province', 100000, 'Lệ phí đăng ký thành lập (miễn phí nếu nộp online)', '2021-01-04', 'active'),
('cd401f44-dbcd-4e55-ab6e-e81da3b4d7e1', 'DN-005', 'Thủ tục đăng ký thành lập Doanh nghiệp tư nhân', 'business', 'Đăng ký thành lập doanh nghiệp do một cá nhân làm chủ và tự chịu trách nhiệm bằng toàn bộ tài sản của mình.', 3, ARRAY['direct', 'online'], 'province', 100000, 'Lệ phí đăng ký thành lập (miễn phí nếu nộp online)', '2021-01-04', 'active'),
('0a2e901e-51d7-4df4-8e5c-2fb720628374', 'DN-006', 'Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp', 'business', 'Xác lập hợp đồng thuê tài sản, địa điểm hợp pháp đặt trụ sở chính của doanh nghiệp theo quy định.', 3, ARRAY['direct', 'online'], 'district', 0, 'Không thu lệ phí hành chính', '2021-01-04', 'active'),
('4ee8a3bd-46c5-4187-bd2a-4cf2a9b63255', 'DN-007', 'Thủ tục đăng ký thuế, khai thuế ban đầu', 'business', 'Đăng ký thông tin thuế, kê khai phương pháp tính thuế ban đầu với cơ quan thuế trực tiếp quản lý.', 3, ARRAY['direct', 'online'], 'district', 0, 'Miễn phí thủ tục đăng ký thuế ban đầu', '2021-01-04', 'active'),
('b5f61819-a0da-41fd-a7bf-bc8c4e02dfb2', 'DN-008', 'Thủ tục đăng ký thành lập Công ty Hợp danh', 'business', 'Đăng ký thành lập công ty có ít nhất 02 thành viên là chủ sở hữu chung của công ty (thành viên hợp danh).', 3, ARRAY['direct', 'online'], 'province', 100000, 'Lệ phí đăng ký thành lập', '2021-01-04', 'active'),
('773b5490-514a-4cf4-bbe0-21252db35e7a', 'DN-009', 'Thủ tục đăng ký giải thể doanh nghiệp', 'business', 'Đăng ký giải thể doanh nghiệp khi kết thúc thời hạn hoạt động, theo quyết định của chủ sở hữu hoặc bị thu hồi GCN.', 5, ARRAY['direct', 'online'], 'province', 0, 'Miễn lệ phí giải thể', '2021-01-04', 'active'),
('697bfc4c-5f4b-4ba3-842e-c0a683e83d6f', 'DN-010', 'Thủ tục thông báo tạm ngừng / Cấp đổi thông tin ĐKDN', 'business', 'Thông báo về việc tạm ngừng kinh doanh hoặc tiếp tục kinh doanh trước thời hạn đã thông báo.', 3, ARRAY['direct', 'online'], 'province', 0, 'Miễn lệ phí', '2021-01-04', 'active'),
('f7c8d9e0-1a2b-3c4d-5e6f-7a8b9c0d1e2f', 'DN-011', 'Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp', 'business', 'Đăng ký thay đổi ngành nghề kinh doanh, tên doanh nghiệp, địa chỉ trụ sở chính, vốn điều lệ hoặc thông tin thành viên/cổ đông.', 3, ARRAY['direct', 'online'], 'province', 100000, 'Lệ phí đăng ký thay đổi nội dung ĐKDN', '2021-01-04', 'active')
ON CONFLICT (code) DO UPDATE 
SET name = EXCLUDED.name, description = EXCLUDED.description, processing_days = EXCLUDED.processing_days, fee = EXCLUDED.fee;

-- 3. Conditions
INSERT INTO procedure_conditions (procedure_id, condition_code, title, description, order_index, is_mandatory)
VALUES
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', 'COND-01', 'Công dân Việt Nam', 'Cá nhân, thành viên hộ gia đình là công dân Việt Nam đủ 18 tuổi, có năng lực hành vi dân sự đầy đủ.', 1, true),
('d5d9c792-5b9e-4e42-9a3b-2856247c7c30', 'COND-02', 'Đất không có tranh chấp', 'Thửa đất chuyển nhượng phải không có tranh chấp, quyền sử dụng đất không bị kê biên để bảo đảm thi hành án.', 1, true)
ON CONFLICT DO NOTHING;

-- 4. Fees
INSERT INTO procedure_fees (procedure_id, fee_type, name, amount, unit, is_exemptible, note)
VALUES
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', 'administrative_fee', 'Lệ phí cấp Giấy chứng nhận đăng ký hộ kinh doanh', 100000, 'lần', true, 'Miễn lệ phí nếu đăng ký qua mạng điện tử')
ON CONFLICT DO NOTHING;

-- 5. Legal bases (aligned with Các thủ tục chi tiết hạng mục kinh doanh.md & Các thủ tục chi tiết hạng mục đất đai.md)
INSERT INTO procedure_legal_bases (procedure_id, doc_number, title, article_ref, source_url, order_index)
VALUES
-- === HẠNG MỤC KINH DOANH ===
-- 1. DN-001
('0f86c6ca-128e-431a-80b7-b0f4117d0247', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 12 (Ủy quyền thực hiện thủ tục đăng ký doanh nghiệp)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 1),

-- 2. DN-002
('a1d7edb1-fa8a-41e2-b83a-f0aa93e79431', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 33', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('a1d7edb1-fa8a-41e2-b83a-f0aa93e79431', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 68', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 3. DN-003
('4be0988c-2da5-4f18-a641-a0ffd8de1f10', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 22', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('4be0988c-2da5-4f18-a641-a0ffd8de1f10', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 23', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 4. DN-004
('4e4fb6f1-4420-46ce-824b-8e7a00f917e1', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 21', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('4e4fb6f1-4420-46ce-824b-8e7a00f917e1', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 22', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 5. DN-005
('cd401f44-dbcd-4e55-ab6e-e81da3b4d7e1', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 19', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('cd401f44-dbcd-4e55-ab6e-e81da3b4d7e1', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 21', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 6. DN-006
('0a2e901e-51d7-4df4-8e5c-2fb720628374', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 42 (Trụ sở chính)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('0a2e901e-51d7-4df4-8e5c-2fb720628374', '91/2015/QH13', 'Bộ luật Dân sự 2015', 'Điều 472 (Hợp đồng thuê tài sản)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=95874', 2),

-- 7. HKD-001
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 31 (Lập địa điểm kinh doanh của DN)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 1),
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 87 (Đăng ký Hộ kinh doanh)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 8. DN-007
('4ee8a3bd-46c5-4187-bd2a-4cf2a9b63255', '38/2019/QH14', 'Luật Quản lý thuế 2019', 'Điều 31 (Hồ sơ đăng ký thuế lần đầu)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=136224', 1),

-- 9. DN-008
('b5f61819-a0da-41fd-a7bf-bc8c4e02dfb2', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 20', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('b5f61819-a0da-41fd-a7bf-bc8c4e02dfb2', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 24', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 10. DN-009
('773b5490-514a-4cf4-bbe0-21252db35e7a', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 207, 208', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('773b5490-514a-4cf4-bbe0-21252db35e7a', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 70, 71', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 11. DN-010
('697bfc4c-5f4b-4ba3-842e-c0a683e83d6f', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 206', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('697bfc4c-5f4b-4ba3-842e-c0a683e83d6f', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 66', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- 12. DN-011
('f7c8d9e0-1a2b-3c4d-5e6f-7a8b9c0d1e2f', '59/2020/QH14', 'Luật Doanh nghiệp 2020', 'Điều 31', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=141641', 1),
('f7c8d9e0-1a2b-3c4d-5e6f-7a8b9c0d1e2f', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Chương V (Từ Điều 46 đến Điều 55)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 2),

-- HKD-002
('b2c3d4e5-f6a7-4b5c-9d0e-1f2a3b4c5d6e', '01/2021/NĐ-CP', 'Nghị định 01/2021/NĐ-CP về đăng ký doanh nghiệp', 'Điều 90 (Đăng ký thay đổi nội dung đăng ký hộ kinh doanh)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146036', 1),

-- === HẠNG MỤC ĐẤT ĐAI ===
-- DD-001
('d5d9c792-5b9e-4e42-9a3b-2856247c7c30', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 133', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),

-- DD-002
('f4c3d251-7c2b-4e62-9e3d-4076269b9b52', '101/2024/NĐ-CP', 'Nghị định 101/2024/NĐ-CP về điều tra cơ bản đất đai, cấp GCN', 'Điều 38', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168270', 1),

-- DD-003
('a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 220', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),

-- DD-004
('b4c5d6e7-f8a9-4b0c-1d2e-3f4a5b6c7d8e', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 137, 138, 139, 140 (Cấp GCN lần đầu)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('b4c5d6e7-f8a9-4b0c-1d2e-3f4a5b6c7d8e', '101/2024/NĐ-CP', 'Nghị định 101/2024/NĐ-CP về điều tra cơ bản đất đai, cấp GCN', 'Điều 28, 31, 32, 33', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168270', 2),

-- DD-005
('c5d6e7f8-a9b0-4c1d-2e3f-4a5b6c7d8e9f', '101/2024/NĐ-CP', 'Nghị định 101/2024/NĐ-CP về điều tra cơ bản đất đai, cấp GCN', 'Điều 39 (Cấp lại Giấy chứng nhận bị mất)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168270', 1),

-- DD-006
('d6e7f8a9-b0c1-4d2e-3f4a-5b6c7d8e9f0a', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 45, Điều 133', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('d6e7f8a9-b0c1-4d2e-3f4a-5b6c7d8e9f0a', '91/2015/QH13', 'Bộ luật Dân sự 2015', 'Chương XXII, XXIII (Thừa kế)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=95874', 2),
('d6e7f8a9-b0c1-4d2e-3f4a-5b6c7d8e9f0a', '101/2024/NĐ-CP', 'Nghị định 101/2024/NĐ-CP về điều tra cơ bản đất đai, cấp GCN', 'Điều 37', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168270', 3),

-- DD-007
('e7f8a9b0-c1d2-4e3f-a4b5-6c7d8e9f0a1b', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 152 (Đính chính GCN đã cấp)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('e7f8a9b0-c1d2-4e3f-a4b5-6c7d8e9f0a1b', '101/2024/NĐ-CP', 'Nghị định 101/2024/NĐ-CP về điều tra cơ bản đất đai, cấp GCN', 'Điều 45 (Trình tự, thủ tục đính chính GCN)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168270', 2),

-- DD-008
('f8a9b0c1-d2e3-4f4a-b5c6-7d8e9f0a1b2c', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 121, 122 (Chuyển mục đích sử dụng đất)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('f8a9b0c1-d2e3-4f4a-b5c6-7d8e9f0a1b2c', '102/2024/NĐ-CP', 'Nghị định 102/2024/NĐ-CP quy định chi tiết thi hành Luật Đất đai', 'Điều 44, 45 (Trình tự chuyển mục đích SDĐ)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168273', 2),

-- DD-009
('a9b0c1d2-e3f4-4a5b-c6d7-8e9f0a1b2c3d', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 172, 173 (Thời hạn sử dụng đất, gia hạn)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('a9b0c1d2-e3f4-4a5b-c6d7-8e9f0a1b2c3d', '102/2024/NĐ-CP', 'Nghị định 102/2024/NĐ-CP quy định chi tiết thi hành Luật Đất đai', 'Điều 64 (Trình tự gia hạn sử dụng đất)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168273', 2),

-- DD-010
('b0c1d2e3-f4a5-4b6c-d7e8-9f0a1b2c3d4e', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 133, 149 (Thế chấp quyền sử dụng đất)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('b0c1d2e3-f4a5-4b6c-d7e8-9f0a1b2c3d4e', '99/2022/NĐ-CP', 'Nghị định 99/2022/NĐ-CP về đăng ký biện pháp bảo đảm', 'Điều 25, 26, 36 (Hồ sơ đăng ký thế chấp QSDĐ)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=158022', 2),

-- DD-011
('c1d2e3f4-a5b6-4c7d-e8f9-0a1b2c3d4e5f', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 133 (Đăng ký biến động)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('c1d2e3f4-a5b6-4c7d-e8f9-0a1b2c3d4e5f', '99/2022/NĐ-CP', 'Nghị định 99/2022/NĐ-CP về đăng ký biện pháp bảo đảm', 'Điều 38 (Xóa đăng ký biện pháp bảo đảm)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=158022', 2),

-- DD-012
('d2e3f4a5-b6c7-4d8e-f9a0-1b2c3d4e5f6a', '31/2024/QH15', 'Luật Đất đai 2024', 'Điều 166, 167 (Khai thác thông tin đất đai)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=164627', 1),
('d2e3f4a5-b6c7-4d8e-f9a0-1b2c3d4e5f6a', '101/2024/NĐ-CP', 'Nghị định 101/2024/NĐ-CP về điều tra cơ bản đất đai, cấp GCN', 'Điều 58, 60 (Cung cấp dữ liệu đất đai)', 'https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=168270', 2);

-- 6. Requirements
INSERT INTO procedure_requirements (procedure_id, name, description, is_required, copies, document_form, form_code, note, order_index)
VALUES
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', 'Giấy đề nghị đăng ký hộ kinh doanh', 'Bản chính Giấy đề nghị đăng ký hộ kinh doanh theo mẫu', true, 1, 'original', 'Phụ lục III-1', '', 1),
('e3b2c140-6b1a-4f51-8d2c-3965158a8a41', 'Bản sao CCCD/CMND/Hộ chiếu', 'Bản sao hợp lệ giấy tờ pháp lý của cá nhân/thành viên hộ gia đình', true, 1, 'copy', '', '', 2),
('d5d9c792-5b9e-4e42-9a3b-2856247c7c30', 'Hợp đồng chuyển nhượng quyền sử dụng đất', 'Hợp đồng chuyển nhượng đã được công chứng/chứng thực hợp pháp', true, 1, 'certified_copy', '', '', 1),
('d5d9c792-5b9e-4e42-9a3b-2856247c7c30', 'Giấy chứng nhận quyền sử dụng đất', 'Bản gốc Giấy chứng nhận (sổ đỏ/sổ hồng)', true, 1, 'original', '', '', 2)
ON CONFLICT DO NOTHING;
