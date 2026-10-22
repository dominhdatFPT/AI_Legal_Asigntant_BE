# Kế hoạch hoàn thiện Location Service

Ngày đối chiếu workspace: 15/10/2026. Đây là kế hoạch thực hiện và contract đề xuất, chưa phải API mới đã chạy. Không sửa runtime trong lần lập kế hoạch này.

## 1. Kết quả cần đạt

Người dùng nhập số nhà/đường/khu/thôn, chọn đơn vị hành chính từ dropdown dùng chung. Location nhận địa chỉ, tìm tọa độ bằng Goong, tìm các cơ quan theo từ khóa quanh vị trí đó, trả danh sách cho FE. Người dùng chọn kết quả rồi mở Google Maps chỉ đường bằng tọa độ.

- Giữ nguyên các API danh mục đang phục vụ form OCR.
- Hỗ trợ danh mục `legacy` ba cấp và `current` hai cấp; không trộn mã giữa hai bộ.
- Tìm kiếm bằng từ khóa tên/loại cơ quan. Có thể thử tên thủ tục như từ khóa, nhưng không cam kết Goong hiểu được nơi có thẩm quyền.
- Không bắt Procedure service xác định thẩm quyền để tính năng này chạy.
- Không cần lưu hồ sơ người dùng, lịch sử tìm kiếm hoặc danh sách cơ quan vào database cho phạm vi này.
- Không cần RabbitMQ, worker, LLM, Google Places key hoặc nhúng bản đồ.
- Danh sách sắp xếp theo khoảng cách đường thẳng trong tập kết quả đã lấy được; không phải toàn bộ cơ quan trong khu vực, không phải quãng đường đi xe.

## 2. Hiện trạng đã kiểm tra

| Hạng mục | Tình trạng tại workspace |
|---|---|
| `services/location-service/app/main.py` | Có health và 5 API danh mục địa chỉ; gọi provinces.open-api.vn |
| Cache danh mục | `lru_cache(maxsize=256)`, chưa có thời gian hết hạn |
| `services/location-service/openapi.yaml` | Chỉ có `GET /offices` ở trạng thái `planned`; thiếu contract các API danh mục |
| URL trong OpenAPI | Cần sửa: Gateway `/api/locations`, nội bộ `/api/v1/locations` |
| Gateway | Đã chuyển `/api/locations/...` sang `/api/v1/locations/...`; không cần thêm route cho search |
| Docker | Đã cài common_auth, mount public key; chưa truyền Goong key |
| FE `public/auth-api.js` | Có `fetchAdministrativeUnits(path)` dùng chung, trả về `data` |
| FE `detail-information.html` | Đang gọi cả `legacy` và `current`; có xử lý bỏ hậu tố hành chính bị trùng |
| Tests | Có health/config/OpenAPI chung và test FE OCR; chưa có bộ test nghiệp vụ Location riêng |
| Git | Backend ở `HIEN/fix_ocr`, frontend ở `HIEN/fix_detail_ocr`; có thay đổi chưa commit, gồm code Location danh mục |

Đây là trạng thái local; không suy ra các thay đổi đó đã có trên remote/dev.

## 3. Ranh giới để giảm xung đột

| Phạm vi | Cách làm |
|---|---|
| `backend/services/location-service/**` | Phần việc chính của người làm Location |
| `backend/tests/test_location_*.py`, fixture riêng | Tạo mới để CI hiện tại tự chạy, tránh sửa test của service khác |
| `backend/docs/api/LOCATION_API.md`, tài liệu task này | Tài liệu riêng |
| `backend/docker-compose.yml` | Chỉ thêm biến vào block `location-service`; không format lại toàn file |
| `backend/.env.example` | Chỉ thêm nhóm biến Location với giá trị mẫu, không key thật |
| `backend/docs/API_GUIDELINES.md` | Chỉ bổ sung mã lỗi mới nếu cần, qua MR có review theo quy ước nhóm |
| `backend/libs/common_auth/**` | Dùng public API hiện có; không sửa logic thư viện chung |
| Gateway/CORS | Dùng route và CORS hiện có; không cần thay vì search nằm dưới `/api/locations/` |
| Document/OCR, User, Procedure, Contract, Agent, Notification | Không đổi code, schema, database, payload hoặc dependency |
| Frontend | Là repo riêng và task bàn giao riêng; phối hợp chủ file trước khi chạm auth helper/trang dùng chung |

Không thể bảo đảm tuyệt đối không phát sinh Git conflict khi người khác sửa cùng file. Mục tiêu là cô lập phạm vi, giữ contract cũ và có regression test để phát hiện hỏng tích hợp.

## L0 — Chốt baseline và nhánh làm việc

**Làm trước mọi refactor.**

1. Chạy Git trong đúng repo `backend`, không ở repo ngoài cùng. FE dùng repo `frontend` riêng.
2. Xem `git status --short`, `git diff -- services/location-service/app/main.py`, rồi fetch remote để đối chiếu `dev` thực tế.
3. Kiểm tra 5 API danh mục đã có trên dev chưa. Nếu chưa, tách đúng phần Location thành commit/MR phụ thuộc hoặc tạo commit nền nhỏ; không cherry-pick nguyên commit OCR nếu nó chứa nhiều service.
4. Giữ nguyên mọi thay đổi chưa commit của người khác. Không reset, không ghi đè từ dev, không `git add .` ở workspace đang có nhiều việc.
5. Tạo nhánh tính năng riêng từ baseline đã xác định; tuân thủ quy ước nhánh của nhóm. Nếu cần làm song song, dùng checkout/worktree sạch sau khi đã bảo toàn thay đổi hiện tại.
6. Ghi lại commit nền, các test đang đỏ sẵn và các file thuộc task này.

**Đầu ra:** baseline có 5 API danh mục, không chứa các sửa OCR/Contract ngoài phạm vi.

**Xong khi:** biết chính xác diff nào thuộc Location và nhánh sẽ merge vào đâu; không mất thay đổi local.

## L1 — Chốt API contract với FE

**Phụ thuộc:** L0. File: `services/location-service/openapi.yaml`, `docs/api/LOCATION_API.md` mới.

Quy ước nhóm trong `docs/API_GUIDELINES.md` yêu cầu contract được review trước khi triển khai API mới. Chuẩn bị MR contract đầy đủ để FE cùng làm; không coi một dòng `planned` là endpoint đã dùng được.

Giữ nguyên 5 API đang chạy (đường dẫn bên dưới dành cho FE):

| Method | URL |
|---|---|
| GET | `/api/locations/administrative/legacy/provinces` |
| GET | `/api/locations/administrative/legacy/provinces/{province_code}/districts` |
| GET | `/api/locations/administrative/legacy/districts/{district_code}/wards` |
| GET | `/api/locations/administrative/current/provinces` |
| GET | `/api/locations/administrative/current/provinces/{province_code}/wards` |

Thêm endpoint đề xuất:

`POST /api/locations/offices/search`

Nội bộ Docker: `POST http://location-service:8000/api/v1/locations/offices/search`.

Request đề xuất:

```json
{
  "address": {
    "administrative_version": "current",
    "detail": "Số nhà và tên đường do người dùng nhập",
    "province_code": 1,
    "district_code": null,
    "ward_code": 2
  },
  "query": "Trung tâm phục vụ hành chính công",
  "radius_meters": 10000,
  "limit": 5
}
```

Các mã `1`, `2` trên chỉ minh họa kiểu dữ liệu, không phải một cặp địa chỉ được xác minh. FE lấy mã thật từ danh mục cùng phiên bản.

Validation đề xuất:

- `administrative_version`: bắt buộc `legacy` hoặc `current`, không tự đoán từ chuỗi.
- Mã hành chính: số nguyên dương, giữ tương thích với `code` mà API hiện tại trả về.
- `legacy`: tỉnh và huyện bắt buộc, xã có thể null để xử lý địa chỉ giấy tờ chỉ ghi hai ô. Ghi rõ độ chính xác đầu vào có thể thấp hơn.
- `current`: tỉnh và xã/phường bắt buộc; huyện phải null.
- `detail`: trim, 1–300 ký tự; `query`: trim, 1–150 ký tự. Giới hạn do dự án đề xuất, ghi vào OpenAPI và test.
- `radius_meters`: mặc định 10000, từ 1000 đến 50000; `limit`: mặc định 5, từ 1 đến 10.
- Không nhận URL Goong hoặc API key từ FE.
- Không bắt `procedure_code`, không gửi `user_id` để chứng minh quyền.

Response đề xuất (dữ liệu giả để diễn tả cấu trúc):

```json
{
  "data": {
    "origin": {
      "input_address": "Địa chỉ đã ghép",
      "resolved_address": "Địa chỉ Goong nhận diện",
      "latitude": 21.0,
      "longitude": 105.8
    },
    "items": [
      {
        "provider": "goong",
        "place_id": "opaque-provider-id",
        "name": "Tên cơ quan mẫu",
        "address": "Địa chỉ cơ quan mẫu",
        "latitude": 21.01,
        "longitude": 105.81,
        "distance_meters": 1521,
        "distance_type": "straight_line",
        "directions_url": "https://www.google.com/maps/dir/?api=1&destination=21.01%2C105.81"
      }
    ],
    "is_partial": false,
    "warnings": []
  },
  "error": null,
  "request_id": "req-example"
}
```

- `place_id` là chuỗi của Goong, không ép thành UUID và không dùng làm Google Place ID.
- Không bịa giờ làm việc, số điện thoại, đánh giá hoặc thẩm quyền nếu provider không cung cấp.
- Đây là danh sách giới hạn kết quả, không phải phân trang toàn bộ cơ quan; ghi rõ trong contract.
- Danh mục và health giữ công khai như hiện tại. Search mới yêu cầu đăng nhập bằng cơ chế hệ thống đang có.
- Giữ `GET /offices` là contract cũ `planned`; đánh dấu hướng thay thế bằng POST search sau khi thống nhất với người dùng contract. Không tự đổi ý nghĩa của GET cũ.

**Xong khi:** FE thống nhất request/response, auth, lỗi, mô hình địa chỉ, empty/partial result; URL trong OpenAPI khớp gateway.

## L2 — Cấu hình Goong và kiểm tra khả năng tìm thực tế

**Phụ thuộc:** L0; có thể làm thử độc lập khi L1 đang review.

File: `app/core/config.py`, `requirements.txt`, script `scripts/check_goong.py` mới, nhóm biến `.env.example`, block Docker Location.

```dotenv
GOONG_API_KEY=
LOCATION_SEARCH_ENABLED=false
LOCATION_GOONG_TIMEOUT_SECONDS=5
LOCATION_SEARCH_TIMEOUT_SECONDS=20
LOCATION_GOONG_MAX_RPS=4
```

1. Key thật chỉ đặt trong `.env`; Compose truyền key vào Location bằng `${GOONG_API_KEY:-}`.
2. Thêm `httpx` vào dependency Location nếu chọn client này; không nâng dependency toàn bộ backend.
3. Script kiểm tra đọc key từ môi trường, gọi một địa chỉ công cộng và từ khóa cơ quan. Chỉ in trạng thái, số kết quả và tên/địa chỉ công cộng cần đối chiếu; không log URL có key.
4. Thử ít nhất hai khu vực và các từ khóa `UBND`, `Trung tâm phục vụ hành chính công`, `Văn phòng đăng ký đất đai`.
5. Ghi nhận số lượt thực tế, kết quả sai/rỗng và quota tài khoản trước khi quyết định mặc định tìm kiếm. Không hứa từ khóa thủ tục dài sẽ được hiểu như tìm kiếm ngôn ngữ tự nhiên.
6. Thiếu key hoặc search tắt: API search trả 503 có mã lỗi rõ; health và dropdown vẫn dùng được. Không gọi Goong khi import module/startup.

**Xong khi:** xác nhận Goong key dùng được cho ba API cần thiết và có kết quả đủ dùng tại khu demo; biết hạn mức thực tế. Nếu chất lượng kém, chỉnh từ khóa trước khi xây toàn bộ UI.

## L3 — Tách module, giữ tương thích form OCR

**Phụ thuộc:** L1. File trong `services/location-service/app/`.

Cấu trúc gợi ý:

```text
app/
  main.py
  api/administrative.py
  api/offices.py
  core/config.py
  core/errors.py
  core/security.py
  schemas/offices.py
  services/administrative_catalog.py
  services/address_normalizer.py
  services/goong_client.py
  services/office_search.py
  services/distance.py
  services/maps_links.py
```

1. Viết test hành vi 5 API hiện có trước khi chuyển code.
2. `main.py` chỉ khởi tạo app, lifespan/client, đăng ký router và handler.
3. Di chuyển code danh mục sang router/service; giữ URL, method, kiểu `code` số nguyên, `name`, `data.items`, `data.source`.
4. Bổ sung `request_id` và header theo chuẩn bằng thay đổi cộng thêm; error chuẩn hóa phải kiểm tra tương thích FE.
5. Health giữ đúng `{"status":"ok","service":"location-service"}`.

**Xong khi:** form OCR vẫn tải được cả danh mục cũ/mới, chưa cần Goong key.

## L4 — Hoàn thiện danh mục và ghép địa chỉ

**Phụ thuộc:** L3. File: `administrative_catalog.py`, `address_normalizer.py` và tests mới.

1. Tách cache theo `legacy/current` và đường dẫn; thay cache vô thời hạn bằng cache có TTL và giới hạn số phần tử. Mặc định đề xuất 24 giờ; không cache lỗi.
2. Xác minh huyện thuộc tỉnh và xã thuộc huyện/tỉnh đúng phiên bản. Từ chối mã không tồn tại hoặc không khớp bằng 422 trước khi gọi Goong.
3. Lấy tên chuẩn từ danh mục bằng mã; không tin tên tỉnh/xã tùy ý gửi từ FE.
4. Ghép `chi tiết, xã, huyện, tỉnh` cho legacy; `chi tiết, xã, tỉnh` cho current; bỏ phần null, không sinh dấu phẩy thừa.
5. Chuẩn hóa khoảng trắng, dấu phẩy. Chỉ bỏ hậu tố hành chính trùng khi khớp đơn vị đã chọn và có ranh giới rõ; không xóa tên đường/thôn chỉ vì trùng tên phường.
6. Giữ trường `input_address` riêng với `resolved_address`; không ghi địa chỉ Goong nhận diện ngược vào User/OCR.
7. Không tự chuyển mã hành chính cũ sang mới bằng so khớp tên. Nếu thiếu mapping xác minh, giữ phiên bản người dùng chọn.

**Test bắt buộc:** địa chỉ thiếu xã legacy; current không có huyện; mã sai cha; lẫn phiên bản; `16 Hòa Mỹ 3, Hòa Minh` khi phường đã chọn; `Khu Tiên Xá, Thị trấn Vương` giữ khu; tên đường trùng tên phường nhưng không phải hậu tố hành chính.

**Xong khi:** ghép được địa chỉ hợp lệ và bảo toàn tên chi tiết, không tạo lỗi lặp lại như form OCR trước đó.

## L5 — Viết Goong client V2

**Phụ thuộc:** L2, L3. File: `services/goong_client.py`.

Ba hàm của adapter:

```python
geocode(address) -> list[GeocodeCandidate]
search_places(query, latitude, longitude, radius_meters, limit) -> list[PlaceCandidate]
place_detail(place_id) -> PlaceDetail
```

Đối chiếu tài liệu chính thức và response thật đã lưu làm fixture trước khi triển khai:

- [Geocode V2](https://help.goong.io/kb/rest-api-v2/geocode-rest-api-v2/geocode-v2/): `GET https://rsapi.goong.io/v2/geocode`, truyền `address` và key.
- [Autocomplete V2](https://help.goong.io/kb/rest-api-v2/autocomplete-rest-api-v2/autocomplete-v2/): `GET https://rsapi.goong.io/v2/place/autocomplete`; tìm từ khóa, có vị trí ưu tiên/bán kính/limit. Tài liệu ghi bán kính theo km: adapter đổi `radius_meters / 1000`.
- [Place Detail V2](https://help.goong.io/kb/rest-api-v2/place-detail-rest-api-v2/place-detail-place-detail-v2/): `GET https://rsapi.goong.io/v2/place/detail`; lấy chi tiết bằng `place_id`.

Yêu cầu kỹ thuật:

1. Dùng một client có vòng đời quản lý bởi app; không gọi HTTP blocking trong async handler.
2. Host/path cố định, query parameters được encode; kiểm tra HTTP status, status nghiệp vụ, schema, tọa độ hữu hạn và hợp lệ. HTTP 200 chưa đủ để coi là thành công.
3. Timeout mỗi call 5 giây, deadline toàn search 20 giây; response có kích thước giới hạn. Không tăng timeout gateway chung để che lỗi.
4. Retry GET tạm lỗi tối đa 2 lần trong deadline; không retry key sai/billing chưa hoạt động/validation sai. Quota hết thì dừng và thông báo, không vòng lặp thử lại.
5. Toàn bộ call của adapter đi qua rate limiter chung của tiến trình; giới hạn song song không thay thế giới hạn request/giây.
6. Không in key, URL query có key, JWT hoặc địa chỉ cá nhân vào log. Provider error chuyển sang mã lỗi của Location, không trả nguyên payload lỗi upstream.

**Xong khi:** test mock đủ thành công/rỗng/timeout/403/429/5xx/HTTP 200 với status lỗi/JSON sai. Kiểm tra thật dùng script opt-in, CI không cần key.

## L6 — Xây pipeline tìm cơ quan

**Phụ thuộc:** L4, L5. File: `services/office_search.py`, `distance.py`.

Thứ tự xử lý:

1. Validate địa chỉ, tên đơn vị và query; kiểm tra auth/quota trước call tính phí.
2. Geocode địa chỉ đã ghép. Không có tọa độ phù hợp: trả `ADDRESS_NOT_FOUND`, không lấy trung tâm Việt Nam làm fallback.
3. Nếu nhiều ứng viên khác nhau đáng kể hoặc không khớp khu vực người dùng chọn, trả `ADDRESS_AMBIGUOUS` yêu cầu bổ sung địa chỉ. Không mặc định chọn kết quả đầu tiên dù ở tỉnh khác. Không suy ra mismatch chỉ từ đổi tên hành chính nếu chưa có mapping.
4. Gọi search theo query người dùng. Ô query mặc định là `Trung tâm phục vụ hành chính công`; thêm vài gợi ý từ khóa ở FE. Chưa cần mapping pháp lý giữa thủ tục và cơ quan.
5. Khử trùng ứng viên theo `(provider, place_id)` trước khi lấy detail.
6. Lấy detail cho tập ứng viên có giới hạn, tối đa 10 địa điểm/lần tìm trong bản đầu. Thiếu tọa độ hợp lệ thì không tính khoảng cách giả.
7. Tính Haversine, lọc lại bán kính tại backend, sắp tăng dần theo mét và lấy tối đa `limit`.
8. Không đủ 5 nơi thì trả số nơi thực có. Không tự mở rộng bán kính im lặng; FE cho người dùng chọn bán kính lớn hơn rồi tìm lại.

Empty/partial/error:

- Search thành công nhưng không có ứng viên hoặc tất cả nằm ngoài bán kính: 200, `items: []`, `is_partial: false`.
- Một số detail lỗi nhưng còn kết quả hợp lệ: 200, `is_partial: true`, warning có mã và thông báo; không gọi đó là tập đầy đủ.
- Tất cả detail lỗi vì provider/network: trả lỗi phù hợp, không trả danh sách rỗng như thể không có cơ quan.
- Provider từ chối key hoặc hết quota: dừng các call còn lại; trả lỗi cấu hình/quota rõ ràng.

**Xong khi:** không trùng kết quả, khoảng cách đúng thứ tự, không lẫn km/m, không nhận nhầm nơi ngoài khu vực, lỗi không biến thành kết quả giả.

## L7 — Đường dẫn Google Maps

**Phụ thuộc:** L6. File: `services/maps_links.py`.

1. Tạo URL từ tọa độ đích hợp lệ bằng hàm encode query params:

```text
https://www.google.com/maps/dir/?api=1&destination=<latitude>,<longitude>
```

2. Có thể thêm `origin` bằng tọa độ địa chỉ đã geocode, để chỉ đường từ vị trí người dùng đã nhập. Bỏ origin thì Google có thể yêu cầu chọn điểm xuất phát.
3. Không truyền Goong `place_id` vào `destination_place_id`; hai nhà cung cấp có mã khác nhau.
4. Dùng host Google cố định, không chuyển tiếp URL tùy ý từ upstream. FE mở với `noopener,noreferrer`.
5. Hiển thị tên/địa chỉ bằng text, tránh render HTML từ dữ liệu provider.

**Xong khi:** chọn kết quả trên FE mở đúng tọa độ, không cần Google API key. Tham khảo [Google Maps URLs](https://developers.google.com/maps/documentation/urls/get-started).

## L8 — Auth, chi phí và xử lý lỗi

**Phụ thuộc:** L3, L5; hoàn tất trước L9.

- Search dùng `get_current_user` và `add_auth_error_handler` của common_auth; hiện thư viện nhận Bearer hoặc HttpOnly cookie. Không viết cơ chế đăng nhập mới, không sửa User Service.
- Danh mục giữ public để không làm hỏng flow OCR đang gọi không kèm Bearer.
- Giới hạn đề xuất cho demo: 5 lần tìm/phút/user; global provider 4 call/giây, điều chỉnh theo quota tài khoản. Trả 429 khi rate limit, không giữ request chờ vô hạn.
- Có ngân sách ngày do ứng dụng tự đếm và dừng, tính cả retry/detail/fallback. Nếu nhiều worker/replica thì dùng counter chung bền vững thuộc Location; limiter trong RAM chỉ đúng cho một tiến trình và reset khi restart. Không coi quota request/phút là trần tiền tháng.
- Chỉ cache danh mục ở L4. Chưa lưu lâu dài dữ liệu Goong/địa chỉ cá nhân; nếu thêm cache provider, kiểm tra điều kiện sử dụng của Goong và thời hạn lưu trước.
- Log `request_id`, operation, status, duration, số upstream calls; không log địa chỉ đầy đủ hoặc secret.

Mã lỗi đề xuất để chốt ở L1 và bổ sung vào quy ước chung bằng diff nhỏ:

| HTTP | Code | Ý nghĩa |
|---|---|---|
| 422 | VALIDATION_ERROR | Input/mã hành chính/quan hệ cha con sai |
| 422 | ADDRESS_NOT_FOUND | Không định vị được địa chỉ |
| 422 | ADDRESS_AMBIGUOUS | Cần bổ sung địa chỉ để xác định vị trí |
| 401 | UNAUTHORIZED / TOKEN_EXPIRED | Dùng handler auth có sẵn |
| 429 | RATE_LIMITED | Chặn tốc độ theo user/app |
| 503 | LOCATION_SEARCH_DISABLED | Chưa bật tìm kiếm |
| 503 | GOONG_NOT_CONFIGURED | Thiếu key |
| 503 | GOONG_ACCESS_DENIED | Key/hạn chế truy cập/tài khoản Goong bị từ chối |
| 503 | GOONG_QUOTA_EXCEEDED | Hết quota hoặc ngân sách provider |
| 503 | GOONG_UNAVAILABLE | Không kết nối được |
| 504 | GOONG_TIMEOUT | Quá thời gian upstream |
| 502 | GOONG_BAD_RESPONSE | Upstream 5xx hoặc schema không hợp lệ |

**Xong khi:** unauthenticated/invalid request không phát sinh call Goong; thiếu key không làm sập dropdown; lỗi chuẩn có request_id.

## L9 — Ghép endpoint, Docker và contract tests

**Phụ thuộc:** L6, L7, L8.

1. Đăng ký POST search trong router `offices.py`; response theo L1.
2. Thêm biến từ L2 vào đúng block Location; giữ Docker port nội bộ 8000, public key mount và health check hiện tại.
3. Không thêm DB dependency lúc startup vì chức năng này chưa cần DB. Không tạo bảng cơ quan cố định chỉ để hiển thị kết quả tìm kiếm.
4. OpenAPI: 5 API danh mục + health + search; `servers` đúng `/api/locations` qua Gateway và `/api/v1/locations` nội bộ.
5. Chỉ đánh dấu `x-status: implemented` sau khi test đủ hành vi; không gắn nhãn implemented cho GET planned cũ.
6. Đặt test mới ở `backend/tests/test_location_*.py` để CI hiện tại (`pytest tests ...`) chạy mà không phải thay `.gitlab-ci.yml`.
7. Tests dùng package `app` phải cách ly import giữa các service; áp dụng pattern loader hiện có của repo, không sửa global `sys.modules`/sys.path làm hỏng tests service khác.

**Xong khi:** gọi API qua gateway được; container Location thiếu key vẫn healthy; test Location và test tích hợp chung đạt.

## L10 — Bàn giao và tích hợp frontend (repo riêng)

**Phụ thuộc:** L1 để dựng UI mock; L9 để kết nối thật.

Phạm vi FE đề xuất: module mới `public/location-search.js`, UI tìm nơi nộp trong trang đã thống nhất với chủ FE, test riêng. Nếu thêm hàm API vào `auth-api.js`, chỉ thêm wrapper nhỏ và không thay cơ chế request/auth của cả ứng dụng.

1. Tái dùng `fetchAdministrativeUnits(path)`; không gọi OCR để lấy danh mục và không copy danh sách tỉnh vào từng trang.
2. Địa chỉ người dùng hiện tại mặc định dùng `current`; hỗ trợ tùy chọn `legacy` cho người cần nhập theo địa chỉ cũ. Không ép mọi màn mới dùng 3 cấp.
3. Chi tiết nhập text; dropdown cấp dưới reset khi cấp trên đổi. Chặn response cũ đến muộn ghi đè lựa chọn mới (AbortController hoặc sequence ID).
4. Chuyển `select.value` thành số trước khi gửi mã; xã legacy bỏ trống gửi null, không gửi `""` hoặc `0`.
5. Thêm ô từ khóa có giá trị mặc định và gợi ý; `procedure_name` có thể điền sẵn vào query nếu màn đang có dữ liệu đó, không bắt thay contract Procedure.
6. Chỉ gọi search khi bấm Tìm; loading rõ, chống bấm lặp. Không gọi API trả phí mỗi lần gõ địa chỉ.
7. Render tên, địa chỉ, khoảng cách với nhãn `Khoảng cách đường thẳng`; danh sách có bao nhiêu hiển thị bấy nhiêu. Hiện địa chỉ mà hệ thống đã định vị để người dùng kiểm tra.
8. Có trạng thái chưa tìm/loading/rỗng/lỗi/partial. Nếu request cũ về sau request mới, bỏ kết quả cũ. Không để danh sách cũ bị hiểu là kết quả của địa chỉ mới khi request mới lỗi.
9. Nút Chỉ đường dùng `directions_url`. Ghi ngắn “Liên hệ cơ quan để xác nhận nơi tiếp nhận hồ sơ”. Không đưa key/provider payload vào giao diện.
10. Dữ liệu lựa chọn giữ ở màn hiện tại. Nếu cần lưu vào hồ sơ nộp, mở task với service sở hữu hồ sơ; Location không tự ghi User/Procedure/Contract DB.
11. Nếu muốn component địa chỉ dùng chung mọi trang, làm module mới rồi migrate từng trang bằng MR riêng; không refactor toàn bộ trang OCR trong MR tìm cơ quan.

**Xong khi:** người dùng hoàn thành toàn luồng trên web; trang OCR cũ vẫn chạy; FE gọi Gateway với cookie/auth helper hiện có.

## L11 — Kiểm thử, bàn giao và merge

**Phụ thuộc:** L9, L10.

Ma trận tối thiểu:

| Nhóm | Tình huống phải kiểm tra |
|---|---|
| Tương thích | 5 endpoint danh mục, kiểu code/name, health, UI OCR legacy/current |
| Địa chỉ | Thiếu detail, legacy thiếu xã hợp lệ, current có huyện bị từ chối, sai quan hệ cha con, hậu tố trùng, không xóa nhầm tên đường |
| Goong | Key lỗi, timeout, quota, 200 nhưng status lỗi, malformed JSON, tọa độ thiếu/sai, geocode mơ hồ |
| Search | 0/1/n kết quả, trùng place_id, điểm ngoài radius, sắp khoảng cách, detail lỗi một phần/toàn bộ |
| Auth/cost | Cookie và Bearer, hết hạn, chưa đăng nhập, invalid input không gọi provider, limiter toàn request và nhiều user, trần call mỗi search |
| FE | Đổi tỉnh nhanh, request đến sai thứ tự, loading/rỗng/lỗi, query hiển thị an toàn, link chỉ đường đúng |
| Integration | CORS/gateway path, 401/429/503 có request_id, restart Location không làm lỗi service khác |

Các lệnh kiểm tra sau khi đã tạo tests mới (chạy tại repo tương ứng):

```powershell
# backend
python -m pytest tests/test_location_*.py -q
python -m pytest tests/test_api.py tests/test_config.py tests/test_gateway_config.py tests/test_openapi.py -q
docker compose config --quiet
docker compose up -d --build --no-deps location-service

# frontend
node --test tests/detail-ocr-address.test.cjs
node --test tests/location-search.test.cjs
```

Lưu ý: shell/pytest không phải môi trường nào cũng expand wildcard. Nếu `tests/test_location_*.py` không expand trên PowerShell, liệt kê tên file test cụ thể hoặc dùng `python -m pytest tests -k location -q` và kiểm tra số test được chọn. Test FE mới phải được tạo trước; `npm test` hiện tại chỉ kiểm tra cú pháp server, chưa thay thế các test trên.

- Regression chung có lỗi sẵn: ghi rõ baseline và lỗi mới, không sửa service khác chỉ để làm xanh. CI phải xanh trước merge theo quy ước nhóm.
- Chạy live test nhỏ bằng key thật sau mock tests; kết quả live phụ thuộc Goong, không assert cứng luôn có đúng 5 nơi. Báo rõ số request và trạng thái, che key.
- Tài liệu bàn giao có URL FE/nội bộ, request mẫu, response mẫu, auth, code lỗi, giới hạn, env, hướng dẫn chạy/test. Có thể import OpenAPI vào Postman; không commit collection sinh tự động.
- Stage file cụ thể hoặc `git add -p`; xem `git diff --cached --name-only` và `git diff --cached --check`. Không stage `.env` hoặc thay đổi OCR/Contract ngoài phạm vi.
- Chia MR: contract/baseline; Backend Location; Frontend tích hợp. FE merge sau BE hoặc ẩn tính năng bằng cờ cho tới lúc BE deploy.
- Deploy riêng Location, không `docker compose down` toàn hệ thống. Rollback: tắt `LOCATION_SEARCH_ENABLED` hoặc dùng lại image Location trước đó; danh mục vẫn hoạt động.

**Xong khi:** demo thật đi qua Gateway → Location → Goong → danh sách FE → Google Maps; hồi quy OCR đạt; diff không đổi nghiệp vụ service khác.

## Thứ tự triển khai

L0 → L1 → L3 → L4; L2 kiểm tra provider sớm; L5 sau L2/L3; L6 sau L4/L5; L7/L8 hoàn thiện trước L9; L10 UI có thể dựng từ L1 nhưng tích hợp thật sau L9; L11 nghiệm thu.

Ưu tiên một luồng đầy đủ có giới hạn trước: danh mục đang chạy → một query cơ quan → tối đa 5 kết quả → link chỉ đường. Sau đó hoàn thiện toàn bộ lỗi, auth, quota và tests trước khi bàn giao. Không cần thêm hệ thống quản trị cơ quan, xác định thẩm quyền pháp lý, lưu lịch sử, bản đồ nhúng hoặc routing trả phí để coi phạm vi đã yêu cầu là hoàn thành.
