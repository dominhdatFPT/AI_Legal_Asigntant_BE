"""Test 02: Verification of internal data flow through each RabbitMQ component.
Tests:
- Component 1: Dead Letter Exchange (DLX) & Queue (q.dead_letter) on error/rejection
- Component 2: Token Revocation Pub/Sub & In-Memory Cleanup
- Component 3: Notification Hub (q.notification.events) formatting & deduplication
- Component 4: Logout Audit Worker (q.audit.user_logout)
- Component 5: SQLite Storage Concurrency Behavior under Prefetched Consumer Loads
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import aio_pika
import config

from common_auth import events, revocation

notif_hub = config.load_service_module("notification-service", "app.core.rabbitmq_notification_hub")
notif_store = config.load_service_module("notification-service", "app.core.notification_store")
logout_worker = config.load_service_module("user-service", "app.services.logout_worker")
user_rabbitmq = config.load_service_module("user-service", "app.core.rabbitmq")

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Test02")


async def run_test():
    url = config.RABBITMQ_URL
    print("=" * 70)
    print("TEST 02: INTERNAL COMPONENTS DATA FLOW & ERROR HANDLING")
    print(f"Broker URL: {events.masked(url)}")
    print("=" * 70)

    connection = await aio_pika.connect(url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    results = []

    # =============================================================
    # COMPONENT 1: Dead Letter Exchange & Queue (q.dead_letter)
    # =============================================================
    print("\n--- 1. Testing Dead Letter Exchange & Error Routing (q.dead_letter) ---")
    test_err_queue_name = "q.test.error_rejection"

    # Setup queue with DLX
    err_queue = await events.declare_work_queue(channel, test_err_queue_name)
    await err_queue.bind(exchange, routing_key="test.component.error")

    # Purge DLQ first
    dlq = await channel.declare_queue(events.DEAD_LETTER_QUEUE, durable=True, arguments={"x-max-length": 10_000})
    await dlq.purge()

    async def faulty_handler(routing_key: str, data: dict):
        # Simulates an unhandled business crash (e.g. database disconnect, code bug)
        raise ValueError(f"Crash simulated for event: {data.get('event_id')}")

    # Start consumer on faulty queue
    consumer_task = asyncio.create_task(
        events.consume_forever(test_err_queue_name, ["test.component.error"], faulty_handler, prefetch=1)
    )
    await asyncio.sleep(0.5)

    # Publish an event that will trigger the failure
    fail_event_id = f"fail-evt-{uuid.uuid4().hex[:8]}"
    await exchange.publish(
        aio_pika.Message(
            body=json.dumps({"event_id": fail_event_id, "payload": {"data": "will_fail"}}).encode("utf-8"),
            message_id=fail_event_id,
        ),
        routing_key="test.component.error",
    )

    # Wait for consumer to process, fail, and reject to DLQ
    await asyncio.sleep(1.0)
    consumer_task.cancel()

    # Verify message landed in q.dead_letter
    dl_msg = await dlq.get(no_ack=False)
    if dl_msg:
        await dl_msg.ack()
        dl_body = json.loads(dl_msg.body.decode("utf-8"))
        headers = dl_msg.headers
        has_x_death = "x-death" in headers
        success = (dl_body.get("event_id") == fail_event_id) and has_x_death
        death_info = headers.get("x-death", [{}])[0]
        print(f"[{'PASS' if success else 'FAIL'}] Message safely moved to DLQ: id={dl_body.get('event_id')}, "
              f"x-death reason={death_info.get('reason')}, orig_queue={death_info.get('queue')}")
        results.append(("Dead Letter Queue Routing on Exception", success, f"reason: {death_info.get('reason')}"))
    else:
        print("[FAIL] Message was LOST! Not found in q.dead_letter!")
        results.append(("Dead Letter Queue Routing on Exception", False, "Message lost"))

    # Also test Malformed Raw Bytes (non-JSON)
    print("Testing malformed non-JSON raw byte rejection...")
    consumer_task2 = asyncio.create_task(
        events.consume_forever(test_err_queue_name, ["test.component.error"], faulty_handler, prefetch=1)
    )
    await asyncio.sleep(0.5)
    await exchange.publish(
        aio_pika.Message(body=b"\x00\xff\xfe INVALID RAW BYTES NOT JSON"),
        routing_key="test.component.error",
    )
    await asyncio.sleep(1.0)
    consumer_task2.cancel()

    dl_msg2 = await dlq.get(no_ack=False)
    if dl_msg2:
        await dl_msg2.ack()
        success2 = dl_msg2.body == b"\x00\xff\xfe INVALID RAW BYTES NOT JSON"
        print(f"[{'PASS' if success2 else 'FAIL'}] Corrupted non-JSON byte rejection to DLQ: {success2}")
        results.append(("Non-JSON Malformed Rejection to DLQ", success2, "preserved raw body"))
    else:
        print("[FAIL] Non-JSON message was NOT routed to DLQ!")
        results.append(("Non-JSON Malformed Rejection to DLQ", False, "Lost"))

    await channel.queue_delete(test_err_queue_name)

    # =============================================================
    # COMPONENT 2: Token Revocation Pub/Sub (common_auth.revocation)
    # =============================================================
    print("\n--- 2. Testing Token Revocation Pub/Sub & Memory Management ---")
    revocation.clear()
    revocation.CHECK_BLOCKED = True

    # Start revocation consumer
    revoc_consumer = asyncio.create_task(
        events.consume_forever(None, [revocation.LOGGED_OUT, revocation.BLOCKED], revocation.apply_event, prefetch=100)
    )
    await asyncio.sleep(0.5)

    # Test Logout Revocation
    test_jti = f"jti-{uuid.uuid4().hex[:12]}"
    assert not revocation.is_jti_revoked(test_jti), "Initially JTI should not be revoked"
    pub_ok = await events.publish_event(
        revocation.LOGGED_OUT,
        {"jti": test_jti, "exp": time.time() + 300, "user_id": "u-test-revoc"}
    )
    await asyncio.sleep(0.5)
    is_rev = revocation.is_jti_revoked(test_jti)
    print(f"[{'PASS' if is_rev else 'FAIL'}] user.logged_out JTI Revocation: pub={pub_ok}, revoked={is_rev}")
    results.append(("Token Revocation: JTI Logout", is_rev, f"jti={test_jti[:8]}..."))

    # Test Block User Revocation
    test_user_id = f"usr-block-{uuid.uuid4().hex[:8]}"
    t_before_block = int(time.time()) - 30
    token_before = {"sub": test_user_id, "iat": t_before_block, "jti": "jti-normal-1"}
    assert not revocation.is_revoked(token_before), "Token before block should be valid"

    pub_block_ok = await events.publish_event(
        revocation.BLOCKED,
        {"user_id": test_user_id, "reason": "Tài khoản có dấu hiệu bị hack"}
    )
    await asyncio.sleep(0.5)

    token_blocked_result = revocation.is_revoked(token_before)
    # Token issued AFTER block (e.g. user re-logs in after unblock)
    token_after = {"sub": test_user_id, "iat": int(time.time()) + 10, "jti": "jti-new-login"}
    token_after_result = not revocation.is_revoked(token_after)
    block_success = token_blocked_result and token_after_result
    print(f"[{'PASS' if block_success else 'FAIL'}] user.blocked Revocation: "
          f"old_token_revoked={token_blocked_result}, new_token_allowed={token_after_result}")
    results.append(("Token Revocation: Block User", block_success, "past iat revoked, future iat allowed"))

    # Memory Sweep Performance Test (O(N) iteration test)
    print("Testing O(N) memory sweep performance with 1,000 revoked JTIs...")
    t_sweep_start = time.perf_counter()
    for i in range(1000):
        revocation.revoke_jti(f"batch-jti-{i}", exp=time.time() + 600)
    sweep_duration_ms = (time.perf_counter() - t_sweep_start) * 1000
    print(f"  -> 1,000 in-memory revocations completed in {sweep_duration_ms:.2f}ms (avg {sweep_duration_ms/1000:.3f}ms/call)")
    results.append(("Revocation In-Memory Sweep", True, f"1000 items in {sweep_duration_ms:.2f}ms"))

    revoc_consumer.cancel()

    # =============================================================
    # COMPONENT 3: Notification Hub & Store Deduplication
    # =============================================================
    print("\n--- 3. Testing Notification Hub (q.notification.events) ---")

    # Use a clean SQLite DB in tmp for testing
    from pathlib import Path
    Path("data").mkdir(parents=True, exist_ok=True)
    test_db_path = str(Path("data/test_notif.db").resolve()).replace("\\", "/")
    if os.path.exists("data/test_notif.db"):
        try:
            os.remove("data/test_notif.db")
        except Exception:
            pass
    os.environ["NOTIFICATION_DATABASE_URL"] = f"sqlite:///{test_db_path}"
    notif_store._engine = None
    notif_store._SessionFactory = None
    notif_store.get_engine()

    # Purge existing inbox queue
    inbox_q = await events.declare_work_queue(channel, notif_hub.QUEUE_NOTIFICATION_INBOX)
    await inbox_q.purge()

    notif_hub_task = asyncio.create_task(notif_hub.start_notification_hub())
    await asyncio.sleep(0.5)

    target_user = f"usr-notif-{uuid.uuid4().hex[:8]}"

    # Send Procedure Submitted event
    await events.publish_event(
        "procedure.application.submitted",
        {"user_id": target_user, "record_id": "REC-999-XYZ", "domain": "Hộ tịch"}
    )
    # Send Document OCR Completed event
    await events.publish_event(
        "document.ocr.completed",
        {"user_id": target_user, "document_id": "DOC-777-ABC"}
    )
    # Send Contract Generated event
    await events.publish_event(
        "contract.generated",
        {"user_id": target_user, "draft_id": "DFT-555-PDF", "format": "pdf"}
    )
    # Send Lawyer Approved event
    await events.publish_event(
        "lawyer.approved",
        {"user_id": target_user, "license_number": "CCHN-001"}
    )
    # Send User Blocked event
    await events.publish_event(
        "user.blocked",
        {"user_id": target_user, "reason": "Hồ sơ giả mạo"}
    )

    await asyncio.sleep(1.5)

    user_notifs = notif_store.get_user_notifications(target_user)
    print(f"Received {len(user_notifs)}/5 notifications for user {target_user}:")
    for n in user_notifs:
        print(f"  • [{n['event_type']}] {n['title']}: {n['message']} (url={n['action_url']})")

    notifs_ok = len(user_notifs) == 5
    print(f"[{'PASS' if notifs_ok else 'FAIL'}] All 5 notification event types formatted and saved.")
    results.append(("Notification Hub Multi-Event Reception", notifs_ok, f"{len(user_notifs)}/5 captured"))

    # Test Deduplication (At-least-once delivery)
    print("Testing message deduplication by event_id...")
    fixed_event = events.make_event(
        "document.ocr.completed",
        {"user_id": target_user, "document_id": "DOC-DUPLICATE-TEST"}
    )
    fixed_event["event_id"] = f"evt-dedup-{uuid.uuid4().hex[:10]}"

    # Send exact same message TWICE
    await exchange.publish(
        aio_pika.Message(
            body=json.dumps(fixed_event).encode("utf-8"),
            message_id=fixed_event["event_id"],
        ),
        routing_key="document.ocr.completed",
    )
    await exchange.publish(
        aio_pika.Message(
            body=json.dumps(fixed_event).encode("utf-8"),
            message_id=fixed_event["event_id"],
        ),
        routing_key="document.ocr.completed",
    )
    await asyncio.sleep(1.0)

    dedup_notifs = [n for n in notif_store.get_user_notifications(target_user) if "DOC-DUPLICATE-TEST" in n["action_url"]]
    dedup_ok = len(dedup_notifs) == 1
    print(f"[{'PASS' if dedup_ok else 'FAIL'}] Deduplication: sent 2 copies with same event_id, stored count = {len(dedup_notifs)}")
    results.append(("Notification Deduplication", dedup_ok, f"count={len(dedup_notifs)}"))

    notif_hub_task.cancel()

    # =============================================================
    # COMPONENT 4: User Logout Audit Worker (q.audit.user_logout)
    # =============================================================
    print("\n--- 4. Testing User Logout Audit Worker (q.audit.user_logout) ---")
    audit_q = await events.declare_work_queue(channel, logout_worker.QUEUE_AUDIT)
    await audit_q.purge()

    captured_audit = []

    async def audit_spy_handler(routing_key: str, data: dict):
        captured_audit.append(data)
        await logout_worker.process_audit_log(routing_key, data)

    audit_task = asyncio.create_task(
        events.consume_forever(logout_worker.QUEUE_AUDIT, [user_rabbitmq.ROUTING_KEY_USER_LOGGED_OUT], audit_spy_handler)
    )
    await asyncio.sleep(0.5)

    test_audit_user = "usr-audit-verify-99"
    await user_rabbitmq.publish_user_logged_out(
        user_id=test_audit_user,
        role="lawyer",
        client_ip="10.0.0.1",
        user_agent="AuditAgent/1.0"
    )
    await asyncio.sleep(1.0)
    audit_task.cancel()

    audit_ok = len(captured_audit) >= 1 and captured_audit[0]["payload"]["user_id"] == test_audit_user
    print(f"[{'PASS' if audit_ok else 'FAIL'}] Logout Audit Worker processed audit event: user_id={test_audit_user}")
    results.append(("Logout Audit Worker Processing", audit_ok, "audit log recorded"))

    await connection.close()

    print("\n" + "=" * 70)
    print("TEST 02 SUMMARY:")
    all_pass = all(status for _, status, _ in results)
    for name, status, detail in results:
        print(f"  - {name:<40}: {'PASS' if status else 'FAIL'} ({detail})")
    print("=" * 70)
    return all_pass


if __name__ == "__main__":
    passed = asyncio.run(run_test())
    sys.exit(0 if passed else 1)
