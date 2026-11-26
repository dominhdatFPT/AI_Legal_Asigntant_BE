"""Measurements against the local, disposable performance stack."""

import base64
import json
import math
import os
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests_3layer.layer2_cross_service.db_bootstrap import E2E_PORT, check_identity, connect

HERE = Path(__file__).resolve().parent
BE = HERE.parents[1]
BUDGETS = json.loads((HERE / "budgets.json").read_text(encoding="utf-8"))
GATEWAY = BUDGETS["load"]["origin"]
ORIGIN = BUDGETS["browser"]["origin"]
PROJECT = "legal-test-e2e"
COMPOSE = BE / "docker-compose.test-e2e.yml"
# Render RAM budgets apply to app processes only; postgres, redis and rabbitmq are reported, not gated.
APP_SERVICES = frozenset(BUDGETS["resources"]["app_services"])


def command(*args, timeout=30, check=True, **kwargs):
    return subprocess.run(
        [str(arg) for arg in args], stdin=subprocess.DEVNULL, capture_output=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=check, **kwargs,
    )


def docker(*args, timeout=30):
    result = command(os.environ["LEGAL_PERF_DOCKER"], *args, timeout=timeout, check=False)
    assert result.returncode == 0, f"Docker {args[0]} failed: {result.stderr.strip()}"
    return result.stdout


def compose(*args, timeout=30):
    return docker(
        "compose", "--env-file", os.environ["LEGAL_TEST_COMPOSE_ENV_FILE"],
        "-p", PROJECT, "-f", COMPOSE, *args, timeout=timeout,
    )


def compose_exec(service, *args):
    return compose("exec", "-T", service, *args)


def compose_services():
    return compose("config", "--services").splitlines()


def preflight():
    limits = BUDGETS["preflight"]
    for key in ("LEGAL_PERF_DOCKER", "LEGAL_PERF_NODE", "LEGAL_PERF_K6", "K6_BROWSER_EXECUTABLE_PATH"):
        assert Path(os.environ.get(key, "")).is_file(), f"Set {key} to an installed executable"
    capacity = json.loads(docker("info", "--format", "{{json .}}"))
    assert capacity["NCPU"] >= limits["cpus"], f"Docker needs {limits['cpus']} CPUs"
    assert capacity["MemTotal"] >= limits["ram_bytes"], f"Docker needs {limits['ram_bytes']} RAM bytes"
    try:
        with socket.socket() as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 3000))
    except OSError as error:
        raise AssertionError("Free loopback port 3000 before running Layer 3 performance tests") from error


def quiet_check():
    limits = BUDGETS["preflight"]
    containers = compose("ps", "-q").splitlines()
    assert len(containers) == len(compose_services()), "Expected every Layer 3 container to be running"
    deadline = time.monotonic() + limits["quiet_timeout_seconds"]
    quiet_since = None
    latest = []
    while time.monotonic() < deadline:
        try:
            latest = docker(
                "stats", "--no-stream", "--format", "{{.Name}}={{.CPUPerc}}", *containers,
                timeout=max(5, deadline - time.monotonic()),
            ).splitlines()
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"Layer 3 containers did not become quiet: docker stats timed out. Last CPU samples: {latest}"
            ) from None
        assert len(latest) == len(containers), "Missing CPU samples from Docker stats"
        now = time.monotonic()
        if all(float(value.rpartition("=")[2].removesuffix("%")) < limits["quiet_pct"] for value in latest):
            quiet_since = quiet_since if quiet_since is not None else now
            if now - quiet_since >= limits["quiet_seconds"]:
                return
        else:
            quiet_since = None
    raise AssertionError(f"Layer 3 containers did not become quiet. Last CPU samples: {latest}")


def check_test_cluster():
    with connect("user_db", E2E_PORT) as db:
        check_identity(db, E2E_PORT)
        db.execute("SELECT 1 FROM pg_stat_statements LIMIT 1").fetchall()


def pg_calls(database, pattern, tables):
    # The extension's SQL view is installed in user_db by the shared bootstrap.
    # Its statistics come from shared memory and include every DB on this server.
    # `tables` keeps healthchecks, pool pre-pings and unrelated statements out of the count.
    with connect("user_db", E2E_PORT) as db:
        return db.execute("""
            SELECT COALESCE(sum(s.calls), 0)::bigint
            FROM pg_stat_statements s
            JOIN pg_database d ON d.oid = s.dbid
            WHERE d.datname = %s AND s.toplevel
              AND s.query ~* %s AND s.query ~* %s AND s.query !~* 'outbox|pg_stat_statements'
            """, (database, pattern, tables)).fetchone()[0]


def query_delta(database, pattern, tables, request, *, control=True):
    before = pg_calls(database, pattern, tables)
    start = time.perf_counter()
    result = request()
    elapsed = time.perf_counter() - start
    after = pg_calls(database, pattern, tables)
    background = 0
    if control:
        time.sleep(elapsed)
        background = pg_calls(database, pattern, tables) - after
    assert after >= before and background >= 0, "pg_stat_statements counters reset during measurement"
    return result, max(0, after - before - background), elapsed * 1000


class ConnSampler:
    """Sample service sessions from a separate database so the probe is excluded."""

    def __init__(self):
        self.peaks = dict.fromkeys(BUDGETS["resources"]["connections"], 0)
        self.api_peaks = dict.fromkeys(BUDGETS["resources"]["api_connections_with_workers"], 0)
        self.samples = 0
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        try:
            with connect("postgres", E2E_PORT, autocommit=True, options="-c statement_timeout=3000") as db:
                while not self.stop.is_set():
                    started = time.monotonic()
                    workers = BUDGETS["resources"]["worker_application_names"]
                    rows = db.execute("""
                        SELECT datname, count(*), count(*) FILTER (WHERE application_name <> ALL(%s))
                        FROM pg_stat_activity
                        WHERE datname = ANY(%s) AND backend_type = 'client backend'
                        GROUP BY datname
                        """, (list(workers.values()), list(self.peaks))).fetchall()
                    for name, count, api_count in rows:
                        self.peaks[name] = max(self.peaks[name], count)
                        if name in self.api_peaks:
                            self.api_peaks[name] = max(self.api_peaks[name], api_count)
                    self.samples += 1
                    self.ready.set()
                    interval = BUDGETS["resources"]["connection_sample_ms"] / 1000
                    self.stop.wait(max(0, interval - (time.monotonic() - started)))
        except Exception as error:
            self.error = error
        finally:
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(10), "PostgreSQL sampler did not start"
        if self.error:
            raise self.error
        assert self.samples, "PostgreSQL sampler produced no samples"
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(10)
        assert not self.thread.is_alive(), "PostgreSQL sampler did not stop"
        if self.error and not exc[0]:
            raise self.error


def cgroup(service):
    lines = compose_exec(
        service, "cat", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.peak",
        "/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/memory.stat",
    ).splitlines()
    at = time.monotonic()
    assert len(lines) >= 3, f"{service}: expected cgroup v2 memory.current, memory.peak, cpu.stat and memory.stat"
    # cpu.stat and memory.stat use disjoint keys, so one dict holds both.
    stats = dict(line.split(maxsplit=1) for line in lines[2:])
    assert {"usage_usec", "anon", "inactive_file"} <= stats.keys(), f"{service}: incomplete cgroup v2 stats"
    current = int(lines[0])
    return {
        "current": current, "peak": int(lines[1]), "cpu_usec": int(stats["usage_usec"]), "at": at,
        # Same working-set definition as cAdvisor/kubelet: reclaimable page cache excluded.
        "working_set": max(0, current - int(stats["inactive_file"])), "anon": int(stats["anon"]),
    }


def cgroups(services=None):
    names = compose_services() if services is None else list(services)
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(zip(names, pool.map(cgroup, names)))


def redis_info():
    output = compose_exec("redis", "redis-cli", "INFO")
    return {
        name: int(value) for name, _, value in (line.partition(":") for line in output.splitlines())
        if name in {"used_memory", "used_memory_peak", "evicted_keys"}
    }


def run_k6(script, env, *, summary_path, timeout):
    child_env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("K6_", "PERF_"))
    }
    browser_run = script == "browser_pages.js"
    args = []
    console_path = summary_path.with_suffix(".console.log")
    if browser_run:
        child_env.update({
            "K6_BROWSER_EXECUTABLE_PATH": os.environ["K6_BROWSER_EXECUTABLE_PATH"],
            "K6_BROWSER_HEADLESS": "true",
            # host-resolver-rules=MAP * ~NOTFOUND also fails 127.0.0.1 in Chromium;
            # routing external origins to an unreachable local proxy with localhost bypass keeps 127.0.0.1 fast while blocking outbound hosts.
            "K6_BROWSER_ARGS": "proxy-server=http://127.0.0.1:1,proxy-bypass-list=127.0.0.1;localhost",
        })
        args = ["--console-output", console_path]
    result = command(
        os.environ["LEGAL_PERF_K6"], "run", "--no-color", "--quiet", *args, HERE / "k6" / script,
        env={**child_env, **env, "PERF_SUMMARY": str(summary_path)}, cwd=HERE, timeout=timeout, check=False,
    )
    summary_path.with_suffix(".log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert summary_path.is_file(), f"k6 exit={result.returncode}: {result.stderr[-5000:]}"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if browser_run:
        summary["pages"] = []
        summary["failures"] = []
        if console_path.is_file():
            for line in console_path.read_text(encoding="utf-8").splitlines():
                # Browser VU globals are unavailable in the handleSummary runtime.
                match = re.search(r"PERF_BROWSER_PAYLOAD ([A-Za-z0-9+/=]+)", line)
                if match:
                    row = json.loads(base64.b64decode(match[1], validate=True))
                    summary[row["kind"]].append(row["data"])
    return result.returncode, summary


def percentile(samples, pct):
    assert samples, "Cannot measure a percentile without samples"
    return sorted(samples)[math.ceil(len(samples) * pct / 100) - 1]


if __name__ == "__main__":
    preflight()
