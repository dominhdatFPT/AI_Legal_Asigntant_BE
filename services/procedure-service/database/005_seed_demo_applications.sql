-- 005_seed_demo_applications.sql
-- Thêm 8 hồ sơ ảo (5 đất đai, 3 kinh doanh) cho MỘT tài khoản để test giao diện.
--
-- Cách dùng:
--   1. Mở SQL editor của database USER (user-service), chạy:
--        select id, email, role from users where email = '<email tài khoản>';
--   2. Copy cột id, dán vào v_user_id bên dưới (thay chuỗi 0000...).
--   3. Chạy file này trên SQL editor của database PROCEDURE (cần đã chạy 001, 002, 003, 004).
--   4. Chạy check_demo_applications.sql để xem kết quả.
--
-- Chạy lại cho cùng tài khoản: hồ sơ ảo cũ bị xóa rồi tạo lại, không nhân đôi.
-- Hồ sơ ảo có extra_data = {"seed": "demo"}; hồ sơ thật của user không bị đụng tới.

DO $$
DECLARE
    v_user_id     uuid := '00000000-0000-0000-0000-000000000000';  -- << dán id tài khoản vào đây
    v_full_name   text := 'Nguyễn Văn An';
    v_cccd        text := '001095012849';
    v_phone       text := '0988123456';
    v_demo        jsonb := '{"seed": "demo"}';
BEGIN
    IF v_user_id = '00000000-0000-0000-0000-000000000000' THEN
        RAISE EXCEPTION 'Chưa điền v_user_id (xem hướng dẫn ở đầu file).';
    END IF;
    IF (SELECT count(*) FROM public.procedures
        WHERE code IN ('DD-001', 'DD-002', 'DD-003', 'DD-004', 'DD-010', 'HKD-001', 'HKD-002')) < 7 THEN
        RAISE EXCEPTION 'Thiếu thủ tục trong bảng procedures, hãy chạy 002_seed_procedures.sql trước.';
    END IF;

    DELETE FROM public.land_procedure_records WHERE user_id = v_user_id AND extra_data @> v_demo;
    DELETE FROM public.business_procedure_records WHERE user_id = v_user_id AND extra_data @> v_demo;

    INSERT INTO public.land_procedure_records
        (user_id, procedure_code, procedure_id, status, applicant_name, applicant_id_number, applicant_phone,
         plot_number, map_sheet_number, land_address, land_area, form_data, extra_data, created_at, updated_at)
    SELECT v_user_id, d.code, p.id, d.status, v_full_name, v_cccd, v_phone,
           d.plot, d.map_sheet, d.address, d.area,
           jsonb_build_object('plot_number', d.plot, 'map_sheet_number', d.map_sheet,
                              'land_address', d.address, 'land_area', d.area),
           v_demo, now() - d.days_ago * interval '1 day', now() - d.days_ago * interval '1 day'
    FROM (VALUES
        ('DD-001', 'submitted',  '128', '14', 'Thửa 128, Tờ 14, Phường Kim Mã, Quận Ba Đình, TP. Hà Nội', 85.5,  1),
        ('DD-002', 'processing', '56',  '19', 'Thửa 56, Tờ 19, Xã Bình Chánh, Huyện Bình Chánh, TP. HCM', 120.0, 4),
        ('DD-003', 'completed',  '91',  '31', 'Thửa 91, Tờ 31, Phường Tân Phú, Quận 7, TP. HCM',          200.0, 12),
        ('DD-004', 'draft',      '45',  '08', 'Thửa 45, Tờ 08, Phường Đa Kao, Quận 1, TP. HCM',           110.2, 20),
        ('DD-010', 'cancelled',  '17',  '05', 'Thửa 17, Tờ 05, Xã Tân An, TP. Hội An, Quảng Nam',         250.0, 29)
    ) AS d(code, status, plot, map_sheet, address, area, days_ago)
    JOIN public.procedures p ON p.code = d.code;

    INSERT INTO public.business_procedure_records
        (user_id, procedure_code, procedure_id, status, applicant_name, applicant_id_number, applicant_phone,
         business_type, business_name_vn, headquarters_address, charter_capital, form_data, extra_data, created_at, updated_at)
    SELECT v_user_id, d.code, p.id, d.status, v_full_name, v_cccd, v_phone,
           'household', d.biz_name, d.address, d.capital,
           jsonb_build_object('business_name_vn', d.biz_name, 'headquarters_address', d.address,
                              'charter_capital', d.capital),
           v_demo, now() - d.days_ago * interval '1 day', now() - d.days_ago * interval '1 day'
    FROM (VALUES
        ('HKD-001', 'processing', 'HKD Tạp Hóa An Nhiên',            'Số 45 Đường số 7, P. An Phú, TP. Thủ Đức', 50000000,  2),
        ('HKD-002', 'submitted',  'HKD Dịch Vụ Sửa Chữa Minh Phát',  '102 Hoàng Hoa Thám, P.7, Q. Bình Thạnh',   80000000,  7),
        ('HKD-001', 'completed',  'HKD Cà Phê Góc Phố',              '12 Lê Lợi, P. Bến Nghé, Quận 1, TP. HCM',  120000000, 25)
    ) AS d(code, status, biz_name, address, capital, days_ago)
    JOIN public.procedures p ON p.code = d.code;

    RAISE NOTICE 'Đã tạo hồ sơ ảo cho %', v_user_id;
END $$;
