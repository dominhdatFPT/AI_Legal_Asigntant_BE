# Location Service API

Contract đầy đủ: [`services/location-service/openapi.yaml`](../../services/location-service/openapi.yaml). Gọi qua Gateway tại `http://localhost:8080/api/locations`; trong Docker dùng `http://location-service:8000/api/v1/locations`. Search yêu cầu Bearer JWT hoặc cookie `access_token`; năm API danh mục và health vẫn public. `GET /offices` là contract cũ chưa triển khai; dùng `POST /offices/search`.

## Danh mục

- `GET /administrative/legacy/provinces`
- `GET /administrative/legacy/provinces/{province_code}/districts`
- `GET /administrative/legacy/districts/{district_code}/wards`
- `GET /administrative/current/provinces`
- `GET /administrative/current/provinces/{province_code}/wards`

Response: `{ "data": {"items": [{"code": 1, "name": "..."}], "source": "provinces.open-api.vn"}, "error": null, "request_id": "..." }`. Cache tối đa 256 mục, TTL 24 giờ theo phiên bản/đường dẫn. Lỗi upstream không cache.

## Tìm cơ quan

`POST /offices/search`:

```json
{
  "address": {"administrative_version": "current", "detail": "12 Lê Duẩn", "province_code": 79, "district_code": null, "ward_code": 26740},
  "query": "Trung tâm phục vụ hành chính công",
  "radius_meters": 10000,
  "limit": 5
}
```

Mã ví dụ chỉ minh họa kiểu dữ liệu; lấy mã thật cùng phiên bản từ danh mục. `legacy` bắt buộc tỉnh/huyện, xã có thể `null`; `current` bắt buộc tỉnh/xã, huyện chỉ được `null`. `detail` 1–300 ký tự, `query` 1–150; bán kính 1.000–50.000 m, `limit` 1–10. Không gửi key/URL provider hay `user_id`.

Response mẫu:

```json
{
  "data": {
    "origin": {"input_address": "12 Lê Duẩn, Phường Bến Nghé, Thành phố Hồ Chí Minh, Việt Nam", "resolved_address": "12 Lê Duẩn, Bến Nghé, Hồ Chí Minh", "latitude": 10.7803, "longitude": 106.7009},
    "items": [{"provider": "goong", "place_id": "opaque-provider-id", "name": "Cơ quan mẫu", "address": "29 Lê Duẩn", "latitude": 10.7811, "longitude": 106.7012, "distance_meters": 96, "distance_type": "straight_line", "directions_url": "https://www.google.com/maps/dir/?api=1&destination=10.7811%2C106.7012&origin=10.7803%2C106.7009"}],
    "is_partial": false,
    "warnings": []
  },
  "error": null,
  "request_id": "req-example"
}
```

`items` có thể rỗng hoặc ít hơn `limit`; đây chỉ là tập ứng viên Goong trả về, không phải danh sách đầy đủ. `distance_meters` là khoảng cách đường thẳng, không phải quãng đường lái xe. Location không xác nhận thẩm quyền pháp lý của cơ quan. `resolved_address` chỉ để người dùng đối chiếu; không ghi ngược vào User/OCR.

## Lỗi và vận hành

Envelope lỗi: `{ "data": null, "error": {"code": "...", "message": "...", "details": []}, "request_id": "..." }`; `details` chỉ có khi cần. Mã: `BAD_REQUEST` 400; `UNAUTHORIZED`/`TOKEN_EXPIRED` 401; `VALIDATION_ERROR`, `ADDRESS_NOT_FOUND`, `ADDRESS_AMBIGUOUS` 422; `RATE_LIMITED` 429; `GOONG_BAD_RESPONSE` 502; `LOCATION_SEARCH_DISABLED`, `GOONG_NOT_CONFIGURED`, `GOONG_ACCESS_DENIED`, `GOONG_QUOTA_EXCEEDED`, `GOONG_UNAVAILABLE` 503; `GOONG_TIMEOUT` 504.

Tìm kiếm tắt mặc định. Cấu hình `LOCATION_SEARCH_ENABLED=true` và `GOONG_API_KEY` chỉ trên backend; timeout Goong 5 giây, toàn search 20 giây, 4 provider calls/giây, 5 search/phút/user, ngân sách mặc định 1.000 provider calls/ngày. Giới hạn trong RAM chỉ chính xác cho một tiến trình và reset khi restart. Triển khai nhiều worker/replica phải dùng counter bền vững dùng chung trước khi bật search. Không cache hoặc lưu lâu dài địa chỉ/kết quả provider. Không log key, JWT, URL query provider hay địa chỉ cá nhân.

Kiểm tra cấu hình: `python services/location-service/scripts/check_goong.py`; probe thật opt-in bằng `--run-probe` chỉ khi có key và sẽ tốn quota. Tắt nhanh bằng `LOCATION_SEARCH_ENABLED=false`; danh mục vẫn hoạt động. Không `docker compose down` toàn hệ thống để rollback riêng Location.
