# Location Service

Dịch vụ quản lý địa điểm tiếp nhận hồ sơ, geocoding, khoảng cách và dữ liệu bản đồ.

Service này sở hữu database riêng trong `database/` và migration riêng trong `alembic/`. Tích hợp Google Maps hoặc provider khác sẽ được đặt trong service này, không gọi trực tiếp từ service khác.
