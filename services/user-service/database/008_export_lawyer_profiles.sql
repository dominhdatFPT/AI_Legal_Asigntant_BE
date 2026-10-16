-- Sinh câu lệnh tạo hồ sơ luật sư "chưa hoàn thiện" cho mọi tài khoản vai trò lawyer (CHỈ ĐỌC).
-- Hai database tách riêng nên không insert thẳng được: file này chạy trên database USER, in ra MỘT câu
-- INSERT; copy nguyên ô kết quả sang SQL Editor của database LAWYER rồi Run.
--
-- Thứ tự: lawyer-service/database/001_lawyer_schema.sql → user-service 007_lawyer_role.sql → file này.
-- Gồm tài khoản created_as_lawyer cũ (007 đã đổi sang lawyer) và luật sư từng được đổi vai trò bằng tay.
-- Tài khoản đã xóa mềm → hồ sơ hidden (reason deleted). Câu INSERT có "on conflict do nothing":
-- chạy lại nhiều lần không tạo trùng, không ghi đè hồ sơ luật sư đã có.
-- Kết quả là NULL nghĩa là không có tài khoản luật sư nào, không cần làm gì.

select
    'insert into lawyer_profiles (user_id, source, status, hidden_at, hidden_reason) values '
    || string_agg(
         case when deleted_at is null
              then format('(%L, ''admin_created'', ''incomplete'', null, null)', id)
              else format('(%L, ''admin_created'', ''hidden'', %L, ''deleted'')', id, deleted_at)
         end, ', ' order by created_at)
    || ' on conflict (user_id) do nothing;' as cau_lenh_cho_lawyer_db
from users
where role = 'lawyer';
