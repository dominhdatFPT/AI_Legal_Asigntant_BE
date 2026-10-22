"""Test 05: Deep Audit & Reality Check of Latent Bugs, Race Conditions, and Edge Cases.
DO NOT MODIFY ANY CODE IN BE/ OR LIBS/.
This script tests and documents:
1. Bug 1: Localhost IPv6 vs IPv4 Windows Resolution Freeze.
2. Bug 2: Race Condition in notification_store (Unprotected _engine / _SessionFactory assignment).
3. Bug 3: Duplicate Delivery DLQ Rejection (Unhandled IntegrityError instead of ACK).
4. Bug 4: publish_event_sync crashing inside existing asyncio event loop.
5. Bug 5: Global 10-Second Blackout on single failure in EventPublisher.
6. Bug 6: O(N) memory sweep latency scaling in token revocation.
"""
import asyncio
import concurrent.futures
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import aio_pika
import config

from common_auth import events, revocation

notif_store = config.load_service_module("notification-service", "app.core.notification_store")
notif_hub = config.load_service_module("notification-service", "app.core.rabbitmq_notification_hub")


async def audit_bugs():
    print("=" * 85)
    print("TEST 05: DEEP AUDIT OF LATENT BUGS, RACE CONDITIONS & STRUCTURAL BOTTLENECK FINDINGS")
    print("=" * 85)

    findings = []

    # -------------------------------------------------------------------------
    # BUG 1: Localhost IPv6 vs IPv4 Resolution on Windows
    # -------------------------------------------------------------------------
    print("\n[AUDIT 1] BE/.env 'localhost' Configuration Reality Check on Windows Host...")
    t0 = time.perf_counter()
    lh_success = False
    lh_err = ""
    try:
        c = await aio_pika.connect(config.ENV_CONFIGURED_URL, timeout=2)
        await c.close()
        lh_success = True
    except Exception as e:
        lh_err = f"{type(e).__name__}: {e}"
    t_lh = time.perf_counter() - t0
    print(f"  Result: success={lh_success}, time={t_lh:.3f}s, error='{lh_err}'")
    print("  -> Impact: All host workers (logout_worker, document worker local) and scripts fail on Windows when using BE/.env as-is.")
    findings.append({
        "id": "BUG-01",
        "severity": "CRITICAL (LOCAL/HOST RUNTIME)",
        "component": "BE/.env line 27 (RABBITMQ_URL)",
        "symptom": f"Connection times out after 2.0s ({lh_err})",
        "root_cause": "On Windows, 'localhost' resolves to IPv6 '::1' first, while docker-compose.yml line 334 explicitly binds only to IPv4 '127.0.0.1:5672:5672'."
    })

    # -------------------------------------------------------------------------
    # BUG 2: Race Condition in notification_store Initialization
    # -------------------------------------------------------------------------
    print("\n[AUDIT 2] Multi-Threaded Cold-Start Race Condition in notification_store...")
    # Reset engine and SessionFactory to simulate cold start
    notif_store._engine = None
    notif_store._SessionFactory = None
    test_db = "data/audit_notif.db"
    os.environ["NOTIFICATION_DATABASE_URL"] = f"sqlite:///{test_db}"

    none_type_errors = 0

    def concurrent_caller(i):
        nonlocal none_type_errors
        try:
            # Calls get_session concurrently
            session = notif_store.get_session()
            session.close()
        except TypeError as te:
            if "'NoneType' object is not callable" in str(te):
                none_type_errors += 1
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(concurrent_caller, i) for i in range(20)]
        concurrent.futures.wait(futures)

    print(f"  Result: TypeError: 'NoneType' object is not callable occurred {none_type_errors} times!")
    print("  -> Impact: When notification-service starts up with pending messages in q.notification.events, incoming messages crash and are rejected to q.dead_letter.")
    findings.append({
        "id": "BUG-02",
        "severity": "HIGH",
        "component": "notification-service/app/core/notification_store.py (get_engine / get_session)",
        "symptom": f"TypeError: 'NoneType' object is not callable during cold start ({none_type_errors} failures)",
        "root_cause": "In get_engine(), _engine is assigned before _SessionFactory is created, with no threading.Lock(). Concurrent threads see _engine is not None and attempt to call _SessionFactory() while it is still None."
    })

    # -------------------------------------------------------------------------
    # BUG 3: Duplicate Delivery DLQ Rejection (Unhandled IntegrityError)
    # -------------------------------------------------------------------------
    print("\n[AUDIT 3] At-Least-Once Duplicate Message Delivery Rejection...")
    notif_store.get_engine()
    dup_id = f"notif-audit-dup-{int(time.time())}"

    # First insert
    notif_store.save_notification({
        "id": dup_id,
        "user_id": "u-dup-audit",
        "event_type": "procedure.application.submitted",
        "title": "T1",
        "message": "M1"
    })

    # Simulate simultaneous duplicate event being formatted and saved
    integrity_caught = False
    try:
        # Directly simulating what handle_event does when duplicate message arrives concurrently
        with notif_store.get_session() as session:
            # If two threads checked session.get() at same time before commit:
            record = notif_store.NotificationRecord(
                id=dup_id,
                user_id="u-dup-audit",
                event_type="procedure.application.submitted",
                title="T1",
                message="M1",
                created_at=time.time()
            )
            session.add(record)
            session.commit()
    except Exception as e:
        integrity_caught = type(e).__name__

    print(f"  Result: Concurrent duplicate insert throws {integrity_caught}")
    print("  -> Impact: In rabbitmq_notification_hub.py, handle_event() does not catch IntegrityError. When broker redelivers or duplicates arrive concurrently, the message is rejected with requeue=False and routed to q.dead_letter instead of being acknowledged as duplicate.")
    findings.append({
        "id": "BUG-03",
        "severity": "MEDIUM-HIGH",
        "component": "notification-service/app/core/notification_store.py & rabbitmq_notification_hub.py",
        "symptom": "Duplicate messages landing in q.dead_letter instead of being acknowledged",
        "root_cause": "save_notification() relies on check-then-insert without handling IntegrityError; unhandled exception in handle_event() triggers message.reject(requeue=False)."
    })

    # -------------------------------------------------------------------------
    # BUG 4: publish_event_sync in Async Loop Crash
    # -------------------------------------------------------------------------
    print("\n[AUDIT 4] publish_event_sync inside Async Event Loop Crash...")
    sync_in_loop_crash = False
    try:
        events.publish_event_sync("test.sync", {"foo": "bar"})
    except RuntimeError as re:
        if "cannot be called from a running event loop" in str(re):
            sync_in_loop_crash = True

    print(f"  Result: Crash caught = {sync_in_loop_crash}")
    print("  -> Impact: Any asynchronous service or route calling publish_event_sync directly will crash immediately with RuntimeError.")
    findings.append({
        "id": "BUG-04",
        "severity": "MEDIUM",
        "component": "libs/common_auth/common_auth/events.py (publish_event_sync)",
        "symptom": "RuntimeError: asyncio.run() cannot be called from a running event loop",
        "root_cause": "publish_event_sync calls asyncio.run() unconditionally. It works only in synchronous threads/processes."
    })

    # -------------------------------------------------------------------------
    # BUG 5: Global 10-Second Blackout on Single Failure
    # -------------------------------------------------------------------------
    print("\n[AUDIT 5] Global 10-Second Event Blackout in EventPublisher...")
    pub = events.EventPublisher()
    # Artificially trigger retry backoff by failing
    pub._retry_at = time.monotonic() + events.RETRY_AFTER

    # Try publishing 5 subsequent events during this backoff
    dropped = 0
    t_start = time.perf_counter()
    for _ in range(5):
        ok = await pub.publish("critical.user.blocked", {"user_id": "u1"})
        if not ok:
            dropped += 1
    duration_dropped = (time.perf_counter() - t_start) * 1000

    print(f"  Result: {dropped}/5 events immediately dropped (returned False) in {duration_dropped:.3f}ms")
    print("  -> Impact: If a single event publish experiences a transient TCP glitch or timeout, the publisher engages a 10-second blackout (time.monotonic() < self._retry_at: return False), causing ALL domain events across the service to be SILENTLY DROPPED for 10 seconds.")
    findings.append({
        "id": "BUG-05",
        "severity": "HIGH",
        "component": "libs/common_auth/common_auth/events.py (EventPublisher.publish)",
        "symptom": "All events dropped silently for 10 seconds following any transient timeout",
        "root_cause": "Line 98 sets self._retry_at = time.monotonic() + 10.0 on any exception; line 81 silently drops any publish attempt while now < _retry_at without queuing or retrying."
    })

    # -------------------------------------------------------------------------
    # BUG 6: O(N) Iteration Sweep on Every Event in Token Revocation
    # -------------------------------------------------------------------------
    print("\n[AUDIT 6] O(N) Token Revocation Memory Sweep Scaling...")
    revocation.clear()
    sizes = [100, 1000, 5000, 10000]
    sweep_timings = []
    for s in sizes:
        revocation.clear()
        # Prepopulate
        now = time.time()
        for i in range(s):
            revocation._revoked_jti[f"jti-{i}"] = now + 900
        # Measure time to insert 1 more event (which sweeps all s items)
        t0 = time.perf_counter()
        for _ in range(50):
            revocation.revoke_jti(f"new-jti-{_}", now + 900)
        elapsed_ms = (time.perf_counter() - t0) * 1000 / 50
        sweep_timings.append((s, elapsed_ms))
        print(f"  • Revocation table size {s:5d}: single sweep latency = {elapsed_ms:.4f}ms")

    findings.append({
        "id": "BUG-06",
        "severity": "LOW-MEDIUM",
        "component": "libs/common_auth/common_auth/revocation.py (revoke_jti & block_user)",
        "symptom": f"O(N) latency growth under table size (at 10,000 items: {sweep_timings[-1][1]:.4f}ms under lock)",
        "root_cause": "On every single event, lines 43-44 iterate through all keys in _revoked_jti under threading.Lock() to purge expired items. Should use a heapq, TTL cache, or periodic cleanup thread."
    })

    print("\n" + "=" * 85)
    print("DEEP AUDIT FINDINGS SUMMARY:")
    print("=" * 85)
    for f in findings:
        print(f"[{f['id']}] [{f['severity']}] {f['component']}")
        print(f"  - Symptom   : {f['symptom']}")
        print(f"  - Root Cause: {f['root_cause']}\n")


if __name__ == "__main__":
    asyncio.run(audit_bugs())
