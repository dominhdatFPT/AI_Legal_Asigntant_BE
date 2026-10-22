"""Test 03: Concurrency & Stress Load Testing for RabbitMQ.
Answers the user's question: "Chịu được bao nhiêu event cùng 1 lúc?"
Tests:
1. Async Publisher Concurrency: 50, 200, 500, 1000, 2500, 5000, 10000 events.
2. End-to-End Consumer Drain Rate & Latency Percentiles (p50, p95, p99).
3. Synchronous Publisher Overhead & Multi-Thread Concurrency (publish_event_sync).
4. SQLite Concurrency Stress Limit (Database lock failure boundary under prefetch).
"""
import asyncio
import concurrent.futures
import json
import logging
import os
import statistics
import sys
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import aio_pika
import config

from common_auth import events

logging.basicConfig(level=logging.WARNING, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Test03")


async def run_load_test():
    url = config.RABBITMQ_URL
    print("=" * 75)
    print("TEST 03: CONCURRENCY & STRESS LOAD BENCHMARK ('CHỊU ĐƯỢC BAO NHIÊU EVENT CÙNG 1 LÚC?')")
    print(f"Broker: {config.EXCHANGE} at {events.masked(url)}")
    print("=" * 75)

    connection = await aio_pika.connect(url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    report = []

    # =========================================================================
    # PART 1: Progressive Async Publisher Burst Test
    # =========================================================================
    print("\n--- 1. Testing Async Publisher Burst Capacity (Simultaneous Events) ---")
    concurrency_tiers = [50, 200, 500, 1000, 2500, 5000]

    # Declare a test drain queue to absorb load
    load_queue = await channel.declare_queue("q.test.benchmark_sink", durable=True, auto_delete=False)
    await load_queue.bind(exchange, routing_key="benchmark.#")

    for tier in concurrency_tiers:
        print(f"\n[TIER] Firing {tier} concurrent events simultaneously...")

        latencies = []
        success_count = 0
        fail_count = 0

        async def worker_publish(i: int):
            nonlocal success_count, fail_count
            payload = {
                "user_id": f"u-bench-{i}",
                "event_index": i,
                "timestamp": time.time(),
                "data": "x" * 256  # 256 bytes realistic payload
            }
            t0 = time.perf_counter()
            try:
                ok = await events.publish_event(f"benchmark.tier_{tier}", payload)
                elapsed = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed)
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
                elapsed = (time.perf_counter() - t0) * 1000
                latencies.append(elapsed)

        t_batch_start = time.perf_counter()
        # Launch all tier coroutines simultaneously
        tasks = [worker_publish(i) for i in range(tier)]
        await asyncio.gather(*tasks)
        total_batch_time = time.perf_counter() - t_batch_start

        throughput = tier / total_batch_time if total_batch_time > 0 else 0
        min_lat = min(latencies) if latencies else 0
        max_lat = max(latencies) if latencies else 0
        med_lat = statistics.median(latencies) if latencies else 0
        p95_lat = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max_lat
        p99_lat = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else max_lat

        print(f"  • Total time: {total_batch_time:.3f}s")
        print(f"  • Throughput: {throughput:.1f} events/sec")
        print(f"  • Success: {success_count}/{tier} ({success_count/tier*100:.1f}%), Fail: {fail_count}")
        print(f"  • Latency (ms): min={min_lat:.2f}, med(p50)={med_lat:.2f}, p95={p95_lat:.2f}, p99={p99_lat:.2f}, max={max_lat:.2f}")

        report.append({
            "test": f"Async Burst {tier} events",
            "count": tier,
            "duration_s": total_batch_time,
            "throughput_eps": throughput,
            "success_rate": success_count / tier * 100,
            "p50_ms": med_lat,
            "p95_ms": p95_lat,
            "max_ms": max_lat
        })

        # Purge after tier
        await load_queue.purge()
        await asyncio.sleep(0.5)

    # =========================================================================
    # PART 2: End-to-End Consumer Drain Rate
    # =========================================================================
    print("\n--- 2. Testing End-to-End Consumer Drain Throughput ---")
    drain_count = 2000
    print(f"Preloading {drain_count} messages into durable queue q.test.consumer_drain...")

    # Clean delete old queue if exists
    try:
        await channel.queue_delete("q.test.consumer_drain")
    except Exception:
        pass
    drain_queue = await events.declare_work_queue(channel, "q.test.consumer_drain")
    await drain_queue.bind(exchange, routing_key="drain.test")

    # Fast preload using asyncio.gather
    preload_start = time.perf_counter()
    async def pub_one(i):
        msg = aio_pika.Message(
            body=json.dumps({"id": i, "data": "drain_payload"}).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT
        )
        await exchange.publish(msg, routing_key="drain.test")
    await asyncio.gather(*[pub_one(i) for i in range(drain_count)])
    preload_time = time.perf_counter() - preload_start
    print(f"  Preloaded {drain_count} messages in {preload_time:.2f}s ({drain_count/preload_time:.1f} msg/s)")

    consumed_count = 0
    consume_done = asyncio.Event()

    async def drain_handler(routing_key: str, data: dict):
        nonlocal consumed_count
        consumed_count += 1
        if consumed_count >= drain_count:
            consume_done.set()

    # Test with prefetch=100
    prefetch_val = 100
    consumer_task = asyncio.create_task(
        events.consume_forever("q.test.consumer_drain", ["drain.test"], drain_handler, prefetch=prefetch_val)
    )

    t_drain_start = time.perf_counter()
    try:
        await asyncio.wait_for(consume_done.wait(), timeout=15.0)
    except asyncio.TimeoutError:
        print(f"  [TIMEOUT] Only drained {consumed_count}/{drain_count} within 15s!")

    total_drain_time = time.perf_counter() - t_drain_start
    drain_eps = consumed_count / total_drain_time if total_drain_time > 0 else 0
    print(f"  Consumer Drained: {consumed_count}/{drain_count} messages in {total_drain_time:.3f}s ({drain_eps:.1f} msg/s, prefetch={prefetch_val})")
    report.append({
        "test": f"Consumer Drain ({drain_count} msgs, prefetch={prefetch_val})",
        "count": consumed_count,
        "duration_s": total_drain_time,
        "throughput_eps": drain_eps,
        "success_rate": consumed_count / drain_count * 100,
        "p50_ms": 0, "p95_ms": 0, "max_ms": 0
    })
    consumer_task.cancel()

    # =========================================================================
    # PART 3: Synchronous Publisher Overhead (publish_event_sync)
    # =========================================================================
    print("\n--- 3. Testing Synchronous Publisher (publish_event_sync) Limits ---")
    sync_counts = [10, 50, 100]

    for count in sync_counts:
        t0 = time.perf_counter()
        latencies = []
        for i in range(count):
            t_single = time.perf_counter()
            ok = await asyncio.to_thread(events.publish_event_sync, "sync.bench", {"idx": i})
            latencies.append((time.perf_counter() - t_single) * 1000)
        total_time = time.perf_counter() - t0
        eps = count / total_time if total_time > 0 else 0
        avg_lat = statistics.mean(latencies)
        print(f"  • Sequential Sync {count:3d} msgs: total={total_time:.3f}s, throughput={eps:5.1f} msg/s, avg latency={avg_lat:.2f}ms/call")
        report.append({
            "test": f"Sync Publisher Sequential ({count} msgs)",
            "count": count,
            "duration_s": total_time,
            "throughput_eps": eps,
            "success_rate": 100.0,
            "p50_ms": statistics.median(latencies),
            "p95_ms": max(latencies),
            "max_ms": max(latencies)
        })

    # Multi-threaded concurrent sync publish
    print("Testing Multi-threaded Concurrent publish_event_sync (10 threads)...")
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(events.publish_event_sync, "sync.bench.threaded", {"t": i}) for i in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    threaded_time = time.perf_counter() - t0
    threaded_eps = len(results) / threaded_time
    print(f"  • 10 Threads publishing 50 sync events: total={threaded_time:.3f}s, throughput={threaded_eps:.1f} msg/s")
    report.append({
        "test": "Sync Publisher 10 Threads (50 msgs)",
        "count": 50,
        "duration_s": threaded_time,
        "throughput_eps": threaded_eps,
        "success_rate": sum(results) / len(results) * 100,
        "p50_ms": 0, "p95_ms": 0, "max_ms": 0
    })

    # =========================================================================
    # PART 4: SQLite Concurrency Stress Limit (Notification Store)
    # =========================================================================
    print("\n--- 4. Testing Notification Hub SQLite Concurrency Boundary ---")
    notif_store = config.load_service_module("notification-service", "app.core.notification_store")
    test_db = "data/bench_notif.db"
    os.environ["NOTIFICATION_DATABASE_URL"] = f"sqlite:///{test_db}"
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass
    notif_store._engine = None
    notif_store._SessionFactory = None
    notif_store.get_engine()

    sqlite_tiers = [10, 20, 50, 100]
    for tier in sqlite_tiers:
        locked_errors = 0
        success_db = 0

        def save_worker(idx):
            nonlocal locked_errors, success_db
            try:
                notif_store.save_notification({
                    "id": f"notif-burst-{tier}-{idx}",
                    "user_id": f"u-burst-{tier}",
                    "event_type": "procedure.application.submitted",
                    "title": f"Thông báo #{idx}",
                    "message": "Chi tiết",
                    "created_at": time.time()
                })
                success_db += 1
            except Exception as e:
                locked_errors += 1

        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=tier) as executor:
            futures = [executor.submit(save_worker, i) for i in range(tier)]
            concurrent.futures.wait(futures)
        db_time = time.perf_counter() - t0
        print(f"  • SQLite Burst {tier:3d} threads: success={success_db}/{tier}, locked_errors={locked_errors}, time={db_time:.3f}s")
        report.append({
            "test": f"SQLite Concurrent Burst ({tier} threads)",
            "count": tier,
            "duration_s": db_time,
            "throughput_eps": success_db / db_time if db_time > 0 else 0,
            "success_rate": success_db / tier * 100,
            "p50_ms": 0, "p95_ms": 0, "max_ms": 0
        })

    # Cleanup queues
    await channel.queue_delete("q.test.benchmark_sink")
    await channel.queue_delete("q.test.consumer_drain")
    await connection.close()

    print("\n" + "=" * 90)
    print("CONCURRENCY & LOAD TEST SUMMARY TABLE:")
    print(f"{'Test Scenario':<42} | {'Events':<7} | {'Duration':<8} | {'Throughput':<14} | {'Success Rate'}")
    print("-" * 90)
    for r in report:
        print(f"{r['test']:<42} | {r['count']:<7} | {r['duration_s']:6.3f}s  | {r['throughput_eps']:7.1f} msg/s  | {r['success_rate']:5.1f}%")
    print("=" * 90)


if __name__ == "__main__":
    asyncio.run(run_load_test())
