-- check_demo_applications.sql: chạy trên database PROCEDURE sau 005_seed_demo_applications.sql.
-- Mong đợi với mỗi tài khoản đã seed: 8 hồ sơ (land 5, business 3), mỗi trạng thái ít nhất 1.

SELECT user_id, domain, status, count(*) AS so_ho_so
FROM (
    SELECT user_id, 'land' AS domain, status FROM public.land_procedure_records WHERE extra_data @> '{"seed": "demo"}'
    UNION ALL
    SELECT user_id, 'business' AS domain, status FROM public.business_procedure_records WHERE extra_data @> '{"seed": "demo"}'
) a
GROUP BY user_id, domain, status
ORDER BY user_id, domain, status;

-- Xóa toàn bộ hồ sơ ảo (bỏ comment khi cần dọn):
-- DELETE FROM public.land_procedure_records WHERE extra_data @> '{"seed": "demo"}';
-- DELETE FROM public.business_procedure_records WHERE extra_data @> '{"seed": "demo"}';
