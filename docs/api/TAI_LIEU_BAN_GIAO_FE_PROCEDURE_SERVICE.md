# TÀI LIỆU BÀN GIAO TÍCH HỢP API PROCEDURE SERVICE (DÀNH CHO FRONTEND)

Tài liệu kỹ thuật tổng hợp toàn bộ các API Backend của **Procedure Service** được thiết kế để đội ngũ Frontend (FE) tích hợp xây dựng giao diện hiển thị danh mục, tìm kiếm, render biểu mẫu động, quản lý vòng đời hồ sơ người dùng (tạo nháp, cập nhật, xóa, nộp chính thức, xem lịch sử) và bảo mật JWT.

---

## 1. Thông tin kết nối & Quy ước hệ thống

- **Base URL qua API Gateway (FE gọi chính thức):** `http://localhost:8080/api/procedures`
- **Base URL Service độc lập (Local test BE):** `http://localhost:8000/api/v1/procedures`
- **Xác thực (Authentication):**
  - **Tra cứu danh mục & Biểu mẫu thủ tục:** Công khai (Public, không yêu cầu đăng nhập).
  - **Quản lý hồ sơ cá nhân:** Yêu cầu đăng nhập qua JWT Bearer Token (`Authorization: Bearer <token>`) hoặc HttpOnly cookie (`access_token`).
  - **Lưu nháp hồ sơ ban đầu (`POST /applications`):** Hỗ trợ cả người dùng ẩn danh (guest) lẫn người dùng đã đăng nhập (tự động gắn `user_id` từ token).
- **Format dữ liệu:** `application/json; charset=utf-8`
- **Chuẩn bọc kết quả (Response Wrapper):**
  - **Thành công (HTTP 200, 201):**
    ```json
    {
      "data": { ... },
      "error": null,
      "request_id": "req-xxxx-xxxx"
    }
    ```
  - **Lỗi (HTTP 400, 401, 403, 404, 422, 500):**
    ```json
    {
      "data": null,
      "error": {
        "code": "MÃ_LỖI",
        "message": "Thông báo lỗi tiếng Việt hiển thị được trên UI",
        "details": [ ... ]
      },
      "request_id": "req-xxxx-xxxx"
    }
    ```

---

## 2. Bảng tổng hợp toàn bộ các API Endpoints

| STT | Method | Endpoint qua Gateway (FE gọi) | Endpoint trực tiếp Backend | Xác thực | Chức năng trên Giao diện Frontend |
|:---:|:---:|---|---|:---:|---|
| **1** | `GET` | `/api/procedures/health` | `/api/v1/procedures/health` | Không | **Kiểm tra trạng thái Service & Database** (cho Header/Status bar) |
| **2** | `GET` | `/api/procedures/domains` | `/api/v1/procedures/domains` | Không | **Lấy danh sách Lĩnh vực** (cho Tabs / Filter: Tất cả, Kinh doanh, Đất đai) |
| **3** | `GET` | `/api/procedures/domains/{domain}` | `/api/v1/procedures/domains/{domain}` | Không | **Danh sách chi tiết thủ tục theo lĩnh vực** (`land` / `business`) |
| **4** | `GET` | `/api/procedures/domains/{domain}/names` | `/api/v1/procedures/domains/{domain}/names` | Không | **Tên & mã thủ tục rút gọn** (cho Select box / Dropdown menu) |
| **5** | `GET` | `/api/procedures` | `/api/v1/procedures` | Không | **Danh sách thủ tục phân trang** (kèm lọc lĩnh vực và tìm kiếm nhanh `q`) |
| **6** | `POST` | `/api/procedures/search` | `/api/v1/procedures/search` | Không | **Tìm kiếm thông minh** (hỗ trợ câu hỏi tự nhiên, từ khóa có dấu/không dấu) |
| **7** | `GET` | `/api/procedures/{id_hoặc_code}` | `/api/v1/procedures/{id_hoặc_code}` | Không | **Chi tiết thủ tục** (nhận UUID hoặc mã: `DD-001`, `HKD-001`...) |
| **8** | `GET` | `/api/procedures/{id_hoặc_code}/requirements` | `/api/v1/procedures/{id_hoặc_code}/requirements` | Không | **Checklist hồ sơ giấy tờ cần chuẩn bị** |
| **9** | `GET` | `/api/procedures/{id_hoặc_code}/conditions` | `/api/v1/procedures/{id_hoặc_code}/conditions` | Không | **Danh sách điều kiện thực hiện thủ tục** |
| **10** | `GET` | `/api/procedures/{id_hoặc_code}/fees` | `/api/v1/procedures/{id_hoặc_code}/fees` | Không | **Bảng phí, lệ phí chi tiết** |
| **11** | `GET` | `/api/procedures/form-schema/{procedure_code}` | `/api/v1/procedures/form-schema/{procedure_code}` | Không | **HÀM TRUNG GIAN (DISPATCHER): Lấy Form Schema động của 25 thủ tục** |
| **12** | `GET` | `/api/procedures/applications` | `/api/v1/procedures/applications` | **Bearer** | **Danh sách hồ sơ của tôi** (phân trang, lọc theo lĩnh vực `domain`, trạng thái `status`, tìm kiếm) |
| **13** | `POST` | `/api/procedures/applications` | `/api/v1/procedures/applications` | Tùy chọn | **Lưu hồ sơ người dùng điền** (tự động phân loại bảng Đất đai / Kinh doanh) |
| **14** | `GET` | `/api/procedures/applications/{domain}/{record_id}` | `/api/v1/procedures/applications/{domain}/{record_id}` | Tùy chọn | **Tra cứu lại hồ sơ đã lưu** (để render lại dữ liệu vào form) |
| **15** | `PUT` | `/api/procedures/applications/{domain}/{record_id}` | `/api/v1/procedures/applications/{domain}/{record_id}` | **Bearer** | **Cập nhật bản nháp hồ sơ** (chỉ chủ sở hữu, chỉ cho phép khi đang là `draft`) |
| **16** | `DELETE` | `/api/procedures/applications/{domain}/{record_id}` | `/api/v1/procedures/applications/{domain}/{record_id}` | **Bearer** | **Xóa bản nháp hồ sơ** (chỉ chủ sở hữu, chỉ cho phép khi đang là `draft`) |
| **17** | `POST` | `/api/procedures/applications/{domain}/{record_id}/submit` | `/api/v1/procedures/applications/{domain}/{record_id}/submit` | **Bearer** | **Nộp chính thức hồ sơ** (kiểm tra đầy đủ trường bắt buộc, khóa hồ sơ) |

---

## 3. Danh sách 25 Thủ tục chi tiết & Dispatcher Form

Backend tích hợp sẵn **1 Hàm điều phối trung gian** (`GET /form-schema/{procedure_code}`) kết nối đến **25 hàm chuyên biệt** tương ứng với 25 thủ tục chi tiết:

### A. Lĩnh vực Đất đai & Bất động sản (12 thủ tục - Bảng đích: `land_procedure_records`)
1. **`DD-001`**: Đăng ký biến động quyền sử dụng đất do chuyển nhượng (Mẫu 11/ĐK - Sang tên sổ đỏ)
2. **`DD-002`**: Cấp đổi Giấy chứng nhận quyền sử dụng đất (Mẫu 10/ĐK - Đổi sang phôi mới)
3. **`DD-003`**: Tách thửa hoặc hợp thửa đất
4. **`DD-004`**: Đăng ký biến động quyền sử dụng đất do tặng cho
5. **`DD-005`**: Đăng ký biến động quyền sử dụng đất do thừa kế
6. **`DD-006`**: Cấp lại Giấy chứng nhận quyền sử dụng đất do bị mất
7. **`DD-007`**: Đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất
8. **`DD-008`**: Xóa đăng ký thế chấp quyền sử dụng đất
9. **`DD-009`**: Chuyển mục đích sử dụng đất phải được phép của cơ quan nhà nước có thẩm quyền
10. **`DD-010`**: Gia hạn thời hạn sử dụng đất ngoài khu công nghệ cao, khu kinh tế
11. **`DD-011`**: Đính chính Giấy chứng nhận đã cấp có sai sót
12. **`DD-012`**: Đăng ký đất đai, tài sản gắn liền với đất lần đầu (Mẫu 04/ĐK)

### B. Lĩnh vực Hộ kinh doanh & Doanh nghiệp (13 thủ tục - Bảng đích: `business_procedure_records`)
1. **`HKD-001`**: Đăng ký thành lập Hộ kinh doanh (Phụ lục III-1)
2. **`HKD-002`**: Đăng ký thay đổi nội dung đăng ký Hộ kinh doanh (Phụ lục III-2)
3. **`DN-001`**: Đăng ký thành lập Công ty TNHH một thành viên (Phụ lục I-2)
4. **`DN-002`**: Đăng ký thành lập Công ty TNHH hai thành viên trở lên (Phụ lục I-3)
5. **`DN-003`**: Đăng ký thành lập Công ty Cổ phần (Phụ lục I-4)
6. **`DN-004`**: Đăng ký thành lập Doanh nghiệp tư nhân (Phụ lục I-1)
7. **`DN-005`**: Đăng ký thay đổi Người đại diện theo pháp luật của doanh nghiệp (Phụ lục II-2)
8. **`DN-006`**: Đăng ký thay đổi Vốn điều lệ, tỷ lệ vốn góp của doanh nghiệp (Phụ lục II-1)
9. **`DN-007`**: Thông báo thay đổi Ngành, nghề kinh doanh của doanh nghiệp (Phụ lục II-1)
10. **`DN-008`**: Đăng ký thay đổi Địa chỉ trụ sở chính của doanh nghiệp (Phụ lục II-1)
11. **`DN-009`**: Đăng ký hoạt động Chi nhánh, Văn phòng đại diện, Địa điểm kinh doanh (Phụ lục II-7)
12. **`DN-010`**: Thông báo Tạm ngừng kinh doanh của doanh nghiệp (Phụ lục II-15)
13. **`DN-011`**: Thông báo Giải thể doanh nghiệp (Phụ lục II-18)

---

## 4. Chi tiết từng API & Dữ liệu mẫu (Request / Response)

### 4.1. `GET /api/procedures/health`
Kiểm tra sức khỏe service và kết nối database.

---

### 4.2. `GET /api/procedures/domains`
Lấy danh sách các lĩnh vực hỗ trợ kèm số lượng thủ tục thực tế (cho Tab / Filter trên UI).

---

### 4.3. `GET /api/procedures/domains/{domain}/names`
Lấy danh sách tên & mã thủ tục rút gọn theo lĩnh vực (cho Dropdown menu / Select box).

---

### 4.4. `GET /api/procedures/domains/{domain}`
Lấy danh sách chi tiết các thủ tục thuộc một lĩnh vực (`land` hoặc `business`).

---

### 4.5. `GET /api/procedures`
Lấy danh sách thủ tục có phân trang, hỗ trợ lọc theo lĩnh vực và tìm kiếm nhanh.
- **Query params:** `domain` (`land` / `business`), `page` (mặc định 1), `page_size` (mặc định 20), `q` hoặc `search` (từ khóa).

---

### 4.6. `POST /api/procedures/search`
Tìm kiếm ngữ nghĩa thông minh theo câu hỏi tự nhiên hoặc từ khóa.

---

### 4.7. `GET /api/procedures/{id_hoặc_code}`
Lấy toàn bộ thông tin chi tiết của một thủ tục (hỗ trợ cả UUID lẫn mã viết tắt: `DD-001`, `HKD-001`...).

---

### 4.8. `GET /api/procedures/{id_hoặc_code}/requirements`
Lấy danh sách hồ sơ, giấy tờ cần chuẩn bị cho thủ tục (cho màn hình Checklist giấy tờ).

---

### 4.9. `GET /api/procedures/{id_hoặc_code}/conditions`
Lấy danh sách các điều kiện thực hiện của thủ tục.

---

### 4.10. `GET /api/procedures/{id_hoặc_code}/fees`
Lấy chi tiết bảng biểu phí và lệ phí của thủ tục.

---

### 4.11. `GET /api/procedures/form-schema/{procedure_code}`
**HÀM TRUNG GIAN ĐIỀU PHỐI (DISPATCHER)**: Lấy form schema động của bất kỳ thủ tục nào trong 25 thủ tục.
Trả về danh sách các `sections` và `fields` (kèm nhãn, kiểu dữ liệu, trường bắt buộc, regex validation, danh sách options).

---

### 4.12. `GET /api/procedures/applications` *(MỚI - ƯU TIÊN CẤP 1)*
**Lấy danh sách hồ sơ của người dùng hiện tại** phục vụ màn hình "Hồ sơ của tôi" (My Applications).
- **Yêu cầu xác thực:** Bearer Token qua header `Authorization: Bearer <jwt>` hoặc cookie.
- **Query Parameters:**
  - `domain`: Lọc theo lĩnh vực (`land` hoặc `business`) - Tùy chọn.
  - `status`: Lọc theo trạng thái (`draft` hoặc `submitted`) - Tùy chọn.
  - `page`: Số trang (mặc định 1).
  - `page_size`: Số bản ghi mỗi trang (mặc định 20, tối đa 100).
  - `search`: Từ khóa tìm kiếm (họ tên người làm đơn, mã thủ tục...).
- **Response mẫu (HTTP 200 OK):**
  ```json
  {
    "data": {
      "items": [
        {
          "id": "34c3b7a2-1f03-425a-b4f4-6f2b06f9528a",
          "user_id": "11111111-1111-1111-1111-111111111111",
          "procedure_code": "DD-001",
          "procedure_id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30",
          "status": "draft",
          "applicant_name": "Nguyễn Văn A",
          "applicant_id_number": "012345678901",
          "applicant_phone": "0901234567",
          "plot_number": "123",
          "map_sheet_number": "45",
          "land_address": "Số 10 Đường ABC, Phường 1, Quận 1, TP.HCM",
          "land_area": 120.5,
          "form_data": { ... },
          "extra_data": { "note": "Hồ sơ nháp đang điền" },
          "created_at": "2026-10-14T08:00:00Z",
          "updated_at": "2026-10-14T08:30:00Z",
          "domain": "land",
          "target_table": "land_procedure_records"
        }
      ],
      "pagination": {
        "page": 1,
        "page_size": 20,
        "total": 1,
        "total_pages": 1
      }
    },
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```
- **Lỗi có thể trả về:**
  - `401 Unauthorized`: Chưa truyền JWT Token hoặc token hết hạn.

---

### 4.13. `POST /api/procedures/applications`
**Lưu hồ sơ người dùng đã điền (Tạo mới bản nháp hoặc nộp)**.
Backend tự động xác định thủ tục thuộc Đất đai (`land_procedure_records`) hay Doanh nghiệp (`business_procedure_records`).
- **Xác thực:** Tùy chọn. Nếu người dùng đăng nhập, backend tự động gán `user_id` từ token.
- **Request Body (JSON):**
  ```json
  {
    "procedure_code": "DD-001",
    "status": "draft",
    "form_data": {
      "applicant_name": "Nguyễn Văn A",
      "applicant_id_number": "012345678901",
      "applicant_phone": "0901234567",
      "land_address": "Số 10 Đường ABC, Quận 1, TP.HCM",
      "land_area": 120.5
    },
    "extra_data": {
      "note": "Lưu nháp lần 1"
    }
  }
  ```

---

### 4.14. `GET /api/procedures/applications/{domain}/{record_id}`
**Tra cứu chi tiết một hồ sơ đã lưu** theo `domain` (`land` hoặc `business`) và `record_id`.
- **Response mẫu (HTTP 200 OK):**
  Trả về chi tiết bản ghi gồm `form_data`, `status`, `applicant_name`, thông tin thửa đất/doanh nghiệp.
- **Lỗi có thể trả về:**
  - `400 Bad Request`: `domain` không hợp lệ (khác `land`, `business`).
  - `403 Forbidden`: Hồ sơ thuộc về người dùng khác.
  - `404 Not Found`: Không tìm thấy bản ghi theo `record_id`.

---

### 4.15. `PUT /api/procedures/applications/{domain}/{record_id}` *(MỚI - ƯU TIÊN CẤP 1)*
**Cập nhật dữ liệu bản nháp hồ sơ** (`form_data`, `extra_data`, `status`).
- **Yêu cầu xác thực:** Bearer Token.
- **Quy tắc nghiệp vụ:**
  - Chỉ người tạo hồ sơ (`user_id` trùng khớp với token) mới có quyền chỉnh sửa.
  - Chỉ cho phép sửa khi hồ sơ ở trạng thái nháp (`status: 'draft'`). Hồ sơ đã nộp chính thức (`submitted`) sẽ bị khóa.
- **Request Body (JSON):**
  ```json
  {
    "form_data": {
      "applicant_name": "Nguyễn Văn A (Cập nhật)",
      "applicant_phone": "0988999888",
      "land_area": 150.0
    },
    "extra_data": {
      "note": "Bổ sung thông tin đợt 2"
    },
    "status": "draft"
  }
  ```
- **Response mẫu (HTTP 200 OK):** Trả về bản ghi sau khi đã cập nhật và đồng bộ các cột searchable.
- **Lỗi có thể trả về:**
  - `400 Bad Request`: Hồ sơ đang ở trạng thái `submitted`, không được sửa (`APPLICATION_LOCKED`).
  - `401 Unauthorized`: Chưa truyền token.
  - `403 Forbidden`: Bạn không phải là chủ sở hữu của hồ sơ này.
  - `404 Not Found`: Không tìm thấy hồ sơ.

---

### 4.16. `DELETE /api/procedures/applications/{domain}/{record_id}` *(MỚI - ƯU TIÊN CẤP 1)*
**Xóa một bản nháp hồ sơ người dùng**.
- **Yêu cầu xác thực:** Bearer Token.
- **Quy tắc nghiệp vụ:**
  - Chỉ người tạo hồ sơ mới có quyền xóa.
  - Chỉ cho phép xóa khi hồ sơ ở trạng thái nháp (`status: 'draft'`).
- **Response mẫu (HTTP 200 OK):**
  ```json
  {
    "data": {
      "success": true,
      "message": "Đã xóa bản nháp hồ sơ thành công"
    },
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```
- **Lỗi có thể trả về:**
  - `400 Bad Request`: Hồ sơ đã nộp chính thức, không thể xóa.
  - `403 Forbidden`: Không có quyền xóa hồ sơ của người khác.
  - `404 Not Found`: Không tìm thấy hồ sơ.

---

### 4.17. `POST /api/procedures/applications/{domain}/{record_id}/submit` *(MỚI - ƯU TIÊN CẤP 1)*
**Nộp chính thức hồ sơ từ trạng thái bản nháp (Official Submission)**.
- **Yêu cầu xác thực:** Bearer Token.
- **Quy tắc nghiệp vụ:**
  - **Kiểm tra chủ quyền:** Chỉ người tạo hồ sơ mới được nộp.
  - **Kiểm tra tính hợp lệ (Form Schema Validation):** Backend tự động đối chiếu dữ liệu trong `form_data` với danh sách trường bắt buộc (`required: true`) theo Schema của thủ tục đó. Nếu thiếu bất kỳ trường bắt buộc nào, API sẽ trả về lỗi `400 VALIDATION_ERROR` kèm danh sách cụ thể các trường bị thiếu.
  - **Khóa hồ sơ:** Chuyển `status` thành `submitted` và cập nhật `updated_at`.
- **Response mẫu thành công (HTTP 200 OK):**
  ```json
  {
    "data": {
      "id": "34c3b7a2-1f03-425a-b4f4-6f2b06f9528a",
      "user_id": "11111111-1111-1111-1111-111111111111",
      "procedure_code": "DD-001",
      "status": "submitted",
      "applicant_name": "Nguyễn Văn A",
      "updated_at": "2026-10-14T09:00:00Z"
    },
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```
- **Lỗi có thể trả về:**
  - `400 Bad Request / VALIDATION_ERROR`:
    ```json
    {
      "data": null,
      "error": {
        "code": "VALIDATION_ERROR",
        "message": "Hồ sơ còn thiếu các trường bắt buộc: Ngày tháng năm sinh (applicant_dob), Số CCCD (applicant_id_number)"
      },
      "request_id": "req-3fa85f64"
    }
    ```
  - `400 Bad Request / APPLICATION_ALREADY_SUBMITTED`: Hồ sơ đã nộp trước đó.
  - `403 Forbidden`: Không có quyền nộp hồ sơ này.
  - `404 Not Found`: Không tìm thấy hồ sơ.

---

## 5. Hướng dẫn Frontend Render Biểu mẫu & Luồng Vòng đời Hồ sơ

### 5.1. Bảng ánh xạ `field_type` sang UI Components

| `field_type` | Component trên giao diện React / Vue | Ví dụ thuộc tính |
|---|---|---|
| `text` | `<input type="text" />` hoặc `<Input />` | `placeholder`, `maxLength` |
| `number` | `<input type="number" />` hoặc `<InputNumber />` | `min`, `max`, `step` |
| `date` | `<input type="date" />` hoặc `<DatePicker />` | `format="YYYY-MM-DD"` |
| `textarea` | `<textarea rows={4} />` hoặc `<Textarea />` | Dùng cho nội dung dài |
| `select` | `<select>` hoặc `<Select options={field.options} />` | Chọn 1 từ danh sách cố định |
| `radio` | `<RadioGroup options={field.options} />` | Chọn 1 trong số ít lựa chọn |
| `checkbox` | `<Checkbox />` | Đồng ý cam kết hoặc chọn nhiều |

### 5.2. Sơ đồ Vòng đời Hồ sơ trên Giao diện FE

```
[Chọn thủ tục từ Catalog]
            │
            ▼
[Tải Schema: GET /form-schema/{code}]
            │
            ▼
[Người dùng điền dữ liệu Form]
      │                   │
      ▼ (Lưu tạm)         ▼ (Nộp ngay)
[POST /applications]      [POST /applications (status: 'submitted')]
(status: 'draft')         (Backend kiểm tra đủ trường bắt buộc)
      │
      ▼
[Màn hình "Hồ sơ của tôi": GET /applications]
      │
      ├─► [Xem lại]: GET /applications/{domain}/{id}
      │
      ├─► [Sửa tiếp bản nháp]: PUT /applications/{domain}/{id}
      │
      ├─► [Xóa bản nháp]: DELETE /applications/{domain}/{id}
      │
      └─► [Nộp chính thức]: POST /applications/{domain}/{id}/submit
                │
                ├─► [Thiếu trường]: Báo lỗi VALIDATION_ERROR -> FE focus ô nhập còn thiếu
                │
                └─► [Đủ trường]: Chuyển trạng thái 'submitted' (Khóa hồ sơ)
```

---

## 6. Code mẫu TypeScript / Axios cho Frontend sử dụng ngay

Đội ngũ Frontend có thể copy đoạn code dưới đây vào file `src/api/procedureApi.ts`:

```typescript
import axios from 'axios';

// Cấu hình Axios Client gọi qua Gateway
const apiClient = axios.create({
  baseURL: 'http://localhost:8080/api/procedures',
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 15000,
  withCredentials: true, // Hỗ trợ gửi kèm Cookie xác thực
});

// Tự động gắn Bearer Token nếu có trong localStorage/session
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Interceptor bọc unwrap data tiện dụng
apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const errData = error.response?.data?.error || {
      code: 'NETWORK_ERROR',
      message: error.message || 'Không thể kết nối đến máy chủ',
    };
    return Promise.reject(errData);
  }
);

// ==========================================
// 1. CÁC API TRA CỨU DANH MỤC & BIỂU MẪU (PUBLIC)
// ==========================================

export const checkHealth = async () => {
  return apiClient.get('/health');
};

export const getDomains = async () => {
  const res: any = await apiClient.get('/domains');
  return res.data;
};

export const getProcedureNames = async (domain: 'land' | 'business') => {
  const res: any = await apiClient.get(`/domains/${domain}/names`);
  return res.data;
};

export const listProcedures = async (params?: { domain?: string; page?: number; page_size?: number; q?: string }) => {
  const res: any = await apiClient.get('', { params });
  return res.data;
};

export const searchProcedures = async (query: string, limit = 20) => {
  const res: any = await apiClient.post('/search', { query }, { params: { limit } });
  return res.data;
};

export const getProcedureDetail = async (procedureIdOrCode: string) => {
  const res: any = await apiClient.get(`/${procedureIdOrCode}`);
  return res.data;
};

export const getProcedureFormSchema = async (procedureCode: string) => {
  const res: any = await apiClient.get(`/form-schema/${procedureCode}`);
  return res.data;
};

// ==========================================
// 2. CÁC API QUẢN LÝ HỒ SƠ NGƯỜI DÙNG (APPLICATIONS)
// ==========================================

export interface ApplicationFilterParams {
  domain?: 'land' | 'business';
  status?: 'draft' | 'submitted';
  page?: number;
  page_size?: number;
  search?: string;
}

/** Lấy danh sách hồ sơ của người dùng đang đăng nhập (kèm phân trang & bộ lọc) */
export const listMyApplications = async (params?: ApplicationFilterParams) => {
  const res: any = await apiClient.get('/applications', { params });
  return res.data; // { items: [...], pagination: { page, page_size, total, total_pages } }
};

export interface SubmitApplicationPayload {
  procedure_code: string;
  user_id?: string | null;
  status?: 'draft' | 'submitted';
  form_data: Record<string, any>;
  extra_data?: Record<string, any>;
}

/** Tạo mới hồ sơ (lưu nháp ban đầu hoặc nộp) */
export const createApplication = async (payload: SubmitApplicationPayload) => {
  const res: any = await apiClient.post('/applications', payload);
  return res.data;
};

/** Xem chi tiết hồ sơ đã lưu */
export const getSavedApplication = async (domain: 'land' | 'business', recordId: string) => {
  const res: any = await apiClient.get(`/applications/${domain}/${recordId}`);
  return res.data;
};

export interface UpdateApplicationPayload {
  form_data?: Record<string, any>;
  extra_data?: Record<string, any>;
  status?: 'draft' | 'submitted';
}

/** Cập nhật hồ sơ bản nháp */
export const updateDraftApplication = async (
  domain: 'land' | 'business',
  recordId: string,
  payload: UpdateApplicationPayload
) => {
  const res: any = await apiClient.put(`/applications/${domain}/${recordId}`, payload);
  return res.data;
};

/** Xóa hồ sơ bản nháp */
export const deleteDraftApplication = async (domain: 'land' | 'business', recordId: string) => {
  const res: any = await apiClient.delete(`/applications/${domain}/${recordId}`);
  return res.data; // { success: true, message: ... }
};

/** Nộp chính thức bản nháp (Backend tự động kiểm tra trường bắt buộc) */
export const submitDraftApplication = async (domain: 'land' | 'business', recordId: string) => {
  const res: any = await apiClient.post(`/applications/${domain}/${recordId}/submit`);
  return res.data;
};
```
