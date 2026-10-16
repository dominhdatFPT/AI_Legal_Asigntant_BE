"""Mẫu HTTP adapter. Caller đọc event từ outbox và chỉ đánh dấu sent khi hàm trả về."""
import json
import os
from pathlib import Path
import time

import httpx


class NotificationDeliveryError(RuntimeError):
    """retryable=True: giữ event pending; False: đánh dấu failed để kiểm tra."""

    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def send_notification_event(event: dict, request_id: str, *, client=None, wait=time.sleep) -> dict:
    base_url = os.environ.get("NOTIFICATION_SERVICE_URL", "").rstrip("/")
    token = os.environ.get("INTERNAL_TOKEN", "")
    if not base_url or not token:
        raise NotificationDeliveryError("MISSING_NOTIFICATION_CONFIG", retryable=False)

    # Không sinh lại event_id hoặc occurred_at tại đây: event phải được lưu trước.
    event_id = event.get("event_id")
    if not event_id:
        raise NotificationDeliveryError("MISSING_EVENT_ID", retryable=False)
    payload = json.loads(json.dumps(event))  # Snapshot không bị caller đổi trong lúc retry.
    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": token,
        "X-Request-ID": request_id,
    }
    owns_client = client is None
    instance = client or httpx.Client(timeout=5.0, trust_env=False)
    last_code = "NOTIFICATION_UNAVAILABLE"
    try:
        for attempt in range(3):
            try:
                response = instance.post(
                    base_url + "/internal/v1/notifications/events",
                    json=payload,
                    headers=headers,
                    timeout=5.0,
                )
            except httpx.TransportError:
                last_code = "NOTIFICATION_UNAVAILABLE"
            else:
                if response.status_code == 200:
                    try:
                        result = response.json()
                        data = result["data"]
                        valid = (
                            result.get("error") is None
                            and isinstance(result.get("request_id"), str)
                            and data["event_id"] == event_id
                            and isinstance(data["is_duplicate"], bool)
                            and type(data["created_count"]) is int
                            and data["created_count"] >= 0
                        )
                    except (ValueError, KeyError, TypeError):
                        valid = False
                    if valid:
                        return result
                    # Không đánh dấu sent nếu response không khớp contract.
                    last_code = "NOTIFICATION_BAD_RESPONSE"
                elif response.status_code == 429 or response.status_code >= 500:
                    last_code = "NOTIFICATION_HTTP_" + str(response.status_code)
                else:
                    # 4xx không tự retry, đặc biệt conflict event_id/token sai.
                    raise NotificationDeliveryError(
                        "NOTIFICATION_HTTP_" + str(response.status_code), retryable=False
                    )
            if attempt < 2:
                wait((0.5, 1.0)[attempt])
        raise NotificationDeliveryError(last_code, retryable=True)
    finally:
        if owns_client:
            instance.close()


if __name__ == "__main__":
    event_path = Path(__file__).resolve().parents[1] / "notification-event.example.json"
    stored_event = json.loads(event_path.read_text(encoding="utf-8"))
    result = send_notification_event(stored_event, request_id="req-demo-001")
    print(json.dumps(result, ensure_ascii=False, indent=2))
