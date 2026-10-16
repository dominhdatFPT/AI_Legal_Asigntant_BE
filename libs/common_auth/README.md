# common_auth

Kiểm tra access token JWT (RS256) dùng chung cho mọi service.
User Service ký token bằng **private key**; các service khác chỉ cần **public key** để kiểm tra.

## Gắn vào một service

1. Trong `app/main.py`, đăng ký handler lỗi một lần:

   ```python
   from common_auth.fastapi_dep import add_auth_error_handler

   app = FastAPI(...)
   add_auth_error_handler(app)
   ```

2. Bảo vệ API:

   ```python
   from fastapi import Depends
   from common_auth.fastapi_dep import get_current_user, require_role

   @app.get("/api/v1/documents")
   def list_documents(user=Depends(get_current_user)):      # ai đăng nhập cũng gọi được
       return get_documents(owner_id=user.id)

   @app.post("/api/v1/consultations/{id}/accept")
   def accept(id: str, user=Depends(require_role("lawyer"))):  # chỉ luật sư
       ...
   ```

   `user.id` là `sub` trong token, `user.role` là `citizen` | `lawyer` | `admin`.
   Không tin `user_id` do frontend tự gửi; chủ sở hữu luôn lấy từ `user.id`.

## Lỗi trả về

| HTTP | `error.code` | Khi nào |
|---|---|---|
| 401 | `UNAUTHORIZED` | Thiếu token, token sai, sai chữ ký, không phải access token |
| 401 | `TOKEN_EXPIRED` | Token hết hạn, FE gọi `/users/auth/refresh` rồi thử lại |
| 403 | `FORBIDDEN` | Đúng token nhưng sai vai trò |

Body theo format chung: `{"data": null, "error": {"code", "message"}, "request_id"}`.

## Cấu hình

| Biến | Dùng khi |
|---|---|
| `JWT_PUBLIC_KEY_PATH` | Đường dẫn tới `jwt_public.pem` |
| `JWT_PUBLIC_KEY` | Nội dung key (một dòng, xuống dòng viết `\n`); ưu tiên hơn `_PATH` |

- **Docker:** đặt `JWT_KEYS_DIR` trong `.env` ở gốc repo là thư mục chứa 2 file `.pem`. `docker-compose.yml` đã gắn thư mục này vào `/run/keys` cho mọi service.
- **Chạy local:** đặt `JWT_PUBLIC_KEY_PATH` trong `.env`, chạy service bằng
  `python -m uvicorn app.main:app --reload --port 800x --env-file ../../.env`.
- Không commit file `.pem`. Xin cặp key dev của nhóm từ người phụ trách User Service.

## Cài đặt

```bash
python -m pip install -e libs/common_auth           # chạy local
python -m pip install -r requirements-test.txt      # đã gồm common_auth, dùng cho test
```

Docker tự cài qua `additional_contexts` trong `docker-compose.yml` (cần Docker Compose 2.17 trở lên).

## Tạo token để test bằng Postman

Khi chưa có đăng nhập Google:

```bash
python scripts/mint_dev_token.py --role lawyer
```

Dán kết quả vào Postman: Authorization → Bearer Token. Script đọc `JWT_PRIVATE_KEY_PATH` trong `.env`. Chỉ dùng ở dev.

## Chạy test

```bash
python -m pytest libs/common_auth -q
```
