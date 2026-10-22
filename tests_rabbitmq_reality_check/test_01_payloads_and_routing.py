"""Test 01: Verify all event types, routing keys, realistic payloads, and abnormal/adversarial inputs."""
import asyncio
import json
import logging
import sys
import time
import uuid

import aio_pika
import config

from common_auth import events

user_rabbitmq = config.load_service_module("user-service", "app.core.rabbitmq")
admin_mq_mod = config.load_service_module("user-service", "app.core.rabbitmq_admin")
admin_mq = admin_mq_mod.admin_mq

proc_mq_mod = config.load_service_module("procedure-service", "app.core.rabbitmq_procedure")
procedure_mq = proc_mq_mod.procedure_mq

lawyer_mq_mod = config.load_service_module("lawyer-service", "app.core.rabbitmq_lawyer")
lawyer_mq = lawyer_mq_mod.lawyer_mq

notif_hub_mod = config.load_service_module("notification-service", "app.core.rabbitmq_notification_hub")
format_notification = notif_hub_mod.format_notification
sanitize_action_url = notif_hub_mod.sanitize_action_url

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Test01")


async def run_test():
    url = config.RABBITMQ_URL
    print("=" * 70)
    print("TEST 01: PAYLOADS, ROUTING KEYS & ABNORMAL/ADVERSARIAL INPUT TEST")
    print(f"Broker: {config.EXCHANGE} at {events.masked(url)}")
    print("=" * 70)

    connection = await aio_pika.connect(url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    # Declare a test tap queue binding to '#' to observe all published events
    tap_queue = await channel.declare_queue(exclusive=True)
    await tap_queue.bind(exchange, routing_key="#")

    results = []

    async def capture_event(timeout=2.0):
        try:
            msg = await asyncio.wait_for(tap_queue.get(no_ack=False), timeout=timeout)
            await msg.ack()
            body = json.loads(msg.body.decode("utf-8"))
            return msg.routing_key, body
        except asyncio.TimeoutError:
            return None, None

    # -------------------------------------------------------------
    # 1. Standard Business Events (Check actual publisher methods)
    # -------------------------------------------------------------
    print("\n--- 1. Testing Standard Service Publishers with Realistic Data ---")

    # 1.1 user.logged_out
    t_start = time.perf_counter()
    ok_logout = await user_rabbitmq.publish_user_logged_out(
        user_id="usr-789a-4bc3",
        role="citizen",
        jti="jti-uuid-xyz-1234",
        exp=int(time.time()) + 900,
        client_ip="192.168.1.105",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
        request_id="req-trace-001"
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_logout and rk == "user.logged_out" and data["payload"]["user_id"] == "usr-789a-4bc3"
    print(f"[{'PASS' if success else 'FAIL'}] user.logged_out: published={ok_logout}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("user.logged_out", success, f"{latency_ms:.2f}ms"))

    # 1.2 user.blocked
    t_start = time.perf_counter()
    ok_blocked = await admin_mq.publish_user_blocked(
        user_id="usr-bad-actor-007",
        admin_id="adm-super-root",
        reason="Gian lận thông tin giấy tờ tùy thân nhiều lần",
        duration_days=30
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_blocked and rk == "user.blocked" and data["payload"]["reason"] == "Gian lận thông tin giấy tờ tùy thân nhiều lần"
    print(f"[{'PASS' if success else 'FAIL'}] user.blocked: published={ok_blocked}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("user.blocked", success, f"{latency_ms:.2f}ms"))

    # 1.3 procedure.application.submitted
    t_start = time.perf_counter()
    ok_proc = await procedure_mq.publish_application_submitted(
        record_id="rec-dkkd-2026-9999",
        domain="Đăng ký doanh nghiệp qua mạng thông tin điện tử",
        user_id="usr-789a-4bc3"
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_proc and rk == "procedure.application.submitted" and data["payload"]["record_id"] == "rec-dkkd-2026-9999"
    print(f"[{'PASS' if success else 'FAIL'}] procedure.application.submitted: published={ok_proc}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("procedure.application.submitted", success, f"{latency_ms:.2f}ms"))

    # 1.4 lawyer.approved
    t_start = time.perf_counter()
    ok_lawyer = await lawyer_mq.publish_lawyer_approved(
        lawyer_id="law-ls-0042",
        user_id="usr-lawyer-pro",
        license_number="CCHN-LS-HN-2026/8888",
        bar_association="Đoàn Luật sư Thành phố Hà Nội",
        approved_by="admin-justice-dept"
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_lawyer and rk == "lawyer.approved" and data["payload"]["license_number"] == "CCHN-LS-HN-2026/8888"
    print(f"[{'PASS' if success else 'FAIL'}] lawyer.approved: published={ok_lawyer}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("lawyer.approved", success, f"{latency_ms:.2f}ms"))

    # 1.5 document.ocr.completed & document.processing.failed via publish_event_sync (in thread)
    t_start = time.perf_counter()
    ok_doc_sync = await asyncio.to_thread(
        events.publish_event_sync,
        "document.ocr.completed",
        {"document_id": "doc-cccd-front-01", "user_id": "usr-789a-4bc3", "error_code": None}
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_doc_sync and rk == "document.ocr.completed" and data["payload"]["document_id"] == "doc-cccd-front-01"
    print(f"[{'PASS' if success else 'FAIL'}] document.ocr.completed (sync): published={ok_doc_sync}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("document.ocr.completed (sync)", success, f"{latency_ms:.2f}ms"))

    t_start = time.perf_counter()
    ok_doc_fail = await asyncio.to_thread(
        events.publish_event_sync,
        "document.processing.failed",
        {"document_id": "doc-corrupted-pdf", "user_id": "usr-789a-4bc3", "error_code": "MALWARE_DETECTED"}
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_doc_fail and rk == "document.processing.failed" and data["payload"]["error_code"] == "MALWARE_DETECTED"
    print(f"[{'PASS' if success else 'FAIL'}] document.processing.failed (sync): published={ok_doc_fail}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("document.processing.failed (sync)", success, f"{latency_ms:.2f}ms"))

    # 1.6 contract.generated via publish_event_sync (in thread)
    t_start = time.perf_counter()
    ok_contract_sync = await asyncio.to_thread(
        events.publish_event_sync,
        "contract.generated",
        {"user_id": "usr-789a-4bc3", "job_id": "job-render-99", "draft_id": "dft-hdmb-2026", "format": "pdf", "error_code": None}
    )
    rk, data = await capture_event()
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_contract_sync and rk == "contract.generated" and data["payload"]["format"] == "pdf"
    print(f"[{'PASS' if success else 'FAIL'}] contract.generated (sync): published={ok_contract_sync}, routed_rk={rk}, latency={latency_ms:.2f}ms")
    results.append(("contract.generated (sync)", success, f"{latency_ms:.2f}ms"))

    # -------------------------------------------------------------
    # 2. Abnormal & Adversarial Payload Tests
    # -------------------------------------------------------------
    print("\n--- 2. Testing Abnormal & Adversarial Payloads ---")

    # 2.1 Empty dictionary payload
    ok_empty = await events.publish_event("test.empty", {})
    rk, data = await capture_event()
    success = ok_empty and data["payload"] == {}
    print(f"[{'PASS' if success else 'FAIL'}] Empty dictionary payload: published={ok_empty}, data={data}")
    results.append(("Empty payload", success, "routed ok"))

    # 2.2 Deep Vietnamese Unicode + Emojis + Special symbols
    heavy_vietnamese = "⚖️ CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM — Độc lập • Tự do • Hạnh phúc! " \
                       "Hợp đồng chuyển nhượng quyền sử dụng đất & tài sản gắn liền với đất số: 104/2026/HĐ-CNQSDĐ. " \
                       "Bên A: Nguyễn Văn Trọng (CMND/CCCD: 001099012345, cấp ngày 15/08/2021). " \
                       "Giá chuyển nhượng: 15.500.000.000 VNĐ (Mười lăm tỷ năm trăm triệu đồng chẵn). 🏛️📜🖋️"
    ok_vn = await events.publish_event("test.vietnamese", {"contract_text": heavy_vietnamese, "symbols": "<>\"'&/\n\t\r"})
    rk, data = await capture_event()
    success = ok_vn and data["payload"]["contract_text"] == heavy_vietnamese
    print(f"[{'PASS' if success else 'FAIL'}] Heavy Vietnamese Unicode & Emojis: published={ok_vn}, length={len(heavy_vietnamese)}")
    results.append(("Heavy Unicode & Emojis", success, "integrity verified"))

    # 2.3 Massive Payload (1 MB text payload)
    large_chunk = "A" * (1024 * 1024)  # 1 MB
    t_start = time.perf_counter()
    ok_large = await events.publish_event("test.large", {"big_field": large_chunk})
    rk, data = await capture_event(timeout=5.0)
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_large and len(data["payload"]["big_field"]) == len(large_chunk)
    print(f"[{'PASS' if success else 'FAIL'}] 1 MB Large Payload: published={ok_large}, routed_bytes={len(data['payload']['big_field'])}, latency={latency_ms:.2f}ms")
    results.append(("1 MB Large Payload", success, f"{latency_ms:.2f}ms"))

    # 2.4 Massive Payload (5 MB text payload)
    large_5mb = "B" * (5 * 1024 * 1024)  # 5 MB
    t_start = time.perf_counter()
    ok_5mb = await events.publish_event("test.large_5mb", {"huge_field": large_5mb})
    rk, data = await capture_event(timeout=10.0)
    latency_ms = (time.perf_counter() - t_start) * 1000
    success = ok_5mb and len(data["payload"]["huge_field"]) == len(large_5mb)
    print(f"[{'PASS' if success else 'FAIL'}] 5 MB Large Payload: published={ok_5mb}, routed_bytes={len(data['payload']['huge_field'])}, latency={latency_ms:.2f}ms")
    results.append(("5 MB Large Payload", success, f"{latency_ms:.2f}ms"))

    # -------------------------------------------------------------
    # 3. Security & URL Sanitization
    # -------------------------------------------------------------
    print("\n--- 3. Testing Security & URL Sanitization (Anti-Phishing) ---")
    attack_vectors = [
        ("//attacker.com/evil-login", "/"),
        ("\\evil.com/phish", "/"),
        ("javascript:alert(document.cookie)", "/"),
        ("data:text/html,<script>alert(1)</script>", "/"),
        ("https://external-fake-domain.vn/steal", "/"),
        ("http://192.168.1.1:8080/admin", "/"),
        ("/legitimate/documents/12345", "/legitimate/documents/12345"),
        ("/contracts/drafts/abc-xyz?ref=share", "/contracts/drafts/abc-xyz?ref=share"),
        (None, "/"),
        (12345, "/"),
        ("", "/"),
    ]
    all_sanitized = True
    for input_url, expected in attack_vectors:
        actual = sanitize_action_url(input_url, default_path="/")
        matches = actual == expected
        if not matches:
            all_sanitized = False
            print(f"  [VULNERABLE] Input: {input_url} -> Expected: {expected}, Got: {actual}")
    print(f"[{'PASS' if all_sanitized else 'FAIL'}] URL Sanitizer Anti-Phishing Filter: {len(attack_vectors)} vectors tested.")
    results.append(("Anti-Phishing URL Sanitizer", all_sanitized, f"{len(attack_vectors)} vectors passed"))

    # -------------------------------------------------------------
    # 4. Notification Hub Payload Formatting Resilience
    # -------------------------------------------------------------
    print("\n--- 4. Testing Notification Hub Payload Formatting Resilience ---")
    n1 = format_notification("document.ocr.completed", {"document_id": "doc-1"})
    assert n1 is None, "Should ignore notification without user_id"

    n2 = format_notification("document.ocr.completed", {"user_id": "usr-1"})
    assert n2 is not None and "Tài liệu #None" in n2["message"]

    n3 = format_notification("unknown.system.heartbeat", {"user_id": "usr-1"})
    assert n3 is None, "Should ignore unknown event type"

    n4 = format_notification("user.blocked", {"user_id": "usr-1", "reason": None})
    assert n4 is not None and "Vi phạm điều khoản dịch vụ" in n4["message"]

    print("[PASS] Notification Hub format resilience: missing fields, unknown types, and None values handled safely.")
    results.append(("Notification Hub Formatting Resilience", True, "safe fallback"))

    await connection.close()

    print("\n" + "=" * 70)
    print("TEST 01 SUMMARY:")
    all_pass = all(status for _, status, _ in results)
    for name, status, detail in results:
        print(f"  - {name:<38}: {'PASS' if status else 'FAIL'} ({detail})")
    print("=" * 70)
    return all_pass


if __name__ == "__main__":
    passed = asyncio.run(run_test())
    sys.exit(0 if passed else 1)
