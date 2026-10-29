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
  "query": "Trung tâm phục vụ hành chính công"
}
```

Mã ví dụ chỉ minh họa kiểu dữ liệu; lấy mã thật cùng phiên bản từ danh mục. `legacy` bắt buộc tỉnh/huyện, xã có thể `null`; `current` bắt buộc tỉnh/xã, huyện chỉ được `null`. `detail` 1–300 ký tự, `query` 1–150; trả tối đa 5 địa điểm gần nhất theo khoảng cách đường thẳng, không lọc bán kính; request chỉ có `address` và `query`. Không gửi key/URL provider hay `user_id`.

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

`items` có thể rỗng hoặc ít hơn 5; xếp khoảng cách trong nguồn dữ liệu hiện có, không phải danh sách đầy đủ. `distance_meters` là khoảng cách đường thẳng, không phải quãng đường lái xe. Location không xác nhận thẩm quyền pháp lý của cơ quan. `resolved_address` chỉ để người dùng đối chiếu; không ghi ngược vào User/OCR.

Trung tâm hành chính công Đà Nẵng dùng snapshot địa chỉ từ cổng thông tin chính thức.
`provider: official_directory` có `source_url`, `source_published_at`, `source_checked_at`;
tọa độ đối chiếu địa chỉ bằng Goong, không phải pin cơ quan do website nguồn cung cấp.
`coverage.source` là `official_directory` hoặc `goong_candidates`; với danh mục chính thức,
`matched_offices`/`located_offices` thể hiện phạm vi dữ liệu. Chưa có tọa độ được báo bằng
`OFFICIAL_COORDINATES_INCOMPLETE` và `is_partial: true`; không thay thế bằng tâm xã.

Thông tin thẻ cơ quan có thêm `unit_name`, `category_label`, `contact_phone`, `contact_label`.
Số điện thoại được đối chiếu trên website hiện hành, kèm `contact_source_url`; nhãn
phân biệt đường dây thủ tục hành chính, đường dây nóng UBND và điện thoại UBND.
Không lấy số di động từ danh mục 2025 làm số tiếp nhận hiện tại.
`photo` là nullable, gồm đường dẫn ảnh frontend `/images/offices/...`, alt text, credit và nguồn bài viết;
`opening_hours` nullable, chỉ có `text` và `source_url` khi xác minh được lịch của đúng cơ quan.
Nguồn có thể là Google Places khớp chính xác số nhà/tên đường hoặc website `.gov.vn`.
`maps_url` và `map_embed_url` luôn dùng địa chỉ đang hiển thị; chỉ gắn Place ID khi đã đối chiếu POI.
Hòa Khánh dùng POI UBND phường tại 168 Nguyễn Sinh Sắc thay cho điểm geocode số nhà.
Nếu không có giờ đã xác minh, frontend hiển thị “Giờ hành chính” và nhắc liên hệ trước khi đến.
`appointment_url`/`appointment_source_url` dẫn tới lịch theo ngày/quầy được website
phường liên kết; không xem khung hẹn còn trống là giờ làm việc cố định cả tuần.
`website_url` cho phép mở website cơ quan. Link chỉ đường dùng địa chỉ xuất phát bằng
văn bản; fallback tọa độ giữ 7 chữ số thập phân để tránh sai số do làm tròn.
FE hiển thị “Liên hệ để xác nhận lịch tiếp nhận” khi thiếu giờ, và trạng thái ảnh chưa cập nhật
khi thiếu ảnh. Không sinh giờ làm việc hoặc trạng thái đang mở cửa từ lịch hành chính chung.

Ứng viên Goong chỉ được trả khi xác minh khớp tỉnh/thành hiện hành, qua metadata
và phần tỉnh/thành cuối địa chỉ; kết quả mâu thuẫn hoặc thiếu căn cứ bị loại.
`coverage.province` nêu phạm vi, `excluded_outside_province` đếm ứng viên bị loại
vì sai tỉnh hoặc chưa xác minh được tỉnh. Không bù từ tỉnh khác để đủ 5 kết quả.
`LOCAL_RESULTS_UNAVAILABLE` thông báo nguồn chưa có kết quả phù hợp tại địa phương.
FE chỉ cho chọn tỉnh/xã hiện hành; API vẫn hỗ trợ `legacy` cho bên gọi cũ.

## Lỗi và vận hành

Envelope lỗi: `{ "data": null, "error": {"code": "...", "message": "...", "details": []}, "request_id": "..." }`; `details` chỉ có khi cần. Mã: `BAD_REQUEST` 400; `UNAUTHORIZED`/`TOKEN_EXPIRED` 401; `VALIDATION_ERROR`, `ADDRESS_NOT_FOUND`, `ADDRESS_AMBIGUOUS` 422; `RATE_LIMITED` 429; `GOONG_BAD_RESPONSE` 502; `LOCATION_SEARCH_DISABLED`, `GOONG_NOT_CONFIGURED`, `GOONG_ACCESS_DENIED`, `GOONG_QUOTA_EXCEEDED`, `GOONG_UNAVAILABLE` 503; `GOONG_TIMEOUT` 504.

Tìm kiếm tắt mặc định. Cấu hình `LOCATION_SEARCH_ENABLED=true` và `GOONG_API_KEY` chỉ trên backend; timeout Goong 5 giây, toàn search 20 giây, 4 provider calls/giây, 5 search/phút/user, ngân sách mặc định 1.000 provider calls/ngày. Giới hạn trong RAM chỉ chính xác cho một tiến trình và reset khi restart. Triển khai nhiều worker/replica phải dùng counter bền vững dùng chung trước khi bật search. Không cache hoặc lưu lâu dài địa chỉ/kết quả provider. Không log key, JWT, URL query provider hay địa chỉ cá nhân.

Kiểm tra cấu hình: `python services/location-service/scripts/check_goong.py`; probe thật opt-in bằng `--run-probe` chỉ khi có key và sẽ tốn quota. Tắt nhanh bằng `LOCATION_SEARCH_ENABLED=false`; danh mục vẫn hoạt động. Không `docker compose down` toàn hệ thống để rollback riêng Location.
# Bổ sung hotline và giờ làm việc qua Google Search

Tùy chọn backend: `LOCATION_WEB_LOOKUP_ENABLED=true`, `GOOGLE_MAPS_API_KEY`, `GEMINI_API_KEY`,
`LOCATION_WEB_MODEL`, `LOCATION_WEB_DAILY_BUDGET=20`. Không gửi key ra frontend.
Google Places được hỏi trước về giờ làm việc và chỉ nhận Place khớp đúng số nhà/tên
đường. Google Search grounding tìm website; Gemini đọc; backend tải nguồn chính thức
`.gov.vn` được dẫn và kiểm tra tên/địa chỉ cùng trích dẫn trước khi chấp nhận.

1. `POST /api/locations/offices/search` giữ contract cũ, có thể thêm `data.web_lookup_id`.
2. Hiển thị ngay các cơ quan. Gọi `POST /api/locations/offices/enrich`, JSON chỉ có
   `lookup_id`. Cùng cookie/JWT, giới hạn 5 lượt/phút/người dùng riêng với search.
3. Response envelope `data.items` tối đa 5 patch gồm `place_id`, `web_lookup_status`
   (`verified`, `unverified`, `unavailable`), tùy chọn `contact_phone`,
   `contact_label`, `contact_source_url`, `contact_evidence`,
   `opening_hours: {text, source_url, source_type?, evidence?}`, `web_checked_at`.
4. `data.google_search_widgets` chứa Google Search suggestions HTML; chỉ dùng
   iframe sandbox, không chèn vào DOM chính. Cache fact phải giữ attribution này.
5. Patch chỉ bổ sung hotline/giờ còn thiếu. Không sửa vị trí/thứ tự và không ghi đè
   liên hệ hiện có đã đối chiếu. Bỏ qua phản hồi nếu người dùng đã đổi tìm kiếm.

Handle thuộc người dùng, hết hạn 5 phút; sai người dùng/expired trả 422,
không đăng nhập 401, rate limit 429. Lỗi quota/provider trả patch `unavailable`,
không biến thành lỗi tìm cơ quan. Cache RAM fact xác minh 6 giờ, tối đa 512.
Thông tin không tìm được hoặc không đủ bằng chứng vẫn để chưa xác minh; không
suy đoán giờ hành chính chung. Không gửi địa chỉ công dân lên Gemini.
