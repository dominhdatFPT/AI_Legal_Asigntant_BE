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

## Năm địa điểm gần nhất

### Danh mục chính thức

Với trung tâm hành chính công Đà Nẵng, tìm trong snapshot địa chỉ cơ quan chính thức
`app/data/danang_public_service_centers.json`, không dựa vào autocomplete để xác định
cơ quan tồn tại. Nguồn là bài công bố trên cổng xã Thăng An thuộc Đà Nẵng ngày 14/7/2025,
đối chiếu ngày 22/10/2026; mỗi kết quả có URL nguồn, ngày công bố và ngày đối chiếu.
Snapshot có 93 trung tâm được nêu rõ (mục Hoàng Sa chỉ nêu trụ sở nên không tự suy ra trung tâm).
20 địa chỉ số nhà/đường được định vị khớp bằng Goong; Hòa Khánh dùng điểm POI của
UBND phường tại đúng địa chỉ 168 Nguyễn Sinh Sắc, đã đối chiếu với Google Maps.
Những mục còn thiếu tọa độ hoặc
chỉ có địa chỉ thôn phải bổ sung pin cơ quan đã kiểm chứng trước khi tham gia xếp hạng.
Không dùng tâm xã hoặc kết quả geocode sai số nhà để thay thế.

`contact_phone`/`contact_label` chỉ lấy từ hồ sơ đã đối chiếu website hiện hành, kèm
`contact_source_url`. Số di động trong danh mục năm 2025 được giữ để truy vết nguồn,
không dùng làm số tiếp nhận hiện tại. Đối chiếu ngày 22/10/2026: Hòa Khánh 02363730807
(đường dây thủ tục hành chính); Liên Chiểu 02363777998 (đường dây nóng UBND phường);
Thanh Khê 02363714922; An Khê 02363683723; Hải Châu 02363827970 (điện thoại UBND).
`app/data/office_profiles.json` bổ sung ảnh đã đối chiếu đúng cơ quan, alt text và URL
bài viết gốc; ảnh được lưu trong frontend tại `/images/offices/`, ghi nguồn ở thẻ.
Nếu chưa có giờ làm việc của đúng cơ quan với nguồn xác minh, `opening_hours` là null;
frontend hiển thị “Chưa có giờ làm việc được xác minh”, không suy đoán một
khung giờ cụ thể. Khi bật tra cứu bổ sung, giờ Google Places chỉ được nhận nếu số nhà
và tên đường trùng chính xác với thẻ; website `.gov.vn` là nguồn dự phòng.
Website Hòa Khánh liên kết cổng hẹn giao dịch theo ngày/quầy (`appointment_url`,
`appointment_source_url`); thẻ mở lịch hiện hành thay vì suy ra giờ làm việc cả tuần
từ các khung hẹn còn trống. Chưa tìm thấy thông báo giờ tiếp nhận cố định trên website
của năm phường đã đối chiếu nên không tự điền khung giờ hành chính chung. Ngày 25/10/2026 đã đọc trực tiếp giờ Google Maps của Hòa Khánh và Thanh Khê, lưu nguồn và ngày đối chiếu trong hồ sơ.
Liên kết chỉ đường dùng tên POI đã đối chiếu vì Google Maps có thể gán tọa độ trần
cho một địa điểm lân cận; đã thử thực tế Google Maps trả đúng UBND phường Hòa Khánh.
Điểm xuất phát gửi địa chỉ đã định vị bằng văn bản để giữ số nhà/tên đường; nếu chỉ
có tọa độ thì giữ 7 chữ số thập phân, không làm tròn longitude xuống 3 chữ số bằng `:g`.
Frontend không hiện số mục danh mục chưa định vị trong luồng kết quả.
Ảnh thiếu hoặc lỗi tải có trạng thái thay thế rõ ràng. Không dùng ảnh trụ sở cũ để lấp chỗ trống.

Backend tính khoảng cách với toàn bộ mục đã định vị phù hợp từ khóa rồi lấy 5 kết quả;
response `coverage` nêu số mục đối chiếu/đã định vị, `is_partial` và cảnh báo nếu còn thiếu.
Snapshot này bao phủ trung tâm hành chính công Đà Nẵng. Danh mục địa chỉ đã xác minh còn có các trụ sở TP.HCM nêu bên dưới. Tỉnh/thành, loại cơ quan khác
vẫn dùng tập ứng viên Goong và giao diện ghi rõ chỉ xếp khoảng cách trong dữ liệu hiện có.

Để cập nhật snapshot có kiểm tra nguồn và geocode opt-in (dùng quota):

```powershell
python services/location-service/scripts/build_official_directory.py --fetch-source
python services/location-service/scripts/build_official_directory.py --geocode
```

Review địa chỉ, `coordinate_status`, `geocoded_address` và tọa độ trước khi build image.
Parser từ chối nhập nếu nguồn không còn đủ thứ tự 94 mục, chọn địa chỉ trung tâm riêng
thay vì địa chỉ UBND khi hai cơ quan khác trụ sở. Snapshot nằm trong image để việc tìm
không phụ thuộc truy cập website nguồn hay geocode lại 93 cơ quan mỗi request.

### Nguồn gợi ý bản đồ cho phạm vi còn lại

Ứng viên phải khớp tỉnh/thành hiện hành qua `compound.province` và phần tỉnh/thành
cuối địa chỉ. Khi thiếu metadata, chỉ chấp nhận phần cuối địa chỉ khớp chính xác;
không tìm chuỗi tỉnh trong tên cơ quan. Loại địa chỉ mâu thuẫn hoặc chưa xác định được
tỉnh, không bù kết quả từ tỉnh khác. `LOCAL_RESULTS_UNAVAILABLE` có nghĩa nguồn chưa
có kết quả phù hợp trong tỉnh đã chọn, không khẳng định địa phương không có cơ quan.

`QUERY_ALIASES` tập trung các tên gọi tương đương, bao gồm Văn phòng/Phòng đăng ký
kinh doanh; giữ hậu tố cụ thể và loại doanh nghiệp tư vấn khỏi nhóm cơ quan đăng ký.
Gộp POI khác ID khi cùng tên chuẩn hóa và tọa độ cách nhau tối đa 25 m; giữ các chi
nhánh khác tên hoặc khác vị trí. FE chỉ dùng địa chỉ `current`; API `legacy` vẫn
được giữ cho những bên gọi cũ.

Request chỉ có `address` và `query`. Backend gộp kết quả từ từ khóa kèm tỉnh/thành,
những tên gọi tương đương của trung tâm hành chính công và từ khóa gốc để hỗ trợ
địa danh cũ. Không tự đổi loại cơ quan thành UBND. Luôn lấy chi tiết, loại trùng
place_id, tính khoảng cách tọa độ thực rồi trả tối đa 5 địa điểm gần nhất.
Loại gợi ý không chứa các từ khóa cơ quan yêu cầu; hỗ trợ không dấu, UBND viết tắt
và hai tên gọi tương đương của trung tâm hành chính công.
Không gửi bán kính tới Goong và không loại ứng viên theo khoảng cách.
Kết quả là 5 nơi gần nhất trong tập ứng viên Goong trả về, không phải danh sách đầy đủ
hay xác nhận thẩm quyền tiếp nhận hồ sơ. Có thể ít hơn 5 nếu nguồn thiếu dữ liệu.

Mỗi tìm kiếm có tối đa 3 lượt autocomplete, 10 ứng viên mỗi lượt, 30 chi tiết khác nhau
và 1 geocode (tối đa 34 thao tác trước retry). Giữ timeout tổng, giới hạn RPS, ngân sách
ngày và giới hạn theo người dùng hiện có. Lỗi quyền/quota dừng ngay; lỗi khác được
báo là kết quả một phần nếu vẫn có dữ liệu sử dụng được.

```powershell
python -m pytest tests/test_location_search.py tests/test_location_goong.py tests/test_location_address.py tests/test_location_config.py tests/test_location_administrative.py tests/test_location_contract.py -q
```

Probe thực tế dùng quota, không in key; chạy trong môi trường đã cấu hình:

```powershell
python services/location-service/scripts/probe_office_search.py --run-probe
```

Bao gồm 9 ca: Đà Nẵng với từ khóa chung/cụ thể và không dấu, đăng ký kinh doanh,
đăng ký đất đai, công chứng, trung tâm hành chính Hà Nội, công chứng TP.HCM và từ
khóa không tồn tại. Kiểm tra tỉnh/thành, thứ tự khoảng cách, tối đa 5 kết quả và
loại trùng; đăng ký kinh doanh Đà Nẵng phải có kết quả trong 20 km.
# Google Places và Google Search: giờ làm việc, hotline

Địa chỉ/đích bản đồ: `/offices/search` đối chiếu số nhà (kể cả hậu tố/hẻm), tên
đường và tỉnh. Không đổi sang nhà bên cạnh khi geocoder không tìm đúng số nhà.
POI Goong phải có địa chỉ đường rõ ràng và cách các kết quả geocode đúng địa chỉ
không quá 150m. Gợi ý thiếu địa chỉ, centroid phường hoặc pin mâu thuẫn được bỏ.
Snapshot chính thức cũng được kiểm tra lại chữ số/đường trước khi xếp hạng.

`verified_office_locations.json` chứa ngoại lệ địa chỉ công bố chính thức đã
đối chiếu cùng Google Place ID (tách biệt hoàn toàn với Goong ID). Phòng Doanh
nghiệp và Đăng ký kinh doanh Đà Nẵng ở tầng 6, 24 Trần Phú; pin là tòa nhà và
ghi rõ điều đó. Nguồn thành phố nêu hồ sơ doanh nghiệp nộp trực tuyến, nên thẻ
có link nộp trực tuyến và ghi chú địa chỉ hỗ trợ, không coi đó là quầy nhận hồ sơ.

Chỉ đường/search/preview được sinh từ cùng `map_destination_name` + `address`.
Google Place ID chỉ có khi đối chiếu được. FE tự xây lại đích theo thẻ để không
mở URL tọa độ cũ từ cache. Dữ liệu các nhà cung cấp vẫn có thể cũ; đối chiếu
geocode là kiểm tra nhất quán, không chứng nhận một cơ quan vẫn đang hoạt động
tại đó. Không tuyên bố bao phủ đầy đủ mọi cơ quan ở mọi tỉnh.

`office_location_conflicts.json` lưu các mâu thuẫn đã phát hiện giữa nguồn bản
đồ để không tiếp tục trả kết quả đó khi chưa xác minh lại (hiện có văn phòng
công chứng Nguyễn Thị Hạnh: Goong 285 / Google 283 Nguyễn Sinh Sắc).

Luồng tìm 5 cơ quan không chờ Google. Khi bật `LOCATION_WEB_LOOKUP_ENABLED=true`
và có `GOOGLE_MAPS_API_KEY` hoặc `GEMINI_API_KEY`, `/offices/search` cấp `web_lookup_id` thuộc người dùng,
hết hạn sau 5 phút. FE gọi riêng `POST /offices/enrich` với `{ "lookup_id": "..." }`.
Không gửi địa chỉ công dân/tọa độ gốc lên Gemini; chỉ gửi tên, địa chỉ công khai
của tối đa 5 cơ quan. Không cho client chỉ định URL để backend tải tùy ý.

`GOOGLE_MAPS_API_KEY` được dùng để hỏi Google Places về giờ làm việc. Kết quả chỉ
được chấp nhận khi khớp tên cơ quan, số nhà, tên đường và tỉnh/thành. Place ID được trả riêng cả khi cơ quan chưa công bố giờ làm việc, giúp frontend gán đúng tên cho liên kết Maps. Một lượt tra cứu lỗi không làm mất kết quả các cơ quan khác. Nếu không có Place khớp, frontend dẫn Maps bằng địa chỉ và có nút tìm tên cơ quan kèm địa chỉ trên web.

Google Search grounding tìm nguồn; Gemini đề xuất trích dẫn. Backend yêu cầu
metadata chứng minh đã tìm kiếm, tải lại trang được dẫn trên `.gov.vn`, đối chiếu
tên/phường, đường/số nhà và tỉnh thành gần trích dẫn. Số điện thoại/giờ phải xuất
hiện nguyên văn; không lấy hạn nộp hồ sơ hoặc lịch tiếp
công dân làm giờ làm việc. Đây là bộ lọc thận trọng: trang PDF/JS-only hoặc cách
ghi địa chỉ khác có thể chưa xác minh được. Không cam kết mọi cơ quan đều có dữ liệu.

FE cập nhật thông tin trong hàng dữ kiện và hàng nút thao tác, giữ thứ tự và khoảng cách. Thẻ kết thúc ngay sau hàng nút, không thêm nguồn, ghi chú, thông báo tra cứu hay bản đồ nhúng bên dưới. Metadata tìm kiếm và xuất xứ giữ nội bộ trong API; giao diện chỉ hiện giờ theo từng dòng. Ưu tiên Maps, nguồn chính thức, nguồn đã duyệt, sau cùng mới dùng giờ AI tổng hợp từ kết quả tìm kiếm cho đúng tên và địa chỉ cơ quan. Không tự gán giờ hành chính chung.
Fact xác minh cache RAM 6 giờ, giới hạn 512 cơ quan; handle tối đa 256. Restart
hoặc nhiều worker không chia sẻ cache/hạn mức; hiện triển khai một worker.

`LOCATION_WEB_MODEL` chọn model hỗ trợ `google_search` (mặc định
`gemini-2.5-flash`; compose có thể kế thừa `GEMINI_MODEL`). Cần quota/billing
phù hợp của Google. `LOCATION_WEB_DAILY_BUDGET=20` giới hạn số lượt gọi model
mỗi ngày UTC mỗi process, không phải số truy vấn Google nội bộ hoặc trần chi phí.
429/401/403 ngắt gọi 15 phút. Lookup có timeout 42 giây, client 45 giây; lỗi trả
`web_lookup_status=unavailable` và giữ nguyên kết quả tìm cơ quan. Không ghi key
hoặc nội dung lỗi upstream ra API/log. Không tự động retry Google.

Tài liệu nhà cung cấp: https://ai.google.dev/gemini-api/docs/generate-content/google-search


### Rà soát location ngày 25/10/2026

- Bổ sung bốn trung tâm ở khu Quận 1 cũ: Tân Định (58B Nguyễn Đình Chiểu), Sài Gòn (45–47 Lê Duẩn), Bến Thành (92 Nguyễn Trãi), Cầu Ông Lãnh (105 Trần Đình Xu). Cả bốn có địa chỉ chính thức, tọa độ, Google Place ID và giờ làm việc được đọc trực tiếp trên Maps; được xếp khoảng cách cùng trụ sở cấp thành phố tại Thủ Đức. Hỗ trợ từ khóa có hậu tố Quận 1.
- Trụ sở 43 Nguyễn Văn Bá cũng đã có Google Place ID và lịch làm việc được đối chiếu trực tiếp. `opening_hours.checked_at` ghi ngày đối chiếu, không phải dữ liệu trạng thái mở cửa thời gian thực.
- Liên Chiểu được bổ sung Google Place ID và giờ sau khi đối chiếu thủ công tên và ảnh biển trung tâm trên Maps; Maps hiện địa chỉ Plus Code, còn thẻ giữ số 68 Lạc Long Quân theo nguồn chính thức. Hải Châu và An Khê tiếp tục dùng địa chỉ khi chưa xác minh được địa điểm khớp.
- Hai ô chọn tỉnh/thành và xã/phường giữ bố cục cũ; mở danh sách mới hiện ô gõ lọc tiếng Việt có hoặc không dấu. Tìm kiếm chỉ lọc các mục trong danh sách, giữ lựa chọn hiện tại cho đến khi chọn mục khác. Đổi tỉnh xóa phường và bỏ qua dữ liệu tải về từ lượt chọn trước. Giờ làm việc hiển thị dọc, mỗi ngày hoặc nhóm ngày một dòng.
- Kiểm tra thực tế Google Places với cấu hình hiện tại trả HTTP 403 `PERMISSION_DENIED`. Dữ liệu đã đối chiếu được hiển thị từ snapshot; cần quyền Google Places hợp lệ để tự bổ sung các địa điểm mới.


### Sửa luồng UBND và đồng bộ giao diện

- Kết quả Goong có cùng tên UBND, số nhà/đường và tỉnh/thành được nối lại hồ sơ đã xác minh; Thanh Khê và Liên Chiểu nhận đúng Google Place ID, số liên hệ và giờ làm việc như luồng trung tâm hành chính công. Không nối sang cơ quan khác ở cùng địa chỉ.
- Handle tra cứu bổ sung giữ Google Place ID và tên đích đã xác minh. Google Places gọi Place Details theo ID khi có, ưu tiên `currentOpeningHours`, dự phòng `regularOpeningHours`. Không nhận ID khác hoặc địa chỉ đường mới; địa chỉ Plus Code được chấp nhận khi ID đã được đối chiếu trước. Provider website có hạn chờ riêng để không làm mất kết quả Maps khi chờ quá lâu.
- Nút tìm web gửi cả tên cơ quan và địa chỉ. Nơi chưa tải được giờ hiển thị trạng thái chưa xác minh; không thêm liên kết xem giờ Maps trong phần giờ.
- Trang dùng `partials/user-header.html` và `user-header.css/js` qua `sendUserPage`, cùng menu, tài khoản, đăng xuất và đường dẫn với các trang khác. Font, màu, khoảng cách và bo góc lấy theo bộ giao diện người dùng đang có.


### Giờ tham khảo từ kết quả tìm web

Đã bỏ dòng “Xem giờ trên Google Maps” trong phần giờ chưa xác minh. Dữ liệu có thể bổ sung qua `opening_hours` trong hồ sơ cơ quan khi đã mở và đối chiếu trang nguồn; AI tổng hợp được chấp nhận làm dữ liệu dự phòng, không nâng thành dữ liệu đã xác minh. Với nguồn chính thức hoặc Maps đã khớp cơ quan, giữ cách xác minh hiện hành.

Hải Châu: đã mở trang https://tinhthanhvn.com/ubnd/phuong-hai-chau-thanh-pho-da-nang ngày 25/10/2026, khớp tên phường và địa chỉ số 15 Lê Hồng Phong. Trang ghi thứ Hai đến thứ Sáu, sáng 07:30–11:30, chiều 13:30–17:00. Đây là nguồn tra cứu độc lập nên hồ sơ mang `source_type=reviewed_website`, `verification_status=reference`, URL, tên nguồn, ngày đối chiếu và bằng chứng; frontend hiện nhãn “Giờ làm việc”, không hiện nguồn hay chữ “tham khảo”. Không suy ra giờ thứ Bảy/Chủ Nhật hoặc đổi số điện thoại từ phần AI tổng hợp. Khi có giờ chính thức/Google Places đã xác minh, luồng bổ sung sẽ ưu tiên dữ liệu đó.


### Giờ AI dự phòng (25/10/2026)
Theo yêu cầu hiển thị, Gemini kết hợp Google Search có thể trả `ai_summary_hours` khi không tìm được giờ từ trang cơ quan. Đây là phần tổng hợp từ kết quả tìm kiếm, không phải API lấy nguyên văn Google AI Overview. Hệ thống kiểm tra tên, địa chỉ khớp đầu vào, giới hạn 500 ký tự và phải có giờ cụ thể. Dữ liệu giữ `source_type=ai_summary`, `verification_status=unverified` trong API, cache tối đa một giờ; không tạo lịch mặc định nếu không có thông tin. Giờ này chỉ bổ sung chỗ trống, không thay giờ Maps, nguồn chính thức hoặc nguồn đã duyệt. Giao diện không hiển thị nguồn, tooltip nguồn hoặc nhãn tham khảo.
