# Location Service

Dịch vụ quản lý địa điểm tiếp nhận hồ sơ, geocoding, khoảng cách và dữ liệu bản đồ.

Service này sở hữu database riêng trong `database/` và migration riêng trong `alembic/`. Tích hợp Google Maps hoặc provider khác sẽ được đặt trong service này, không gọi trực tiếp từ service khác.

## Cấu hình Goong (Task L2)

Tìm kiếm đang tắt mặc định và việc import/startup không gọi Goong. Cấu hình backend:

```dotenv
GOONG_API_KEY=
LOCATION_SEARCH_ENABLED=false
LOCATION_GOONG_TIMEOUT_SECONDS=5
LOCATION_SEARCH_TIMEOUT_SECONDS=20
LOCATION_GOONG_MAX_RPS=4
LOCATION_GOONG_DAILY_BUDGET=1000
```

Chỉ lưu key thật trong `.env` đã được Git ignore. Thiếu key hoặc cờ tắt không ảnh hưởng health và các API danh mục; search trả lần lượt `GOONG_NOT_CONFIGURED` hoặc `LOCATION_SEARCH_DISABLED`. Khi chạy nhiều worker/replica, thay bộ đếm trong RAM bằng counter dùng chung bền vững trước khi bật tìm kiếm.

Kiểm tra cấu hình mà không gọi mạng:

```powershell
python services/location-service/scripts/check_goong.py
```

Kiểm tra thật là opt-in, gọi tối đa 10 request cho hai khu vực công cộng, ba từ khóa và Place Detail. Lệnh này có thể tiêu tốn quota:

```powershell
python services/location-service/scripts/check_goong.py --run-probe
```

Script chỉ in trạng thái, số kết quả và tên/địa chỉ công cộng; không in key, URL request hay raw response. Kiểm tra quota/hạn mức thực tế trong tài khoản Goong sau khi chạy.

## Danh mục hành chính (Task L3)

`app/main.py` đăng ký health, router và handler. Năm endpoint danh mục công khai nằm ở `app/api/administrative.py`; adapter nguồn `provinces.open-api.vn` và cache TTL 24 giờ, tối đa 256 mục nằm ở `app/services/administrative_catalog.py`. Search xác minh quan hệ cha con trước Goong.

Response danh mục giữ `data.items`, `data.source`, `error` để form OCR dùng tiếp. `request_id` có trong body và header `X-Request-ID`; lỗi dùng `{data, error, request_id}`. Health vẫn trả đúng payload cũ. Tìm cơ quan đã đăng ký ở `POST /api/v1/locations/offices/search`, yêu cầu đăng nhập; contract và ví dụ nằm ở `docs/api/LOCATION_API.md`.

Kiểm tra hồi quy riêng:

```powershell
python -m pytest tests/test_location_administrative.py tests/test_location_contract.py -q
```
