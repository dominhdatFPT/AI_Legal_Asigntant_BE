"""Test 04: Chaos, Fault-Tolerance & Extreme Limits Testing.
Tests:
1. 10,000 Simultaneous Events Extreme Load Burst.
2. Broker Outage / Timeout & 10s Circuit-Breaker Backoff Verification.
3. Sudden TCP Disconnect & Auto-Recovery.
4. Queue x-max-length Overflow Protection Behavior.
5. Windows host IPv6 vs IPv4 Configuration Reality Check (BE/.env vs Docker bind).
"""
import asyncio
import logging
import os
import statistics
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import aio_pika
import config

from common_auth import events

logging.basicConfig(level=logging.WARNING, format="[%(asctime)s] %(levelname)s: %(message)s")


async def run_chaos_and_limits():
    url = config.RABBITMQ_URL
    print("=" * 80)
    print("TEST 04: CHAOS RESILIENCE, CIRCUIT BREAKER & EXTREME 10,000 BURST LIMIT")
    print(f"Broker: {config.EXCHANGE} at {events.masked(url)}")
    print("=" * 80)

    connection = await aio_pika.connect(url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    results = []

    # =========================================================================
    # 1. Extreme 10,000 Simultaneous Events Burst
    # =========================================================================
    print("\n--- 1. Testing Extreme 10,000 Simultaneous Events Burst ---")
    sink_q = await channel.declare_queue("q.test.sink_10k", durable=False, auto_delete=True)
    await sink_q.bind(exchange, routing_key="burst10k.#")

    burst_count = 10000
    latencies = []
    successes = 0
    failures = 0

    async def send_one(i):
        nonlocal successes, failures
        t0 = time.perf_counter()
        try:
            ok = await events.publish_event(f"burst10k.event", {"idx": i, "data": "stress_payload"})
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)
            if ok:
                successes += 1
            else:
                failures += 1
        except Exception:
            failures += 1

    t_start = time.perf_counter()
    await asyncio.gather(*[send_one(i) for i in range(burst_count)])
    total_time = time.perf_counter() - t_start

    eps = burst_count / total_time
    p50 = statistics.median(latencies) if latencies else 0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else 0
    p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else 0
    max_lat = max(latencies) if latencies else 0

    print(f"  • 10,000 simultaneous events completed in: {total_time:.3f}s")
    print(f"  • Peak throughput: {eps:.1f} events/sec")
    print(f"  • Success rate: {successes}/{burst_count} ({successes/burst_count*100:.1f}%)")
    print(f"  • Latency (ms): min={min(latencies):.2f}, p50={p50:.2f}, p95={p95:.2f}, p99={p99:.2f}, max={max_lat:.2f}")

    burst_ok = successes == burst_count
    results.append(("10,000 Simultaneous Event Burst", burst_ok, f"{eps:.1f} msg/s, {total_time:.2f}s, p99={p99:.1f}ms"))
    await channel.queue_delete("q.test.sink_10k")

    # =========================================================================
    # 2. Broker Outage / Timeout & 10s Circuit-Breaker Backoff
    # =========================================================================
    print("\n--- 2. Testing Fault Tolerance: Circuit Breaker & 10s Backoff ---")
    dead_broker_url = "amqp://guest:guest@127.0.0.1:59999/"  # Non-existent port
    os.environ["RABBITMQ_URL"] = dead_broker_url

    test_pub = events.EventPublisher()

    # Call 1: Should attempt connection, time out within ~2.0-3.0s, log warning, and engage backoff
    t0 = time.perf_counter()
    ok_1 = await test_pub.publish("test.dead", {"data": 1})
    call1_duration = time.perf_counter() - t0
    backoff_engaged = (test_pub._retry_at > time.monotonic())
    print(f"  • Call 1 (Broker down): returned={ok_1} in {call1_duration:.3f}s, backoff_engaged={backoff_engaged}")

    # Call 2: Immediately following call 1, MUST NOT wait for TCP timeout (fast fail <= 1ms)
    t0 = time.perf_counter()
    ok_2 = await test_pub.publish("test.dead", {"data": 2})
    call2_duration = (time.perf_counter() - t0) * 1000  # in ms
    print(f"  • Call 2 (During 10s backoff): returned={ok_2} in {call2_duration:.3f}ms (Fast Fail without blocking HTTP request)")

    fast_fail_ok = (not ok_1) and (not ok_2) and (call2_duration < 10.0) and backoff_engaged
    results.append(("Broker Outage 10s Circuit Breaker Backoff", fast_fail_ok, f"Fast fail: {call2_duration:.3f}ms"))

    # Restore correct URL
    os.environ["RABBITMQ_URL"] = url

    # =========================================================================
    # 3. Connection Auto-Recovery after Broker/Socket Reset
    # =========================================================================
    print("\n--- 3. Testing Connection Auto-Recovery after Abrupt Close ---")
    live_pub = events.EventPublisher()
    # Initial publish
    ok_before = await live_pub.publish("test.recovery", {"status": "before_kill"})
    assert ok_before, "Initial publish should succeed"

    # Abruptly close the underlying TCP connection without informing publisher object
    if live_pub._connection and not live_pub._connection.is_closed:
        await live_pub._connection.close()

    # Next publish: Should detect closed connection, reconnect automatically, and succeed
    t0 = time.perf_counter()
    ok_after = await live_pub.publish("test.recovery", {"status": "after_kill"})
    reconnect_time = (time.perf_counter() - t0) * 1000
    print(f"  • Connection abrupt close -> Auto-reconnect: success={ok_after} in {reconnect_time:.2f}ms")
    results.append(("Connection Auto-Recovery after Abrupt Reset", ok_after, f"{reconnect_time:.2f}ms"))

    # =========================================================================
    # 4. Queue x-max-length Overflow Protection
    # =========================================================================
    print("\n--- 4. Testing Queue x-max-length Overflow Drop Behavior ---")
    small_q_name = "q.test.max_length_bound"
    small_q = await channel.declare_queue(
        small_q_name,
        durable=False,
        arguments={"x-max-length": 50, "x-overflow": "drop-head"},
        auto_delete=True
    )
    # Push 150 messages
    for i in range(150):
        await channel.default_exchange.publish(
            aio_pika.Message(body=f"msg-{i}".encode("utf-8")),
            routing_key=small_q_name
        )

    # Check queue message count
    await small_q.declare()
    msg_count = small_q.declaration_result.message_count
    # Should be capped at 50!
    is_capped = msg_count == 50

    # Read first message: with drop-head, oldest 100 were dropped, so first is msg-100
    first_msg = await small_q.get(no_ack=True)
    first_body = first_msg.body.decode("utf-8") if first_msg else ""
    dropped_head_correctly = (first_body == "msg-100")

    print(f"  • Queue capped at max length: count={msg_count}/50 (is_capped={is_capped})")
    print(f"  • Head message after 150 pushes: {first_body} (expected msg-100: {dropped_head_correctly})")
    max_length_ok = is_capped and dropped_head_correctly
    results.append(("Queue x-max-length Overflow Drop-Head", max_length_ok, f"capped at {msg_count}"))

    await channel.queue_delete(small_q_name)

    # =========================================================================
    # 5. Reality Check: BE/.env (localhost) vs 127.0.0.1 on Windows Host
    # =========================================================================
    print("\n--- 5. Reality Check: Hostname Resolution (BE/.env 'localhost' vs '127.0.0.1') ---")
    env_url = config.ENV_CONFIGURED_URL  # from BE/.env with localhost
    t0 = time.perf_counter()
    localhost_connected = False
    try:
        c = await aio_pika.connect(env_url, timeout=2)
        await c.close()
        localhost_connected = True
    except Exception as e:
        localhost_error = type(e).__name__
    elapsed_localhost = time.perf_counter() - t0

    print(f"  • Connecting with BE/.env value ({events.masked(env_url)}):")
    if localhost_connected:
        print(f"    -> Connected in {elapsed_localhost:.3f}s")
    else:
        print(f"    -> FAILED with {localhost_error} in {elapsed_localhost:.3f}s (IPv6 loopback mismatch on Windows)")

    results.append((
        "Host IPv6/IPv4 Resolution Reality Check",
        localhost_connected,
        f"{'Connected' if localhost_connected else 'FAIL: ' + localhost_error + ' (localhost -> ::1 mismatch)'}"
    ))

    await connection.close()

    print("\n" + "=" * 80)
    print("TEST 04 SUMMARY:")
    for name, status, detail in results:
        print(f"  - {name:<45}: {'PASS' if status else 'FAIL'} ({detail})")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_chaos_and_limits())
