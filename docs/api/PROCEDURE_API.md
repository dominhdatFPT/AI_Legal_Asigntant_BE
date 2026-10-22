# Hướng dẫn tích hợp API Procedure Service cho Frontend

Tài liệu bàn giao danh sách các endpoint, tham số và dữ liệu mẫu của **Procedure Service** dành cho đội ngũ Frontend (FE) phát triển giao diện.

---

## 1. Thông tin chung & Quy ước kết nối

- **Base URL qua Gateway (FE gọi chính thức):** `http://localhost:8080` (prefix `/api/...`)
- **Base URL khi chạy riêng Service (Local test độc lập):** `http://localhost:8000` (prefix `/api/v1/...`)
- **Xác thực (Authentication):** **Không yêu cầu đăng nhập**. Mọi thông tin thủ tục hành chính là công khai theo quy định pháp luật.
- **Header khuyến nghị:**
  ```http
  Content-Type: application/json
  X-Request-ID: req-<uuid-hoặc-chuỗi-tùy-ý>  (nếu không gửi, Backend tự sinh)
  ```
- **Cấu trúc Response chuẩn:**
  - Thành công:
    ```json
    {
      "data": { ... },
      "error": null,
      "request_id": "req-123"
    }
    ```
  - Thất bại:
    ```json
    {
      "data": null,
      "error": {
        "code": "MÃ_LỖI",
        "message": "Thông báo lỗi tiếng Việt hiển thị được trên UI",
        "details": [ ... ]
      },
      "request_id": "req-123"
    }
    ```
- **Chế độ Mock Data tự động:** Backend đã tích hợp sẵn cơ chế **Fallback Mock Data thực tế** (bao gồm `HKD-001`, `HKD-002`, `HKD-003`, `DD-001`, `DD-002`...). Ngay cả khi chưa kết nối database Supabase, các API vẫn trả về dữ liệu chuẩn xác 100% để FE làm UI.

---

## 2. Bảng tổng hợp Endpoint cho Frontend

| Method | URL qua Gateway (FE gọi) | Chức năng trên Giao diện Frontend |
|---|---|---|
| `GET` | `/api/procedures/health` | **Kiểm tra trạng thái service & database** (gửi 1 query nhẹ kiểm tra Supabase) |
| `GET` | `/api/procedures/domains` | **Lấy danh sách lĩnh vực** (`land`, `business`) kèm số lượng thủ tục thực tế (cho Tab / Filter) |
| `GET` | `/api/procedures/domains/{domain}` | **Lấy chi tiết danh sách thủ tục theo lĩnh vực** (tên, mã, phí, thời gian, hồ sơ) |
| `GET` | `/api/procedures/domains/{domain}/names` | **Lấy danh sách tên & mã thủ tục theo lĩnh vực** (cho Select / Dropdown menu) |
| `GET` | `/api/procedures` | Danh sách thủ tục (phân trang, lọc theo lĩnh vực, tìm kiếm nhanh `q`) |
| `POST` | `/api/procedures/search` | Tìm kiếm thủ tục bằng câu hỏi hoặc từ khóa (hỗ trợ có dấu và không dấu) |
| `GET` | `/api/procedures/{id_hoặc_code}` | Chi tiết đầy đủ của một thủ tục (chấp nhận cả UUID lẫn mã `HKD-001`, `DD-001`) |
| `GET` | `/api/procedures/{id_hoặc_code}/requirements` | Danh mục hồ sơ, giấy tờ cần chuẩn bị (cho màn hình Checklist giấy tờ) |
| `GET` | `/api/procedures/fees` | Chi tiết các khoản phí và lệ phí |
| `GET` | `/api/procedures/form-schema/{procedure_code}` | **Lấy Form Schema động của 25 thủ tục** |
| `GET` | `/api/procedures/applications` | **Danh sách hồ sơ của tôi** (phân trang, lọc theo domain, status, tìm kiếm - Yêu cầu Bearer Token) |
| `POST` | `/api/procedures/applications` | Lưu hồ sơ người dùng điền (tạo nháp hoặc nộp) |
| `GET` | `/api/procedures/applications/{domain}/{record_id}` | Tra cứu chi tiết một hồ sơ đã lưu |
| `PUT` | `/api/procedures/applications/{domain}/{record_id}` | **Cập nhật hồ sơ bản nháp** (chỉ chủ sở hữu, yêu cầu Bearer Token) |
| `DELETE` | `/api/procedures/applications/{domain}/{record_id}` | **Xóa hồ sơ bản nháp** (chỉ chủ sở hữu, yêu cầu Bearer Token) |
| `POST` | `/api/procedures/applications/{domain}/{record_id}/submit` | **Nộp chính thức hồ sơ** (kiểm tra trường bắt buộc, yêu cầu Bearer Token) |

---

## 3. Hướng dẫn chi tiết từng màn hình Frontend cần làm

### Màn hình 1: Danh mục Thủ tục hành chính (Procedure Catalog / List)

#### A. Kiểm tra trạng thái hệ thống (Health Check)
- **Endpoint:** `GET /api/procedures/health`
- **Mục đích:** FE hiển thị trạng thái kết nối máy chủ và cơ sở dữ liệu trên thanh Header/Status bar.
- **Response mẫu:**
  ```json
  {
    "status": "healthy",
    "service": "procedure-service",
    "version": "0.1.0",
    "database": {
      "status": "connected",
      "connected": true,
      "message": "Kết nối cơ sở dữ liệu Supabase hoạt động tốt."
    },
    "request_id": "aacb0a6ad559be38f3ba4f59e3b6387b"
  }
  ```

#### B. Lấy danh sách Lĩnh vực để tạo Tab / Dropdown bộ lọc
- **Endpoint:** `GET /api/procedures/domains`
- **Mục đích:** FE render các Tab: "Tất cả (25)", "Doanh nghiệp & Hộ kinh doanh (13)", "Đất đai & Bất động sản (12)".
- **Response mẫu:**
  ```json
  {
    "data": [
      {
        "code": "business",
        "name": "Doanh nghiệp & Hộ kinh doanh",
        "description": "Thủ tục thành lập, thay đổi thông tin, đăng ký thuế, tạm ngừng, giải thể doanh nghiệp và hộ kinh doanh cá thể.",
        "icon": "building",
        "count": 13
      },
      {
        "code": "land",
        "name": "Đất đai & Bất động sản",
        "description": "Thủ tục đăng ký biến động, cấp đổi, cấp mới, tách thửa, thế chấp, sang tên sổ đỏ theo Luật Đất đai 2024.",
        "icon": "map-pin",
        "count": 12
      }
    ],
    "total_procedures": 25,
    "error": null,
    "request_id": "req-123"
  }
  ```

#### C. Lấy Tên và Thông tin chi tiết thủ tục theo Lĩnh vực (Hiển thị Card / Bảng)
- **Endpoint:** `GET /api/procedures/domains/{domain}` (hoặc `GET /api/procedures/domains/{domain}/procedures`)
- **Tham số đường dẫn (`path`):** `domain` = `land` hoặc `business` hoặc `all`.
- **Query parameters:**
  - `include_details` *(mặc định `true`)*: Bao gồm thành phần hồ sơ, căn cứ pháp lý, điều kiện.
  - `limit` *(mặc định `100`)*: Số lượng thủ tục tối đa cần lấy.
- **Mục đích:** Khi người dùng bấm chọn một Tab Lĩnh vực (ví dụ "Đất đai"), FE chỉ cần gọi endpoint này là nhận về toàn bộ danh sách thủ tục chi tiết để render ra màn hình ngay lập tức mà không cần gọi thêm API nào khác.
- **Response mẫu:**
  ```json
  {
    "data": {
      "domain": "land",
      "total": 12,
      "items": [
        {
          "id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30",
          "code": "DD-001",
          "name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
          "domain": "land",
          "description": "Thủ tục sang tên, chuyển nhượng quyền sử dụng đất...",
          "processing_days": 10,
          "submission_channels": ["direct", "online", "postal"],
          "receiving_agency_level": "district",
          "fee": 0,
          "fee_formatted": "0 ₫ (Miễn phí)",
          "fee_note": "Lệ phí địa chính...",
          "requirements_count": 4,
          "conditions_count": 2,
          "legal_bases_count": 2,
          "requirements": [ ... ],
          "conditions": [ ... ],
          "legal_bases": [ ... ]
        }
      ]
    },
    "error": null,
    "request_id": "req-123"
  }
  ```

#### D. Lấy Tên thủ tục gọn nhẹ theo Lĩnh vực (Dành cho Dropdown / Select box)
- **Endpoint:** `GET /api/procedures/domains/{domain}/names`
- **Mục đích:** Phục vụ các Form tiếp nhận hồ sơ, popup chọn thủ tục hành chính.
- **Response mẫu:**
  ```json
  {
    "data": {
      "domain": "land",
      "total": 12,
      "items": [
        {
          "id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30",
          "code": "DD-001",
          "name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
          "domain": "land",
          "fee": 0,
          "fee_formatted": "0 ₫ (Miễn phí)",
          "processing_days": 10
        }
      ]
    },
    "error": null,
    "request_id": "req-123"
  }
  ```

#### B. Lấy danh sách thủ tục (có Phân trang & Lọc)
- **Endpoint:** `GET /api/procedures`
- **Query Parameters:**
  - `domain` *(tùy chọn)*: `'business'` hoặc `'land'`. Bỏ trống = tất cả.
  - `page` *(tùy chọn)*: Số trang (mặc định `1`, bắt đầu từ 1).
  - `page_size` *(tùy chọn)*: Số phần tử/trang (mặc định `20`, tối đa `100`).
  - `q` hoặc `search` *(tùy chọn)*: Từ khóa tìm kiếm nhanh trên thanh tìm kiếm.
- **Ví dụ gọi:** `GET /api/procedures?domain=business&page=1&page_size=10`
- **Response mẫu:**
  ```json
  {
    "data": {
      "items": [
        {
          "id": "e3b2c140-6b1a-4f51-8d2c-3965158a8a41",
          "code": "HKD-001",
          "name": "Đăng ký thành lập hộ kinh doanh",
          "domain": "business",
          "description": "Cấp giấy chứng nhận đăng ký hộ kinh doanh cá thể cho cá nhân hoặc các thành viên hộ gia đình.",
          "fee": 100000,
          "processing_days": 3
        },
        {
          "id": "f4c3d250-7c2b-5e62-9e3d-4076269b9b52",
          "code": "HKD-002",
          "name": "Đăng ký thay đổi nội dung đăng ký hộ kinh doanh",
          "domain": "business",
          "description": "Thủ tục điều chỉnh thông tin ngành nghề, địa chỉ, vốn hoặc người đại diện hộ kinh doanh cá thể.",
          "fee": 50000,
          "processing_days": 3
        }
      ],
      "pagination": {
        "page": 1,
        "page_size": 10,
        "total": 3,
        "total_pages": 1
      }
    },
    "error": null,
    "request_id": "req-123"
  }
  ```

---

### Màn hình 2: Tìm kiếm Thủ tục (Search Screen)

- **Endpoint:** `POST /api/procedures/search`
- **Request Body:**
  ```json
  {
    "query": "đăng ký hộ kinh doanh"
  }
  ```
- **Tính năng nổi bật:**
  - Hỗ trợ tìm kiếm tiếng Việt **có dấu** hoặc **không dấu** (ví dụ: gõ `"ho kinh doanh"`, `"sang ten so do"`, `"so hong"` đều tìm ra kết quả).
  - Tự động tìm kiếm qua tên thủ tục, mã thủ tục, mô tả và cả danh mục giấy tờ yêu cầu.
- **Response mẫu:**
  ```json
  {
    "data": {
      "items": [
        {
          "id": "e3b2c140-6b1a-4f51-8d2c-3965158a8a41",
          "code": "HKD-001",
          "name": "Đăng ký thành lập hộ kinh doanh",
          "domain": "business",
          "description": "Cấp giấy chứng nhận đăng ký hộ kinh doanh cá thể cho cá nhân hoặc các thành viên hộ gia đình.",
          "fee": 100000,
          "processing_days": 3
        }
      ]
    },
    "error": null,
    "request_id": "req-123"
  }
  ```
- **Khi không có kết quả:** Trả về HTTP `200` với `"items": []` (không trả 404).

---

### Màn hình 3: Chi tiết Thủ tục (Procedure Detail)

- **Endpoint:** `GET /api/procedures/{procedure_id}`
- **Tham số đường dẫn (`procedure_id`):**
  - Có thể truyền **UUID:** `GET /api/procedures/e3b2c140-6b1a-4f51-8d2c-3965158a8a41`
  - Hoặc truyền **Mã thủ tục:** `GET /api/procedures/HKD-001` hoặc `GET /api/procedures/DD-001`
- **Ý nghĩa các trường trả về:**
  - `processing_days`: Thời gian giải quyết (ngày làm việc).
  - `submission_channels`: Hình thức nộp hồ sơ (`direct`: Trực tiếp, `online`: Trực tuyến, `postal`: Bưu chính).
  - `receiving_agency_level`: Cấp cơ quan tiếp nhận (`district`: Quận/Huyện, `commune`: Xã/Phường, `province`: Tỉnh/TP).
  - `legal_bases`: Danh sách căn cứ pháp luật (Luật, Nghị định, điều khoản, link tra cứu).
  - `requirements`: Danh sách hồ sơ giấy tờ cần nộp.
  - `conditions`: Điều kiện thực hiện.
  - `fees`: Chi tiết lệ phí.
- **Response mẫu (HTTP 200):**
  ```json
  {
    "data": {
      "id": "e3b2c140-6b1a-4f51-8d2c-3965158a8a41",
      "code": "HKD-001",
      "name": "Đăng ký thành lập hộ kinh doanh",
      "domain": "business",
      "description": "Cấp giấy chứng nhận đăng ký hộ kinh doanh cá thể cho cá nhân hoặc các thành viên hộ gia đình.",
      "fee": 100000,
      "fee_note": "Miễn lệ phí nếu nộp hồ sơ đăng ký qua mạng điện tử",
      "processing_days": 3,
      "submission_channels": ["direct", "online"],
      "receiving_agency_level": "district",
      "legal_bases": [
        {
          "doc_number": "01/2021/NĐ-CP",
          "title": "Nghị định về đăng ký doanh nghiệp và hộ kinh doanh",
          "article_ref": "Điều 87",
          "source_url": "https://thuvienphapluat.vn/..."
        }
      ],
      "requirements": [
        {
          "id": "e3b2c140-req-001",
          "name": "Giấy đề nghị đăng ký hộ kinh doanh",
          "description": "Bản chính Giấy đề nghị đăng ký hộ kinh doanh theo mẫu quy định",
          "is_required": true,
          "copies": 1,
          "document_form": "original",
          "form_code": "Phụ lục III-1",
          "note": "Kèm chữ ký của tất cả các thành viên tham gia hộ kinh doanh"
        },
        {
          "id": "e3b2c140-req-002",
          "name": "Bản sao CCCD/CMND/Hộ chiếu",
          "description": "Bản sao hợp lệ giấy tờ pháp lý của cá nhân/các thành viên hộ gia đình",
          "is_required": true,
          "copies": 1,
          "document_form": "copy",
          "form_code": null,
          "note": "Xuất trình bản chính để đối chiếu hoặc bản chứng thực"
        }
      ],
      "conditions": [
        {
          "id": "e3b2c140-cond-001",
          "condition_code": "COND-01",
          "title": "Công dân Việt Nam đủ 18 tuổi",
          "description": "Cá nhân, thành viên hộ gia đình là công dân Việt Nam đủ 18 tuổi, có năng lực hành vi dân sự đầy đủ.",
          "is_mandatory": true
        }
      ],
      "fees": [
        {
          "id": "e3b2c140-fee-001",
          "fee_type": "administrative_fee",
          "name": "Lệ phí cấp Giấy chứng nhận đăng ký hộ kinh doanh",
          "amount": 100000,
          "unit": "lần",
          "is_exemptible": true,
          "note": "Miễn lệ phí nếu đăng ký qua cổng dịch vụ công trực tuyến"
        }
      ],
      "effective_from": "2021-01-04",
      "updated_at": "2026-10-02T02:00:00Z"
    },
    "error": null,
    "request_id": "req-123"
  }
  ```

---

### Màn hình 4: Checklist Giấy tờ cần chuẩn bị (Document Checklist)

- **Endpoint:** `GET /api/procedures/{procedure_id}/requirements`
- **Mục đích:** Khi FE muốn hiển thị riêng danh sách giấy tờ, mẫu biểu hoặc hộp thoại Checklist để người dân kiểm tra trước khi đi nộp.
- **Ý nghĩa trường `document_form`:**
  - `original`: Bản chính
  - `certified_copy`: Bản sao có công chứng / chứng thực
  - `copy`: Bản photo thông thường
- **Response mẫu:**
  ```json
  {
    "data": {
      "procedure_id": "e3b2c140-6b1a-4f51-8d2c-3965158a8a41",
      "procedure_code": "HKD-001",
      "procedure_name": "Đăng ký thành lập hộ kinh doanh",
      "requirements": [
        {
          "id": "e3b2c140-req-001",
          "name": "Giấy đề nghị đăng ký hộ kinh doanh",
          "description": "Bản chính Giấy đề nghị đăng ký hộ kinh doanh theo mẫu quy định",
          "is_required": true,
          "copies": 1,
          "document_form": "original",
          "form_code": "Phụ lục III-1",
          "note": "Kèm chữ ký của tất cả các thành viên tham gia hộ kinh doanh"
        }
      ]
    },
    "error": null,
    "request_id": "req-123"
  }
  ```

---

### Màn hình 5: Điều kiện & Lệ phí riêng biệt

- **Lấy điều kiện thực hiện:** `GET /api/procedures/{procedure_id}/conditions`
- **Lấy chi tiết lệ phí:** `GET /api/procedures/{procedure_id}/fees`

---

## 4. Xử lý Lỗi (Error Handling cho FE)

Backend trả về định dạng lỗi đồng nhất theo HTTP status code:

### A. Lỗi 404 - Không tìm thấy thủ tục (`PROCEDURE_NOT_FOUND`)
- Khi người dùng vào link có ID hoặc Code không tồn tại:
```json
{
  "data": null,
  "error": {
    "code": "PROCEDURE_NOT_FOUND",
    "message": "Không tìm thấy thủ tục."
  },
  "request_id": "req-123"
}
```

### B. Lỗi 422 - Dữ liệu gửi lên không hợp lệ (`VALIDATION_ERROR`)
- Ví dụ gửi query rỗng hoặc chuỗi toàn khoảng trắng lên `/api/procedures/search`:
```json
{
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Dữ liệu gửi lên không hợp lệ.",
    "details": [
      {
        "field": "query",
        "message": "String should have at least 1 character"
      }
    ]
  },
  "request_id": "req-123"
}
```

### C. Lỗi 500 - Lỗi hệ thống (`INTERNAL_ERROR`)
```json
{
  "data": null,
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau."
  },
  "request_id": "req-123"
}
```

---

## 5. Danh sách Thủ tục mẫu có sẵn trong hệ thống để FE test ngay

FE có thể dùng ngay các mã sau để test các màn hình:

1. **`HKD-001`**: Đăng ký thành lập hộ kinh doanh (Lĩnh vực: Hộ kinh doanh)
2. **`HKD-002`**: Đăng ký thay đổi nội dung đăng ký hộ kinh doanh (Lĩnh vực: Hộ kinh doanh)
3. **`HKD-003`**: Chấm dứt hoạt động hộ kinh doanh (Lĩnh vực: Hộ kinh doanh)
4. **`DD-001`**: Đăng ký biến động quyền sử dụng đất do chuyển nhượng / Sang tên sổ đỏ (Lĩnh vực: Đất đai)
5. **`DD-002`**: Cấp đổi Giấy chứng nhận quyền sử dụng đất / Đổi sổ đỏ sang mẫu mới (Lĩnh vực: Đất đai)

---

## 6. API Biểu mẫu động (Dynamic Form Schema Dispatcher)

Backend cung cấp 1 hàm trung gian (Dispatcher) kết nối tới **25 hàm xử lý chi tiết** cho 25 thủ tục hành chính (`DD-001`..`DD-012`, `HKD-001`..`HKD-002`, `DN-001`..`DN-011`).

### `GET /api/procedures/form-schema/{procedure_code}`
- **Alias:** `GET /api/procedures/{procedure_code}/form-schema`
- **Mục đích:** FE chỉ cần truyền mã thủ tục (ví dụ: `DD-001` hoặc `HKD-001`), Backend sẽ trả về cấu trúc trường nhập liệu, kiểu dữ liệu, nhãn tiếng Việt, danh sách options (nếu là select/radio), và bảng đích trong CSDL.
- **Tham số Path:** `procedure_code` (Mã thủ tục: `DD-001`, `DD-002`, `HKD-001`, `DN-001`...)
- **Response mẫu (DD-001):**
```json
{
  "data": {
    "procedure_code": "DD-001",
    "procedure_name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
    "domain": "land",
    "target_table": "land_procedure_records",
    "sections": [
      {
        "section_id": "applicant_info",
        "title": "Thông tin Người nộp hồ sơ / Người sử dụng đất",
        "description": "Thông tin cá nhân người đứng tên yêu cầu đăng ký",
        "order": 1,
        "fields": [
          {
            "name": "applicant_name",
            "label": "Họ và tên người sử dụng đất / sở hữu tài sản",
            "field_type": "text",
            "required": true,
            "placeholder": "NGUYỄN VĂN A"
          },
          {
            "name": "applicant_id_number",
            "label": "Số CCCD / Hộ chiếu",
            "field_type": "text",
            "required": true
          }
        ]
      },
      {
        "section_id": "land_plot_info",
        "title": "Thông tin Thửa đất & Tài sản gắn liền với đất",
        "order": 2,
        "fields": [
          {
            "name": "plot_number",
            "label": "Thửa đất số",
            "field_type": "text",
            "required": true
          },
          {
            "name": "map_sheet_number",
            "label": "Tờ bản đồ số",
            "field_type": "text",
            "required": true
          },
          {
            "name": "land_area",
            "label": "Diện tích đất (m²)",
            "field_type": "number",
            "required": true
          }
        ]
      }
    ]
  },
  "error": null,
  "request_id": "req-123"
}
```

---

## 7. API Lưu trữ & Tra cứu Hồ sơ Người dùng (Application Records)

Hệ thống tự động phân loại và lưu hồ sơ vào đúng 1 trong 2 bảng CSDL tương ứng:
- **Đất đai (`DD-*`):** Bảng `public.land_procedure_records`
- **Kinh doanh (`HKD-*`, `DN-*`):** Bảng `public.business_procedure_records`

### A. Lưu hồ sơ người dùng điền (`POST /api/procedures/applications`)
- **Headers:** `Content-Type: application/json`
- **Request Body mẫu (Lĩnh vực Đất đai):**
```json
{
  "procedure_code": "DD-001",
  "user_id": null,
  "status": "draft",
  "form_data": {
    "applicant_name": "Nguyễn Văn A",
    "applicant_id_number": "012345678901",
    "applicant_phone": "0901234567",
    "plot_number": "123",
    "map_sheet_number": "45",
    "land_address": "Số 10 Đường ABC, Phường 1, Quận 1, TP.HCM",
    "land_area": 120.5,
    "land_use_purpose": "Đất ở tại đô thị",
    "land_use_origin": "Nhận chuyển nhượng quyền sử dụng đất"
  },
  "extra_data": {
    "note": "Ghi chú bổ sung từ người dùng"
  }
}
```
- **Response mẫu (200 OK):**
```json
{
  "data": {
    "id": "34c3b7a2-1f03-425a-b4f4-6f2b06f9528a",
    "user_id": null,
    "procedure_code": "DD-001",
    "procedure_id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30",
    "status": "draft",
    "applicant_name": "Nguyễn Văn A",
    "created_at": "2026-10-10T13:46:26.123456Z",
    "updated_at": "2026-10-10T13:46:26.123456Z",
    "domain": "land",
    "target_table": "land_procedure_records"
  },
  "error": null,
  "request_id": "req-456"
}
```

### B. Tra cứu chi tiết hồ sơ đã lưu (`GET /api/procedures/applications/{domain}/{record_id}`)
- **Tham số Path:**
  - `domain`: `land` (đất đai) hoặc `business` (kinh doanh)
  - `record_id`: UUID của hồ sơ
- **Response mẫu (200 OK):**
```json
{
  "data": {
    "id": "34c3b7a2-1f03-425a-b4f4-6f2b06f9528a",
    "user_id": null,
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
    "form_data": {
      "applicant_name": "Nguyễn Văn A",
      "plot_number": "123",
      "land_use_purpose": "Đất ở tại đô thị"
    },
    "extra_data": {
      "note": "Ghi chú bổ sung từ người dùng"
    },
    "created_at": "2026-10-10T13:46:26.123456Z",
    "updated_at": "2026-10-10T13:46:26.123456Z",
    "domain": "land",
    "target_table": "land_procedure_records"
  },
  "error": null,
  "request_id": "req-789"
}
```

### C. Lấy danh sách hồ sơ của tôi (`GET /api/procedures/applications`) *(MỚI - ƯU TIÊN CẤP 1)*
- **Xác thực:** Yêu cầu Bearer Token (`Authorization: Bearer <jwt>`).
- **Query params:**
  - `domain`: `land` hoặc `business` (tùy chọn)
  - `status`: `draft` hoặc `submitted` (tùy chọn)
  - `page`: Số trang (mặc định 1)
  - `page_size`: Số bản ghi mỗi trang (mặc định 20)
  - `search`: Từ khóa tìm kiếm (tùy chọn)
- **Response mẫu (200 OK):**
```json
{
  "data": {
    "items": [
      {
        "id": "34c3b7a2-1f03-425a-b4f4-6f2b06f9528a",
        "user_id": "11111111-1111-1111-1111-111111111111",
        "procedure_code": "DD-001",
        "status": "draft",
        "applicant_name": "Nguyễn Văn A",
        "plot_number": "123",
        "land_address": "Số 10 Đường ABC, Quận 1, TP.HCM",
        "land_area": 120.5,
        "form_data": { ... },
        "domain": "land",
        "created_at": "2026-10-14T08:00:00Z",
        "updated_at": "2026-10-14T08:30:00Z"
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
  "request_id": "req-abc"
}
```

### D. Cập nhật hồ sơ bản nháp (`PUT /api/procedures/applications/{domain}/{record_id}`) *(MỚI - ƯU TIÊN CẤP 1)*
- **Xác thực:** Yêu cầu Bearer Token.
- **Quy tắc:** Chỉ chủ sở hữu mới có quyền sửa, và chỉ được sửa khi hồ sơ ở trạng thái `draft`.
- **Request Body (JSON):**
```json
{
  "form_data": {
    "applicant_name": "Nguyễn Văn A (Cập nhật)",
    "applicant_phone": "0988999888",
    "land_area": 150.0
  },
  "extra_data": {
    "note": "Cập nhật lại diện tích"
  },
  "status": "draft"
}
```
- **Response mẫu (200 OK):** Trả về bản ghi sau khi cập nhật thành công.

### E. Xóa hồ sơ bản nháp (`DELETE /api/procedures/applications/{domain}/{record_id}`) *(MỚI - ƯU TIÊN CẤP 1)*
- **Xác thực:** Yêu cầu Bearer Token.
- **Quy tắc:** Chỉ chủ sở hữu mới có quyền xóa, và chỉ được xóa hồ sơ ở trạng thái `draft`.
- **Response mẫu (200 OK):**
```json
{
  "data": {
    "success": true,
    "message": "Đã xóa bản nháp hồ sơ thành công"
  },
  "error": null,
  "request_id": "req-del"
}
```

### F. Nộp chính thức hồ sơ (`POST /api/procedures/applications/{domain}/{record_id}/submit`) *(MỚI - ƯU TIÊN CẤP 1)*
- **Xác thực:** Yêu cầu Bearer Token.
- **Quy tắc:**
  - Kiểm tra quyền sở hữu của user.
  - Tự động kiểm tra tính đầy đủ của các trường bắt buộc (`required: true`) theo Schema thủ tục.
  - Trả về `400 VALIDATION_ERROR` nếu thiếu trường; nếu đầy đủ, chuyển `status` sang `submitted` và khóa hồ sơ.
- **Response mẫu (200 OK):**
```json
{
  "data": {
    "id": "34c3b7a2-1f03-425a-b4f4-6f2b06f9528a",
    "procedure_code": "DD-001",
    "status": "submitted",
    "updated_at": "2026-10-14T09:00:00Z"
  },
  "error": null,
  "request_id": "req-sub"
}
```


