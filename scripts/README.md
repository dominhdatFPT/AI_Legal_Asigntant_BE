# Scripts

Các lệnh hỗ trợ developer, không phải code nghiệp vụ service.

- `mint_dev_token.py`: ký access token để test dev bằng private key local. Không gửi token/key vào Git.
- `check_contract_notification_jwt.py`: 24 bài kiểm tra HTTP đọc-only trên hai container local; tự ký token dev trong RAM và chỉ in kết quả, không in token/key.

Thêm `--gateway-url http://localhost:8080` để kiểm tra cùng các API qua Gateway thay vì cổng trực tiếp 8006/8009.

Chạy từ gốc backend sau khi cài `requirements-test.txt`. Chi tiết cấu hình Docker, public key và lệnh chạy: [hướng dẫn JWT](../docs/api/JWT_DOCKER_CONTRACT_NOTIFICATION.md).
