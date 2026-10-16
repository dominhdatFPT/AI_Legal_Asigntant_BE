# Nối FE với dữ liệu thật từ database — Kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ba trang FE còn dùng dữ liệu tĩnh (`user-dashboard.html`, `dashboard.html` cho admin, `applicationSubmission.html`) sẽ đọc và ghi dữ liệu thật trong Supabase thông qua procedure-service, user-service và document-service.

**Architecture:** Dùng nhánh `origin/service/procedure` của Duc Trong (đẩy lên 12/10) làm nền. Nhánh này đã có bảng `procedures`, bảng hồ sơ `land_procedure_records`/`business_procedure_records` và API danh mục. Mình sẽ bỏ dữ liệu mock dự phòng trong nhánh, gắn JWT cho API hồ sơ, rồi thêm API danh sách hồ sơ của tôi, danh sách cho admin và số đếm. Sau đó FE thay các mảng/HTML cứng bằng `fetch` qua `auth-api.js`. Những mục BE chưa có sẽ hiển thị nhãn "Sắp ra mắt".

**Tech Stack:** FastAPI + SQLAlchemy `text()` + Supabase Postgres (procedure DB), `libs/common_auth`, FE Express + HTML/JS thuần (ES module `auth-api.js`).

**Spec:** các câu trả lời của bạn trong phiên ngày 13/10/2026:
1. Thêm dữ liệu ảo vào DB cho một tài khoản bằng query SQL.
2. Danh mục thủ tục lấy từ code procedure-service của bạn mình.
3. Phần nào BE chưa có thì giữ trên giao diện và ghi "Sắp ra mắt".

## Global Constraints

- Không dùng dữ liệu giả ở runtime. DB lỗi thì trả `503 PROCEDURE_DB_UNAVAILABLE`, tuyệt đối không fallback sang mock.
- **User chỉ được đọc và ghi dữ liệu của chính mình, xác định qua JWT.** Chủ hồ sơ luôn là `CurrentUser.id` (claim `sub`). Không nhận `user_id` từ body, query hay header (`X-User-Id`), và không có route nào nhận `user_id` của người khác từ phía citizen/lawyer. Mọi truy vấn hồ sơ của user đều có `WHERE user_id = :current_user_id` ngay trong SQL, không lọc lại ở Python. Truy cập hồ sơ của người khác trả **404** (không trả 403, để không lộ việc hồ sơ đó tồn tại). Chỉ role `admin` (`require_role("admin")`) mới xem được hồ sơ của mọi người. Danh mục thủ tục là dữ liệu công khai, không cần token.
- FE không gửi `user_id` trong bất kỳ request nào. Danh tính chỉ đi qua cookie hoặc Bearer token.
- Response theo envelope `{data, error, request_id}`. Danh sách trả `data.items` + `data.pagination {page, page_size, total, total_pages}`. Health giữ đúng `{"status":"ok","service":"procedure-service"}`.
- Lỗi validation trả `422 VALIDATION_ERROR`. Không có token trả 401. Không phải admin trả 403. Hồ sơ không phải của mình trả 404.
- Câu `message` lỗi viết bằng tiếng Việt. Commit theo dạng `feat(procedure): ...` / `fix(procedure): ...`.
- Không commit secret. Không log tên, CCCD hay số điện thoại của người nộp.
- Sửa code thì cập nhật `services/procedure-service/openapi.yaml` trong cùng commit (`x-status` + example).
- Tiền lưu dạng số nguyên đồng. Thời gian là ISO-8601 UTC. Giá trị trống trả `null`.

## Review Focus

1. **Nhánh của bạn mình có mật khẩu DB viết thẳng** trong `docker-compose.yml` (`PROCEDURE_DATABASE_URL: ...hiengia123!@...`). Khi merge phải bỏ dòng này và nhắc bạn ấy đổi mật khẩu Supabase. Task 0 có bước kiểm tra.
2. **User chưa có hồ sơ nào:** bảng phải hiện "Bạn chưa có hồ sơ nào", không được trống trơn hay báo lỗi. Test `test_list_my_applications_empty` nằm ở Task 3. FE xử lý ở Task 5 và 6.
3. **Thứ tự route:** `/applications/me` và `/applications/summary` phải khai báo **trước** `/{procedure_id}` và `/applications/{domain}/{record_id}`. Nếu không, chữ `me` sẽ bị hiểu là id. Test `test_me_route_not_shadowed` nằm ở Task 3.
4. **Diện tích người dùng nhập dạng "85.5 m²"** hoặc dùng dấu phẩy "85,5". FE phải chuẩn hoá về số trước khi gửi, nếu không cột `land_area NUMERIC` sẽ bị null. Hàm `parseArea` nằm ở Task 7.
5. **Token hết hạn giữa chừng khi nộp hồ sơ:** `authRequest` đã tự refresh token. Nút "Tạo hồ sơ" phải tắt trong lúc gửi để không tạo trùng hồ sơ. Xử lý ở Task 7.

---

### Task 0: Merge nhánh procedure và làm sạch

**Files:**
- Merge: `origin/service/procedure` vào `features/Register_MinhDat`
- Modify: `docker-compose.yml` (khối `procedure-service`)
- Modify: `services/procedure-service/app/api/procedures.py` (route `/health`)

- [ ] **Step 1:** `git merge --no-ff origin/service/procedure`. Nếu xung đột thì giữ code procedure-service của nhánh mới.
- [ ] **Step 2:** Trong `docker-compose.yml`, đưa về `PROCEDURE_DATABASE_URL: ${PROCEDURE_DATABASE_URL:-}` và xoá volume `./services/procedure-service/app:/app/app:ro`. Thêm `PROCEDURE_DATABASE_URL=` vào `.env.example` nếu chưa có.
- [ ] **Step 3:** Sửa `/health` để trả đúng `{"status":"ok","service":"procedure-service"}`. Phần kiểm tra DB chuyển sang `GET /procedures/ready`, chỉ thêm route này nếu cần.
- [ ] **Step 4:** Chạy `pytest tests libs/common_auth services/user-service/tests -q` và `pytest services/procedure-service/tests -q`. Cả hai phải PASS.
- [ ] **Step 5:** `git grep -n "hiengia123" -- . ':!*.md'` không được trả về kết quả nào. Sau đó commit: `chore(procedure): merge service/procedure, bỏ secret khỏi compose`.

### Task 1: Bỏ dữ liệu mock dự phòng

**Files:**
- Modify: `services/procedure-service/app/repositories/procedure_repository.py` (xoá `MOCK_PROCEDURES` cùng mọi nhánh "Fallback dữ liệu mẫu")
- Modify: `services/procedure-service/app/main.py` (handler cho `ProcedureDbUnavailable`)
- Test: `services/procedure-service/tests/test_procedure_api.py`

**Interfaces:**
- Produces: `class ProcedureDbUnavailable(Exception)` trong `app/repositories/procedure_repository.py`. Repository raise lỗi này khi chưa cấu hình DB hoặc truy vấn lỗi. `main.py` chuyển nó thành `503 {"code":"PROCEDURE_DB_UNAVAILABLE","message":"Không kết nối được cơ sở dữ liệu thủ tục."}`.

- [ ] **Step 1:** Viết test `test_list_procedures_db_down_returns_503`: monkeypatch engine để raise `OperationalError`. Assert `GET /api/v1/procedures` trả 503, `error.code == "PROCEDURE_DB_UNAVAILABLE"` và `data is None`.
- [ ] **Step 2:** Chạy test, phải FAIL vì hiện code trả 200 kèm mock.
- [ ] **Step 3:** Xoá mock và circuit-breaker fallback, raise `ProcedureDbUnavailable`. Các test cũ đang dựa vào mock thì chuyển sang SQLite in-memory seed sẵn qua fixture trong `tests/conftest.py`, giống conftest của user-service.
- [ ] **Step 4:** `pytest services/procedure-service/tests -q` phải PASS.
- [ ] **Step 5:** Commit `fix(procedure): bỏ mock fallback, DB lỗi trả 503`.

### Task 2: Gắn JWT cho API nộp/xem hồ sơ

**Files:**
- Modify: `services/procedure-service/app/schemas/form_schema.py` (xoá `user_id` khỏi `ApplicationSubmitRequest`, giới hạn `status` trong `Literal["draft","submitted"]`)
- Modify: `services/procedure-service/app/api/procedures.py` (`submit_application`, `get_application_record`)
- Modify: `services/procedure-service/app/repositories/procedure_record_repository.py` (`get_procedure_record_by_id` nhận thêm `owner_id: str | None`)
- Modify: `services/procedure-service/Dockerfile`, `docker-compose.yml` (`additional_contexts: libs: ./libs` nếu chưa có)
- Test: `services/procedure-service/tests/test_applications_auth.py`

**Interfaces:**
- Consumes: `common_auth.fastapi_dep.get_current_user -> CurrentUser(id, role)`, dùng fixture `service_auth` trong `tests/conftest.py` để ký token.
- Produces: `POST /api/v1/procedures/applications` lưu `user_id = current_user.id`. `GET /applications/{domain}/{record_id}` trả 404 khi hồ sơ không thuộc user, trừ khi role là `admin`.

- [ ] **Step 1:** Viết các test:
  - `test_submit_requires_token`: gọi không có token, trả 401.
  - `test_submit_uses_token_owner`: body có thêm `"user_id": "<uuid khác>"`, hồ sơ lưu ra phải có `user_id == token sub`. Nếu dùng `extra="forbid"` thì kỳ vọng 422.
  - `test_get_other_users_record_404`
  - `test_admin_can_get_any_record`
  - `test_submit_invalid_status_422`
- [ ] **Step 2:** Chạy test, phải FAIL.
- [ ] **Step 3:** Thêm `current_user: CurrentUser = Depends(get_current_user)` vào hai route. Bỏ `except Exception` đang trả 500 kèm `str(e)` (lộ thông tin lỗi), để `ProcedureDbUnavailable` đi qua handler của Task 1.
- [ ] **Step 4:** Chạy test, phải PASS.
- [ ] **Step 5:** Cập nhật `openapi.yaml`: thêm `security` và các mã 401/404/422/503 cho hai route. Commit `feat(procedure): hồ sơ lấy chủ từ JWT`.

### Task 3: API danh sách hồ sơ (của tôi, admin) và số đếm

**Files:**
- Modify: `services/procedure-service/app/repositories/procedure_record_repository.py`
- Modify: `services/procedure-service/app/api/procedures.py` (khai báo trước `@router.get("/{procedure_id}")` và trước `/applications/{domain}/{record_id}`)
- Modify: `services/procedure-service/openapi.yaml`
- Test: `services/procedure-service/tests/test_application_lists.py`

**Interfaces:**
- Produces:
  - `list_records(owner_id: str | None, domain: str | None, status: str | None, q: str | None, page: int, page_size: int) -> tuple[list[dict], int]`. Dùng `UNION ALL` hai bảng, `JOIN procedures` theo `procedure_id`, sắp `created_at DESC`. Truyền `owner_id=None` nghĩa là lấy tất cả (dành cho admin). `q` tìm `ILIKE` trên `applicant_name`, `applicant_id_number`, `procedure_code`.
  - `count_records() -> dict` trả `{"total": int, "by_domain": {"land": int, "business": int}, "by_status": {"draft","submitted","processing","completed","cancelled": int}}`.
  - Mỗi item có đủ khoá sau, khoá nào thiếu thì để `null`:
    `id, domain, procedure_code, procedure_name, status, applicant_name, applicant_id_number, address, fee, receiving_agency_level, created_at, due_date`
    (`address` = `land_address` hoặc `headquarters_address`; `due_date` = `created_at + processing_days` dạng ISO date, null nếu thủ tục không có `processing_days`; item cho admin có thêm `user_id`).
  - Routes:
    - `GET /api/v1/procedures/applications/me?domain=&status=&page=1&page_size=10` (cần đăng nhập)
    - `GET /api/v1/procedures/applications?domain=&status=&q=&page=1&page_size=10` (`require_role("admin")`)
    - `GET /api/v1/procedures/applications/summary` (`require_role("admin")`)
  - `page_size` nằm trong 1–50. `domain` thuộc `land|business`, `status` thuộc 5 giá trị của CHECK trong bảng. Sai thì trả 422.

- [ ] **Step 1:** Viết các test, có seed 2 hồ sơ cho user A, 1 cho user B:
  - `test_list_my_applications_only_mine`: A chỉ thấy 2, `pagination.total == 2`.
  - `test_list_my_applications_empty`: user C nhận `items == []`, `total == 0`, `total_pages == 0`.
  - `test_me_route_not_shadowed`: `/applications/me` trả 200, không trả 404 kiểu "không tìm thấy thủ tục".
  - `test_admin_list_all_and_filter_q`: admin thấy 3; `q=<CCCD của B>` chỉ còn 1.
  - `test_admin_list_forbidden_for_citizen`: trả 403.
  - `test_summary_counts`: `total == 3`, `by_domain["land"]` khớp với seed.
  - `test_list_invalid_page_size_422`
  - `test_me_ignores_user_id_query`: A gọi `/applications/me?user_id=<id của B>` vẫn chỉ thấy hồ sơ của A.
  - `test_all_application_routes_require_token` (parametrize mọi route `/applications*`): không có token thì trả 401; token sai chữ ký hoặc hết hạn cũng trả 401.
- [ ] **Step 2:** Chạy test, phải FAIL.
- [ ] **Step 3:** Viết repository và route theo Interfaces.
- [ ] **Step 4:** `pytest services/procedure-service/tests -q` và `pytest tests/test_openapi.py -q` phải PASS.
- [ ] **Step 5:** Commit `feat(procedure): danh sách hồ sơ của tôi, admin và số đếm`.

### Task 4: Query SQL thêm dữ liệu ảo cho một tài khoản

**Files:**
- Create: `services/procedure-service/database/004_seed_demo_applications.sql`
- Create: `services/procedure-service/database/check_demo_applications.sql`
- Modify: `services/procedure-service/database/README.md` (hướng dẫn 3 bước)

**Interfaces:**
- Đầu vào: biến `v_user_id uuid` ở đầu khối `DO $$ ... $$`. Lấy giá trị này bằng cách chạy query sau trên **DB của user-service**: `select id, email, role from users where email = '<email>';`. Hai DB tách riêng nên không join chéo được.
- Hồ sơ ảo được đánh dấu `extra_data = '{"seed":"demo"}'`.

- [ ] **Step 1:** Viết `004_...sql`. Đầu tiên `DELETE` các hồ sơ của `v_user_id` có `extra_data->>'seed' = 'demo'` để chạy lại nhiều lần không bị nhân đôi. Sau đó insert 5 hồ sơ đất đai (`DD-001`, `DD-002`, `DD-003`, `DD-004`, `DD-010`) và 3 hồ sơ hộ kinh doanh (`HKD-001`, `HKD-002`, `HKD-001`), lấy `procedure_id` theo `code`. Trạng thái trải đều 5 giá trị, `created_at` lùi dần từ `now()` trong khoảng 1–30 ngày. Có thể chạy cho nhiều tài khoản bằng cách đổi `v_user_id`.
- [ ] **Step 2:** Viết `check_demo_applications.sql`: đếm số hồ sơ demo theo `user_id` và `status`. Thêm một dòng dọn dẹp, để comment sẵn: `-- delete ... where extra_data->>'seed'='demo';`.
- [ ] **Step 3:** Chạy thử trên DB procedure (Supabase SQL editor). Check script phải ra 8 dòng cho tài khoản đã chọn. Gọi `GET /api/procedures/applications/me` với token của tài khoản đó phải ra `total == 8`.
- [ ] **Step 4:** Commit `chore(procedure): query seed hồ sơ demo theo tài khoản`.

### Task 5: FE — các hàm gọi API trong `auth-api.js`

Repo FE: `D:\OJT_FPT\FE\frontend-project (2)\frontend-project`, tạo nhánh `features/real-data_MinhDat` từ `develop`.

**Files:**
- Modify: `AI_Legal/public/auth-api.js`

**Interfaces (export, đều đi qua `authRequest` + `readData`):**
- `fetchMyApplications({ domain, status, page = 1, pageSize = 10 } = {}) -> {items, pagination}`
- `fetchAllApplications({ domain, status, q, page = 1, pageSize = 10 } = {}) -> {items, pagination}` (admin)
- `fetchApplicationSummary() -> {total, by_domain, by_status}`
- `fetchUserSummary() -> data` của `GET /api/users/summary`
- `fetchProcedures(domain) -> items` từ `GET /api/procedures/domains/${domain}/procedures?include_details=false`
- `submitApplication({ procedureCode, status, formData }) -> record` gửi `POST /api/procedures/applications`
- `fetchMyDocuments() -> {items, pagination}` từ `GET /api/documents/`
- `STATUS_LABELS = { draft:'Bản nháp', submitted:'Đã nộp', processing:'Đang xử lý', completed:'Đã hoàn tất', cancelled:'Đã hủy' }`
- `COMING_SOON_HTML`: một chuỗi badge "Sắp ra mắt" dùng chung cho Task 6 và Task 7.

- [ ] **Step 1:** Thêm các hàm. Query string chỉ chứa tham số có giá trị, dùng `URLSearchParams`.
- [ ] **Step 2:** `npm test` (node --check) phải PASS. Mở DevTools console trên `/user-dashboard`, chạy `(await import('/auth-api.js')).fetchMyApplications()`, phải ra `{items, pagination}`.
- [ ] **Step 3:** Commit `feat: thêm hàm gọi API hồ sơ, thủ tục, tài liệu`.

### Task 6: FE — `user-dashboard.html` và `dashboard.html` (admin) dùng dữ liệu thật

**Files:**
- Modify: `AI_Legal/src/pages/user-dashboard.html` (bảng hồ sơ ~dòng 348–520, "Kho Tài Liệu Số" ~569, "Dịch Vụ Công Nổi Bật" ~638, "Lịch hẹn Tiếp dân" ~538)
- Modify: `AI_Legal/src/pages/dashboard.html` (4 thẻ thống kê ~370–470, "Kỳ giao dịch 2024-Q4" ~318, thao tác nhanh ~480–560, bảng "Hồ Sơ Cập Nhật Gần Đây" ~563–900)

**Interfaces:**
- Consumes: các hàm của Task 5.

- [ ] **Step 1 (user):**
  - Xoá 5 dòng `<tr>` cứng, chỉ để lại `<tbody id="my-applications-body">`. Render các cột: mã hồ sơ (`procedure_code` + 8 ký tự đầu của `id`), tên thủ tục, cơ quan (`receiving_agency_level`: commune → UBND cấp xã, district → cấp huyện, province → cấp tỉnh), ngày nộp, hạn trả (`due_date` hoặc "—"), trạng thái theo `STATUS_LABELS`.
  - Phân trang dùng `pagination`, không có hồ sơ thì hiện "Bạn chưa có hồ sơ nào".
  - API lỗi thì hiện "Không tải được hồ sơ, thử lại sau" kèm nút "Thử lại".
- [ ] **Step 2 (user):**
  - "Kho Tài Liệu Số": hiện CCCD đã lưu (`fetchMyCitizenProfile`, che số còn 6 ký tự đầu + `****`) và danh sách file từ `fetchMyDocuments`.
  - "Dịch Vụ Công Nổi Bật": hiện 2 thủ tục đầu của `land` và 2 thủ tục đầu của `business`, mỗi mục link tới `/applicationSubmission?procedure=<code>`.
  - "Lịch hẹn Tiếp dân": giữ khung, thay nội dung bằng `COMING_SOON_HTML`.
- [ ] **Step 3 (admin):**
  - 4 thẻ thống kê lấy từ `fetchApplicationSummary`: tổng hồ sơ, hồ sơ đất đai, hồ sơ hộ kinh doanh, số đang xử lý (`submitted + processing`).
  - Bỏ các số "+12.4%", "52 cần thẩm định gấp", thay bằng "Sắp ra mắt".
  - "Kỳ giao dịch 2024-Q4" đổi thành "Sắp ra mắt".
  - Các nút Tra cứu quy hoạch / In biên nhận / Quét OCR chuyển sang trạng thái disabled kèm badge "Sắp ra mắt". Hai nút "+ Hồ sơ" giữ nguyên link.
- [ ] **Step 4 (admin):**
  - Bảng "Hồ Sơ Cập Nhật Gần Đây" lấy từ `fetchAllApplications`. Tab Tất cả/Đất đai/Hộ kinh doanh gọi lại API với `domain`, số trên tab lấy từ summary.
  - Ô tìm kiếm lớn gửi `q` (debounce 300ms). Bộ lọc `owner` cũng dùng `q`.
  - Bỏ cột "Đánh giá sơ bộ AI", thay bằng "Sắp ra mắt". Nút thao tác chỉ giữ "Xem chi tiết".
- [ ] **Step 5:** Kiểm tra thủ công. Chạy BE bằng docker cùng gateway, FE bằng `npm run dev`. Đăng nhập tài khoản đã seed ở Task 4:
  - user-dashboard phải thấy 8 hồ sơ.
  - Tài khoản mới phải thấy "Bạn chưa có hồ sơ nào".
  - Admin phải thấy tổng khớp với `check_demo_applications.sql`.
  - Tắt procedure-service thì trang hiện thông báo lỗi, không vỡ layout.
  - `grep -n "GPLX-2024\|HS-DD-2024\|1,284" AI_Legal/src/pages/*.html` không còn kết quả nào.
- [ ] **Step 6:** Commit `feat: dashboard user và admin lấy hồ sơ thật`.

### Task 7: FE — `applicationSubmission.html` dùng danh mục thật và lưu hồ sơ

**Files:**
- Modify: `AI_Legal/src/pages/applicationSubmission.html` (script ~616–1200, form đất ~463, form HKD dựng ở ~926)

**Interfaces:**
- Consumes: `fetchProcedures`, `fetchMyCitizenProfile`, `submitApplication` (Task 5).
- Đổi category nội bộ `'dat-dai'` → `'land'` và `'ho-kinh-doanh'` → `'business'` cho trùng `domain` của BE. Định danh thủ tục giờ là `code` (`DD-001`, `HKD-001`), không còn id kiểu `'land-first-registration'` hay `'hkd-1'`.

- [ ] **Step 1:**
  - Xoá `citizenDatabase`, `currentCitizenIndex`, mảng `procedureCatalog` và `procedureTemplateDescriptions`.
  - Khi trang load, gọi `fetchProcedures('land')` và `fetchProcedures('business')` để dựng `procedureCatalog = [{code, domain, name, description}]`. Từ khoá tìm kiếm lấy từ `name + description`.
  - Đọc `?procedure=<code>` trên URL để chọn sẵn thủ tục.
- [ ] **Step 2:**
  - Xoá các `value="..."` cứng trong form đất (128, 14, 85.5 m², địa chỉ Kim Mã, GPXD 118...) cùng `checked`/`selected` mẫu. Giữ `placeholder`.
  - Phần "Thông tin người làm hồ sơ" điền từ `fetchMyCitizenProfile()`.
- [ ] **Step 3:** Thêm hàm `parseArea(text) -> number | null`. Hàm bỏ `m²` và khoảng trắng, đổi `,` thành `.`, chuỗi không phải số thì trả `null`. Có self-check: `console.assert(parseArea('85,5 m²') === 85.5 && parseArea('abc') === null)`.
- [ ] **Step 4:** Nút tạo hồ sơ gọi `submitApplication` với `status: 'submitted'` và `formData` như sau:
  - Đất: `applicant_name`, `applicant_id_number`, `applicant_phone`, `plot_number`, `map_sheet_number`, `land_area` (dùng `parseArea`), `land_address`. Các ô còn lại để nguyên id input làm khoá trong `form_data`.
  - HKD: `business_name_vn`, `business_type`, `headquarters_address`, `charter_capital` (số nguyên đồng). Trước khi code, đối chiếu tên khoá với `procedure_record_repository.save_procedure_record`.
  - Trong lúc gửi thì disable nút. Thành công thì chuyển về `/user-dashboard`. Lỗi thì hiện `backendMessage`.
- [ ] **Step 5:** Kiểm tra thủ công. Nộp một hồ sơ đất và một hồ sơ HKD, cả hai phải xuất hiện đầu bảng ở user-dashboard và ở admin dashboard. Trên DB, cột `land_area` phải có số. Chạy `grep -n "NGUYỄN VĂN AN\|citizenDatabase" AI_Legal/src/pages/applicationSubmission.html`, không còn kết quả nào.
- [ ] **Step 6:** Commit `feat: trang tạo hồ sơ dùng danh mục thủ tục và lưu hồ sơ thật`.

---

**Ngoài phạm vi (giữ "Sắp ra mắt"):** lịch hẹn tiếp dân, đánh giá sơ bộ bằng AI, tra cứu quy hoạch, in biên nhận, trạng thái "cần bổ sung" (bảng hiện chưa có giá trị này), admin đổi trạng thái hồ sơ.
