# Notification database

Database riêng của Notification Service, gồm `notifications`, trạng thái đọc và cấu hình mute/suppress. Các bảng tham chiếu người nhận bằng `user_id`, không dùng chung database với User Service.
