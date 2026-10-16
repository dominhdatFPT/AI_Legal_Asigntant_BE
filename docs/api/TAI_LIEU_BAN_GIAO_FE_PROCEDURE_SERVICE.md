# TÀI LIỆU BÀN GIAO TÍCH HỢP API PROCEDURE SERVICE (DÀNH CHO FRONTEND)

Tài liệu kỹ thuật tổng hợp toàn bộ các API Backend của **Procedure Service** được thiết kế để đội ngũ Frontend (FE) tích hợp xây dựng giao diện hiển thị danh mục, tìm kiếm, render biểu mẫu động và lưu trữ hồ sơ người dùng.

---

## 1. Thông tin kết nối & Quy ước hệ thống

- **Base URL qua API Gateway (FE gọi chính thức):** `http://localhost:8080/api/procedures`
- **Base URL Service độc lập (Local test BE):** `http://localhost:8000/api/v1/procedures`
- **Xác thực (Authentication):** Công khai (Không bắt buộc token, hỗ trợ khách vãng lai và người dân nộp đơn).
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
  - **Lỗi (HTTP 400, 404, 422, 500):**
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

| STT | Method | Endpoint qua Gateway (FE gọi) | Endpoint trực tiếp Backend | Chức năng trên Giao diện Frontend |
|:---:|:---:|---|---|---|
| **1** | `GET` | `/api/procedures/health` | `/api/v1/procedures/health` | **Kiểm tra trạng thái Service & Database** (cho Header/Status bar) |
| **2** | `GET` | `/api/procedures/domains` | `/api/v1/procedures/domains` | **Lấy danh sách Lĩnh vực** (cho Tabs / Filter: Tất cả, Kinh doanh, Đất đai) |
| **3** | `GET` | `/api/procedures/domains/{domain}` | `/api/v1/procedures/domains/{domain}` | **Danh sách chi tiết thủ tục theo lĩnh vực** (`land` / `business`) |
| **4** | `GET` | `/api/procedures/domains/{domain}/names` | `/api/v1/procedures/domains/{domain}/names` | **Tên & mã thủ tục rút gọn** (cho Select box / Dropdown menu) |
| **5** | `GET` | `/api/procedures` | `/api/v1/procedures` | **Danh sách thủ tục phân trang** (kèm lọc lĩnh vực và tìm kiếm nhanh `q`) |
| **6** | `POST` | `/api/procedures/search` | `/api/v1/procedures/search` | **Tìm kiếm thông minh** (hỗ trợ câu hỏi tự nhiên, từ khóa có dấu/không dấu) |
| **7** | `GET` | `/api/procedures/{id_hoặc_code}` | `/api/v1/procedures/{id_hoặc_code}` | **Chi tiết thủ tục** (nhận UUID hoặc mã: `DD-001`, `HKD-001`...) |
| **8** | `GET` | `/api/procedures/{id_hoặc_code}/requirements` | `/api/v1/procedures/{id_hoặc_code}/requirements` | **Checklist hồ sơ giấy tờ cần chuẩn bị** |
| **9** | `GET` | `/api/procedures/{id_hoặc_code}/conditions` | `/api/v1/procedures/{id_hoặc_code}/conditions` | **Danh sách điều kiện thực hiện thủ tục** |
| **10** | `GET` | `/api/procedures/{id_hoặc_code}/fees` | `/api/v1/procedures/{id_hoặc_code}/fees` | **Bảng phí, lệ phí chi tiết** |
| **11** | `GET` | `/api/procedures/form-schema/{procedure_code}` | `/api/v1/procedures/form-schema/{procedure_code}` | **HÀM TRUNG GIAN (DISPATCHER): Lấy Form Schema động của 25 thủ tục** |
| **12** | `POST` | `/api/procedures/applications` | `/api/v1/procedures/applications` | **Lưu hồ sơ người dùng điền** (tự động phân loại bảng Đất đai / Kinh doanh) |
| **13** | `GET` | `/api/procedures/applications/{domain}/{record_id}` | `/api/v1/procedures/applications/{domain}/{record_id}` | **Tra cứu lại hồ sơ đã lưu** (để render lại dữ liệu vào form) |

---

## 3. Danh sách 25 Thủ tục chi tiết & Dispatcher Form

Backend tích hợp sẵn **1 Hàm điều phối trung gian** kết nối đến **25 hàm chuyên biệt** tương ứng với 25 thủ tục chi tiết:

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
- **Response mẫu:**
  ```json
  {
    "status": "healthy",
    "service": "procedure-service",
    "version": "0.1.0",
    "database": {
      "status": "connected",
      "connected": true,
      "message": "Kết nối PostgreSQL qua PROCEDURE_DATABASE_URL hoạt động tốt."
    },
    "request_id": "req-3fa85f64-5717-4562-b3fc-2c963f66afa6"
  }
  ```

---

### 4.2. `GET /api/procedures/domains`
Lấy danh sách các lĩnh vực hỗ trợ kèm số lượng thủ tục thực tế (cho Tab / Filter trên UI).
- **Response mẫu:**
  ```json
  {
    "data": [
      {
        "code": "business",
        "name": "Doanh nghiệp & Hộ kinh doanh",
        "description": "Thành lập, thay đổi nội dung, giải thể doanh nghiệp và hộ kinh doanh...",
        "icon": "storefront",
        "count": 13
      },
      {
        "code": "land",
        "name": "Đất đai & Bất động sản",
        "description": "Đăng ký biến động, cấp đổi, tách thửa, thế chấp, sang tên sổ đỏ...",
        "icon": "landscape",
        "count": 12
      }
    ],
    "total_procedures": 25,
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```

---

### 4.3. `GET /api/procedures/domains/{domain}/names`
Lấy danh sách tên & mã thủ tục rút gọn theo lĩnh vực (cho Dropdown menu / Select box).
- **Tham số:** `domain` (`land` hoặc `business`)
- **Response mẫu:**
  ```json
  {
    "data": {
      "domain": "land",
      "total": 12,
      "items": [
        { "id": "d5d9c792-5b9e-4e42-9a3b-2856247c7c30", "code": "DD-001", "name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng" },
        { "id": "b6c5d470-9e4d-7a84-1a5f-6298481d1d74", "code": "DD-002", "name": "Cấp đổi Giấy chứng nhận quyền sử dụng đất..." }
      ]
    },
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```

---

### 4.4. `GET /api/procedures/form-schema/{procedure_code}`
**HÀM TRUNG GIAN LẤY BIỂU MẪU ĐỘNG:** FE chỉ cần truyền mã thủ tục, BE điều phối tới 1 trong 25 hàm chi tiết trả về schema biểu mẫu hoàn chỉnh.
- **Ví dụ gọi:** `GET /api/procedures/form-schema/DD-001` hoặc `GET /api/procedures/form-schema/HKD-001`
- **Response mẫu (`DD-001` - Đăng ký biến động đất đai):**
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
              "required": true,
              "placeholder": "12 chữ số CCCD"
            },
            {
              "name": "applicant_phone",
              "label": "Số điện thoại liên hệ",
              "field_type": "text",
              "required": true
            }
          ]
        },
        {
          "section_id": "land_plot_info",
          "title": "Thông tin Thửa đất & Tài sản gắn liền với đất",
          "description": "Dữ liệu thể hiện trên Giấy chứng nhận hiện tại",
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
              "name": "land_address",
              "label": "Địa chỉ thửa đất",
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
        },
        {
          "section_id": "procedure_specific",
          "title": "Nội dung biến động đề nghị đăng ký",
          "order": 3,
          "fields": [
            {
              "name": "transfer_contract_number",
              "label": "Số hợp đồng chuyển nhượng công chứng",
              "field_type": "text",
              "required": false
            },
            {
              "name": "transfer_date",
              "label": "Ngày công chứng hợp đồng",
              "field_type": "date",
              "required": false
            },
            {
              "name": "change_content_description",
              "label": "Nội dung biến động chi tiết",
              "field_type": "textarea",
              "required": false
            }
          ]
        }
      ]
    },
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```

---

### 4.5. `POST /api/procedures/applications`
**LƯU HỒ SƠ NGƯỜI DÙNG ĐIỀN VÀO CSDL:**
Hệ thống tự động nhận diện mã thủ tục:
- Nếu bắt đầu bằng `DD-*` -> Lưu vào bảng `public.land_procedure_records`
- Nếu bắt đầu bằng `HKD-*` hoặc `DN-*` -> Lưu vào bảng `public.business_procedure_records`

#### Request Body mẫu (Lĩnh vực Đất đai):
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
    "land_use_origin": "Nhận chuyển nhượng quyền sử dụng đất",
    "transfer_contract_number": "CC-12345/2026",
    "transfer_date": "2026-10-01"
  },
  "extra_data": {
    "note": "Nộp kèm 01 bản chính Sổ đỏ và hợp đồng công chứng"
  }
}
```

#### Request Body mẫu (Lĩnh vực Kinh doanh):
```json
{
  "procedure_code": "HKD-001",
  "user_id": null,
  "status": "draft",
  "form_data": {
    "applicant_name": "Trần Thị B",
    "applicant_id_number": "098765432109",
    "applicant_phone": "0912345678",
    "business_name_vn": "HỘ KINH DOANH BÌNH MINH",
    "headquarters_address": "Số 50 Đường XYZ, Phường 2, Quận 3, TP.HCM",
    "charter_capital": 100000000,
    "business_lines": "Bán lẻ hàng tạp phẩm, nước giải khát",
    "household_head_name": "Trần Thị B"
  },
  "extra_data": {
    "note": "Địa điểm kinh doanh thuê 5 năm"
  }
}
```

#### Response mẫu (HTTP 200 OK):
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
  "request_id": "req-3fa85f64"
}
```

---

### 4.6. `GET /api/procedures/applications/{domain}/{record_id}`
**TRA CỨU HỒ SƠ ĐÃ LƯU:** Lấy lại dữ liệu người dùng đã nộp/lưu nháp để hiển thị ngược lại vào các trường form trên giao diện.
- **Tham số Path:**
  - `domain`: `land` (đất đai) hoặc `business` (kinh doanh)
  - `record_id`: UUID của hồ sơ (nhận từ API lưu ở trên).
- **Ví dụ gọi:** `GET /api/procedures/applications/land/34c3b7a2-1f03-425a-b4f4-6f2b06f9528a`
- **Response mẫu (HTTP 200 OK):**
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
        "map_sheet_number": "45",
        "land_use_purpose": "Đất ở tại đô thị",
        "transfer_contract_number": "CC-12345/2026"
      },
      "extra_data": {
        "note": "Nộp kèm 01 bản chính Sổ đỏ và hợp đồng công chứng"
      },
      "created_at": "2026-10-10T13:46:26.123456Z",
      "updated_at": "2026-10-10T13:46:26.123456Z",
      "domain": "land",
      "target_table": "land_procedure_records"
    },
    "error": null,
    "request_id": "req-3fa85f64"
  }
  ```

---

## 5. Hướng dẫn Frontend Render Biểu mẫu động (Dynamic Form)

Khi FE gọi `GET /form-schema/{procedure_code}`, hãy lặp qua mảng `sections`, trong mỗi section lặp qua mảng `fields` và render Component tương ứng theo `field_type`:

| `field_type` | Component trên giao diện React / Vue | Ví dụ thuộc tính |
|---|---|---|
| `text` | `<input type="text" />` hoặc `<Input />` | `placeholder`, `maxLength` |
| `number` | `<input type="number" />` hoặc `<InputNumber />` | `min`, `max`, `step` |
| `date` | `<input type="date" />` hoặc `<DatePicker />` | `format="YYYY-MM-DD"` |
| `textarea` | `<textarea rows={4} />` hoặc `<Textarea />` | Dùng cho nội dung dài |
| `select` | `<select>` hoặc `<Select options={field.options} />` | Chọn 1 từ danh sách cố định |
| `radio` | `<RadioGroup options={field.options} />` | Chọn 1 trong số ít lựa chọn |
| `checkbox` | `<Checkbox />` | Đồng ý cam kết hoặc chọn nhiều |

Khi người dùng ấn **"Lưu nháp"** hoặc **"Nộp hồ sơ"**, gom toàn bộ giá trị của các ô nhập vào đối tượng `form_data: { [field.name]: value }` và gửi lên `POST /api/procedures/applications`.

---

## 6. Code mẫu TypeScript / Axios cho Frontend sử dụng ngay

```typescript
import axios from 'axios';

// Cấu hình Axios Client gọi qua Gateway
const apiClient = axios.create({
  baseURL: 'http://localhost:8080/api/procedures',
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 10000,
});

// Interceptor bọc unwrap data tiện dụng
apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const errData = error.response?.data?.error || {
      code: 'NETWORK_ERROR',
      message: 'Không thể kết nối đến máy chủ',
    };
    return Promise.reject(errData);
  }
);

// 1. Kiểm tra trạng thái service
export const checkHealth = async () => {
  return apiClient.get('/health');
};

// 2. Lấy danh sách Lĩnh vực (Tabs: business, land)
export const getDomains = async () => {
  const res: any = await apiClient.get('/domains');
  return res.data;
};

// 3. Lấy tên rút gọn thủ tục theo lĩnh vực (cho Dropdown)
export const getProcedureNames = async (domain: 'land' | 'business') => {
  const res: any = await apiClient.get(`/domains/${domain}/names`);
  return res.data;
};

// 4. Lấy chi tiết thủ tục (căn cứ pháp lý, checklist giấy tờ, lệ phí)
export const getProcedureDetail = async (procedureIdOrCode: string) => {
  const res: any = await apiClient.get(`/${procedureIdOrCode}`);
  return res.data;
};

// 5. HÀM TRUNG GIAN: Lấy Form Schema động của thủ tục (DD-001, HKD-001...)
export const getProcedureFormSchema = async (procedureCode: string) => {
  const res: any = await apiClient.get(`/form-schema/${procedureCode}`);
  return res.data;
};

// 6. Lưu / Nộp hồ sơ người dùng đã điền
export interface SubmitApplicationPayload {
  procedure_code: string;
  user_id?: string | null;
  status?: 'draft' | 'submitted';
  form_data: Record<string, any>;
  extra_data?: Record<string, any>;
}

export const submitApplication = async (payload: SubmitApplicationPayload) => {
  const res: any = await apiClient.post('/applications', payload);
  return res.data;
};

// 7. Lấy lại hồ sơ đã lưu để hiển thị lại lên màn hình
export const getSavedApplication = async (domain: 'land' | 'business', recordId: string) => {
  const res: any = await apiClient.get(`/applications/${domain}/${recordId}`);
  return res.data;
};
```
