"""Master Runner for all RabbitMQ Reality Check Tests."""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPTS = [
    ("Test 01: Payloads, Routing & Security Anti-Phishing", "test_01_payloads_and_routing.py"),
    ("Test 02: Components Data Flow & DLQ Routing", "test_02_components_dataflow.py"),
    ("Test 03: Concurrency, Load & Drain Benchmark", "test_03_load_and_concurrency.py"),
    ("Test 04: Chaos, Fault Tolerance & 10,000 Burst", "test_04_chaos_and_limits.py"),
    ("Test 05: Deep Audit of Latent Bugs & Bottlenecks", "test_05_deep_audit_bugs.py"),
]


def run_all():
    cur_dir = Path(__file__).parent.resolve()
    print("=" * 85)
    print("LEGAL ASSISTANT AI — RABBITMQ REALITY CHECK MASTER TEST SUITE")
    print("=" * 85)

    summary = []
    t_master_start = time.perf_counter()

    for title, script_name in SCRIPTS:
        script_path = cur_dir / script_name
        print(f"\n>>> EXECUTING: {title} ({script_name})...")
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, encoding="utf-8")
        elapsed = time.perf_counter() - t0

        print(proc.stdout)
        if proc.stderr:
            print(f"[STDERR]\n{proc.stderr}")

        success = proc.returncode == 0
        summary.append((title, success, f"{elapsed:.2f}s", proc.returncode))

    total_elapsed = time.perf_counter() - t_master_start

    print("\n" + "=" * 85)
    print("OVERALL REALITY CHECK SUMMARY:")
    print("=" * 85)
    for title, success, duration, code in summary:
        status_str = "PASS" if success else f"FAIL (Exit code {code})"
        print(f"  • {title:<55}: {status_str} in {duration}")
    print("-" * 85)
    print(f"Total Suite Runtime: {total_elapsed:.2f}s")
    print("=" * 85)


if __name__ == "__main__":
    run_all()
