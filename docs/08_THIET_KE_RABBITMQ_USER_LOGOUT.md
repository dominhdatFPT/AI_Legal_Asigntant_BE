# THIẾT KẾ RABBITMQ CHO TÍNH NĂNG ĐĂNG XUẤT (ASYNCHRONOUS SECURE LOGOUT)

> **Cập nhật 19/10/2026:** đây là tài liệu thiết kế ban đầu. Bản cài đặt hiện tại khác ở hai điểm: (1) thu hồi token do mọi service tự làm qua `common_auth.revocation` (không còn queue `q.auth.token_blacklist`, không dùng Redis); (2) các worker dọn dẹp consultation/notification giả lập đã bị gỡ, chỉ còn worker audit `q.audit.user_logout`. Xem `09_TONG_HOP_TOAN_BO_RABBITMQ_HE_THONG.md`.

Tài liệu này cung cấp bản thiết kế kỹ thuật hoàn chỉnh cho tính năng **Đăng xuất an toàn bất đồng bộ (Asynchronous Secure Logout)** kết hợp giữa **RabbitMQ**, **Redis Blacklist** và cơ chế **Xóa Cookie / Thu hồi Token**.

---

## 1. NGUYÊN LÝ HOẠT ĐỘNG: TẠI SAO CÁCH NÀY NHANH HƠN CÁCH CŨ?

### Cách làm cũ (Đồng bộ - Synchronous Blocking):
```
[User bấm Logout] ---> [User Service]
                             |-- (Chờ) Thu hồi token trong DB (50ms)
                             |-- (Chờ) Gọi HTTP sang Consultation Service ngắt socket (200ms)
                             |-- (Chờ) Gọi HTTP sang Notification Service xóa session (150ms)
                             |-- (Chờ) Ghi log an ninh (50ms)
                             v
               [Tổng thời gian: 450ms - 1.5s] ---> Màn hình user bị xoay vòng quay (loading spinner)
```
- **Nhược điểm:** Nếu một service (như Consultation) bị lag hoặc timeout, request đăng xuất bị treo, user bấm nút Logout nhưng không thoát được ra màn hình ngoài.

---

### Cách làm mới với RabbitMQ (Bất đồng bộ - Optimistic & Event-Driven):
```
[User bấm Logout] ---> [User Service]
                             |-- 1. Thu hồi Refresh Token trong DB
                             |-- 2. Bắn Message vào RabbitMQ (mất ~2ms)
                             |-- 3. Set-Cookie: Max-Age=0 (Xóa sạch Cookie)
                             v
               [Trả kết quả ngay sau 15ms]
                             |
                             v
[Client / Trình duyệt]: Nhận header xóa cookie, chuyển ngay sang trang /login (Tức thì, 0s delay!)

----------------- DƯỚI NỀN (BACKGROUND CONSUMERS QUA RABBITMQ) -----------------
[RabbitMQ Exchange]
        |
        +---> [Queue q.auth.blacklist] -----> Lưu JWT vào Redis Blacklist
        +---> [Queue q.consultation.logout] -> Đóng WebSocket, chuyển trạng thái Offline
        +---> [Queue q.notification.logout] -> Giải phóng kết nối Realtime Push
        +---> [Queue q.audit.logout] --------> Ghi nhật ký bảo mật
```

> **Câu trả lời cho thắc mắc của bạn:**
> **ĐÚNG HOÀN TOÀN!** 
> Phía trình duyệt chỉ cần nhận lệnh xóa Cookie/Token là coi như **đã thoát ngay lập tức (Instant Logout)**. Người dùng không phải chờ đợi Backend dọn dẹp phòng chat, ngắt socket hay ghi log. Toàn bộ các việc nặng đó do các Worker của RabbitMQ làm ngầm phía sau mà không ảnh hưởng gì đến trải nghiệm người dùng.

---

## 2. BẢO MẬT 3 LỚP: ĐẢM BẢO TOKEN BỊ VÔ HIỆU HÓA HOÀN TOÀN

Vì Access Token trong hệ thống dùng **JWT bất đối xứng (RS256) - Stateless**, nếu kẻ gian đã sao chép được token trước khi người dùng bấm đăng xuất, token đó về mặt toán học vẫn hợp lệ cho đến khi hết hạn (TTL 15-30 phút). 

Để giải quyết triệt để lỗ hổng này, chúng ta áp dụng **Chiến lược bảo mật 3 lớp**:

```
+---------------------------------------------------------------------------------+
|                       3 LỚP BẢO MẬT KHI ĐĂNG XUẤT                              |
+---------------------------------------------------------------------------------+
| LỚP 1: XÓA COOKIE TRÊN MÁY USER (Client-side Cookie Invalidation)               |
| - Header Set-Cookie: Max-Age=0, Expires=Thu, 01 Jan 1970 00:00:00 GMT           |
| - Trình duyệt lập tức xóa sạch cookie access_token và refresh_token.            |
+---------------------------------------------------------------------------------+
| LỚP 2: THU HỒI REFRESH TOKEN TRONG CSDL (PostgreSQL Revocation)                 |
| - Đánh dấu revoked_at = NOW() trong bảng refresh_tokens.                         |
| - Kẻ gian vĩnh viễn không thể dùng Refresh Token này để xin cấp Access Token mới.|
+---------------------------------------------------------------------------------+
| LỚP 3: ACCESS TOKEN BLACKLIST TRÊN REDIS (Tự hủy theo thời gian sống TTL)       |
| - Worker lưu jti (Token ID) hoặc token signature vào Redis dạng Key-Value.      |
| - Redis đặt TTL đúng bằng thời gian còn lại của Token (ví dụ: còn 14 phút).     |
| - Middleware Gateway / Service kiểm tra Redis (~0.3ms). Nằm trong Blacklist: 401!|
| - Sau 14 phút, Redis tự động giải phóng RAM mà không cần dọn dẹp thủ công.      |
+---------------------------------------------------------------------------------+
```

---

## 3. THIẾT KẾ RABBITMQ CHO LOGOUT

### 1. Phân loại Exchange & Queues
- **Exchange:** `legal.events.topic` (Loại: `topic`, Durable: `true`)
- **Routing Key:** `user.logged_out`
- **Các Queues tiêu thụ (Consumers):**

| Tên Hàng Đợi (Queue) | Consumer Service | Nhiệm vụ xử lý khi nhận sự kiện Logout |
|---|---|---|
| `q.auth.token_blacklist` | `user-service` / `gateway` | Ghi Access Token vào Redis Blacklist để chặn truy cập tức thì. |
| `q.consultation.logout` | `consultation-service` | Cắt đứt kết nối WebSocket phòng chat; nếu là Luật sư -> Đổi trạng thái sang Offline. |
| `q.notification.logout` | `notification-service` | Hủy kết nối SSE/WebSocket chuông thông báo, xóa Connection ID trong bộ nhớ RAM. |
| `q.audit.security_logs` | `audit-worker` | Ghi log đăng xuất (IP, thời gian, thiết bị) để phục vụ kiểm toán an ninh. |

---

### 2. Cấu trúc Message Payload (`user.logged_out`)
```json
{
  "event_id": "a1b2c3d4-e5f6-4789-a012-b3c4d5e6f7a8",
  "event_type": "user.logged_out",
  "occurred_at": "2026-10-19T11:15:00Z",
  "user_id": "11111111-1111-4111-8111-111111111111",
  "role": "citizen",
  "token_info": {
    "jti": "jwt-unique-id-987654",
    "expires_at": 1729338000,
    "remaining_seconds": 840
  },
  "device_info": {
    "ip_address": "14.162.180.22",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)..."
  },
  "scope": "single_device"
}
```

---

## 4. CODE MINH HỌA CHI TIẾT TỪNG BƯỚC

### Bước 1: Viết API Đăng xuất tại User Service ([`app/api/auth.py`](file:///D:/Legal%20Assistant%20AI/BE/services/user-service/app/api/auth.py))

```python
import datetime
import json
import uuid
import aio_pika
from fastapi import APIRouter, Depends, Request, Response
from common_auth import CurrentUser
from app.api.deps import get_current_user, get_db
from app.core import cookies, security
from app.services import auth_service

router = APIRouter()

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current: CurrentUser = Depends(get_current_user),
    db = Depends(get_db)
):
    # 1. THU HỒI REFRESH TOKEN TRONG CSDL (Lớp bảo mật 2)
    raw_refresh = cookies.refresh_token_from(request, None)
    if raw_refresh:
        auth_service.logout(db, raw_refresh, str(current.id))

    # 2. BẮN MESSAGE SANG RABBITMQ (Thực hiện bất đồng bộ)
    token_claims = getattr(request.state, "token_claims", {})
    exp_timestamp = token_claims.get("exp", 0)
    now_timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    remaining_ttl = max(0, exp_timestamp - now_timestamp)

    logout_event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "user.logged_out",
        "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user_id": str(current.id),
        "role": current.role,
        "token_info": {
            "jti": token_claims.get("jti", str(uuid.uuid4())),
            "token_signature": security.get_token_signature(request),
            "remaining_seconds": remaining_ttl
        },
        "device_info": {
            "ip_address": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent", "")
        }
    }

    # Bắn vào RabbitMQ Topic Exchange (Chỉ mất 1-2ms)
    await publish_to_rabbitmq(logout_event, routing_key="user.logged_out")

    # 3. XÓA COOKIE TRÊN MÁY USER (Lớp bảo mật 1)
    cookies.clear_auth_cookies(response)
    
    return {"data": {"logged_out": True}, "error": None}
```

---

### Bước 2: Worker lắng nghe và ghi JWT vào Redis Blacklist (Lớp bảo mật 3)

Tạo file Consumer chuyên xử lý Blacklist:
```python
import asyncio
import json
import aio_pika
import redis.asyncio as aioredis

redis_client = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)

async def handle_token_blacklist(message: aio_pika.IncomingMessage):
    async with message.process():
        event = json.loads(message.body.decode())
        token_info = event.get("token_info", {})
        token_key = f"blacklist:{token_info.get('jti') or token_info.get('token_signature')}"
        ttl = token_info.get("remaining_seconds", 900)

        if ttl > 0:
            # Lưu vào Redis với TTL: Sau khi token hết hạn tự nhiên, Redis tự xóa key
            await redis_client.setex(token_key, ttl, "revoked")
            print(f"[Blacklist] Đã khóa Access Token của user {event['user_id']} trong {ttl}s")

async def start_blacklist_worker():
    connection = await aio_pika.connect_robust("amqp://legal_admin:legal_secret_pass@rabbitmq:5672//legal_vhost")
    channel = await connection.channel()
    
    exchange = await channel.declare_exchange("legal.events.topic", aio_pika.ExchangeType.TOPIC, durable=True)
    queue = await channel.declare_queue("q.auth.token_blacklist", durable=True)
    await queue.bind(exchange, routing_key="user.logged_out")

    print("[*] Worker Blacklist sẵn sàng nhận lệnh khóa token...")
    await queue.consume(handle_token_blacklist)
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(start_blacklist_worker())
```

---

### Bước 3: Middleware kiểm tra Blacklist khi User gửi request tiếp theo

Tại các microservice hoặc API Gateway, middleware xác thực chỉ cần thêm 2 dòng kiểm tra Redis:

```python
async def verify_token_not_blacklisted(jti: str) -> bool:
    # Truy vấn in-memory Redis cực nhanh (~0.3ms)
    is_revoked = await redis_client.exists(f"blacklist:{jti}")
    return not bool(is_revoked)

# Trong dependency auth:
if not await verify_token_not_blacklisted(token_jti):
    raise HTTPException(status_code=401, detail="Token đã bị vô hiệu hóa do người dùng đã đăng xuất.")
```

---

## 5. TỔNG KẾT QUY TRÌNH KHI USER BẤM ĐĂNG XUẤT

| Phía | Hành động diễn ra | Thời gian | Kết quả |
|---|---|:---:|---|
| **Máy User (Frontend)** | Xóa token trong RAM, nhận header `Set-Cookie` xóa cookie, chuyển trang sang `/login`. | **~15ms** | Người dùng thấy thoát ra ngay lập tức, cực kỳ mượt mà. |
| **User Service** | Đánh dấu thu hồi Refresh Token trong DB, gửi message vào RabbitMQ. | **~5ms** | Hoàn thành trách nhiệm bảo mật cơ bản. |
| **RabbitMQ & Workers** | Đưa Access Token vào Redis Blacklist, ngắt WebSocket phòng chat, chuyển trạng thái Luật sư thành Offline, ghi audit log. | **Xử lý ngầm** | Diễn ra độc lập, an toàn tuyệt đối, không làm nghẽn ứng dụng. |
