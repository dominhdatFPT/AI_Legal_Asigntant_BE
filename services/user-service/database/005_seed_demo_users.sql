-- DỮ LIỆU MẪU cho màn hình Quản lý người dùng (chỉ dùng khi dev/demo, KHÔNG chạy trên database thật).
-- Chạy SAU 001 → 004 (và 007 nếu đã deploy chức năng Luật sư; file không dùng cột created_as_lawyer nữa). Chạy lại nhiều lần không sao: email / username đã có thì bỏ qua.
-- Mọi tài khoản mẫu đăng nhập bằng: tên đăng nhập = email, mật khẩu = Demo@123
-- Xóa dữ liệu mẫu: delete from users where email like '%@demo.ai-legal.local';

-- Mật khẩu chung Demo@123 (bcrypt, pgcrypto tạo ngay trong câu lệnh)
with
pw as (select crypt('Demo@123', gen_salt('bf', 12)) as hash),
adm as (select id from users where lower(username) = 'admin2026' limit 1)
insert into users (email, username, password_hash, full_name, phone, auth_provider, role, status,
                   locked_reason, locked_by, locked_at, locked_until,
                   created_by, created_at, deleted_at, deleted_by)
select v.email, v.email, pw.hash, v.full_name, v.phone, 'local', v.role::user_role, v.status::user_status,
       v.locked_reason,
       case when v.status = 'locked' then adm.id end,
       case when v.status = 'locked' then now() - interval '1 day' end,
       v.locked_until,
       case when v.by_admin then adm.id end,
       v.created_at,
       v.deleted_at,
       case when v.deleted_at is not null then adm.id end
from pw cross join adm cross join (values
    -- email,                               họ tên,           SĐT,          role,      status,   lý do khóa,                      khóa đến,                    admin tạo, ngày đăng ký,                         đã xóa lúc
    ('an.nguyen@demo.ai-legal.local',     'Nguyễn Văn An',  '0912345678', 'citizen', 'active', null,                            null::timestamptz,           false, now() - interval '120 days', null::timestamptz),
    ('mai.tran@demo.ai-legal.local',      'Trần Thị Mai',   '0988765432', 'citizen', 'active', null,                            null,                        false, now() - interval '90 days',  null),
    ('nam.le@demo.ai-legal.local',        'Lê Hoàng Nam',   '0935210888', 'citizen', 'active', null,                            null,                        false, now() - interval '60 days',  null),
    ('ha.pham@demo.ai-legal.local',       'Phạm Thu Hà',    '0918334221', 'lawyer',  'active', null,                            null,                        false, now() - interval '45 days',  null),
    ('minh.vo@demo.ai-legal.local',       'Võ Đức Minh',    '0907123456', 'lawyer',  'active', null,                            null,                        true,  now() - interval '30 days',  null),
    ('khoa.bui@demo.ai-legal.local',      'Bùi Anh Khoa',   '0977001122', 'lawyer',  'active', null,                            null,                        true,  now() - interval '3 days',   null),
    ('tuan.dang@demo.ai-legal.local',     'Đặng Quốc Tuấn', '0903445112', 'citizen', 'locked', 'Đăng nội dung spam nhiều lần',  now() + interval '7 days',   false, now() - interval '200 days', null),
    ('lan.hoang@demo.ai-legal.local',     'Hoàng Thị Lan',  '0965778899', 'lawyer',  'locked', 'Giả mạo thẻ luật sư',           null,                        false, now() - interval '150 days', null),
    ('phuc.do@demo.ai-legal.local',       'Đỗ Hồng Phúc',   '0938556677', 'citizen', 'active', null,                            null,                        false, now() - interval '100 days', now() - interval '5 days'),
    ('admin.hcm@demo.ai-legal.local',     'Quản trị viên HCM', '0281234567', 'admin', 'active', null,                           null,                        true, now() - interval '20 days',  null)
) as v(email, full_name, phone, role, status, locked_reason, locked_until, by_admin, created_at, deleted_at)
on conflict do nothing;

-- Hồ sơ CCCD mẫu cho 2 người (để thấy has_citizen_profile = true). Số CCCD là số giả.
insert into citizen_profiles (user_id, id_number, full_name, date_of_birth, gender, nationality,
                              issue_date, issue_place, expiry_date,
                              permanent_detail, permanent_ward, permanent_province,
                              current_same_as_permanent, ethnicity, source, declared_at, consented_at)
select u.id, v.id_number, upper(u.full_name), v.dob, v.gender, 'Việt Nam',
       date '2021-09-20', 'Cục Cảnh sát QLHC về TTXH', v.dob + interval '40 years',
       v.detail, v.ward, v.province, true, 'Kinh', 'manual', now(), now()
from (values
    ('an.nguyen@demo.ai-legal.local', '001095099001', date '1995-08-15', 'Nam', 'Số 45 ngõ 120 Hoàng Hoa Thám', 'Phường Ngọc Hà', 'Thành phố Hà Nội'),
    ('ha.pham@demo.ai-legal.local',   '079190099002', date '1990-03-02', 'Nữ',  'Số 12 Lê Lợi',                 'Phường Sài Gòn', 'Thành phố Hồ Chí Minh')
) as v(email, id_number, dob, gender, detail, ward, province)
join users u on u.email = v.email
on conflict do nothing;

-- Kiểm tra: phải thấy 10 tài khoản mẫu
select full_name, email, role, status, locked_until, created_by is not null as admin_tao,
       deleted_at is not null as da_xoa,
       exists (select 1 from citizen_profiles c where c.user_id = users.id) as co_ho_so
from users
where email like '%@demo.ai-legal.local'
order by created_at desc;
