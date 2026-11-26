import os
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pika
import pytest

from tests_3layer.layer2_cross_service.db_bootstrap import connect, database_url
from tests_3layer.support import (
    BE, INTERNAL_TOKEN, LAWYER_TOKEN, L2_AMQP, L2_REDIS, create_keys, eventually,
)

HEALTH = {
    "consultation": "consultations", "contract": "contracts", "lawyer": "lawyers",
    "notification": "notifications", "procedure": "procedures", "user": "users",
}


def start_uvicorn():
    if os.getenv("LEGAL_TEST_PAUSE_PROCEDURE_RELAY") == "1":
        from app.core.event_outbox import relay

        async def park_until_shutdown():
            import asyncio
            while not relay._stopping:
                await asyncio.sleep(0.05)

        relay.run_forever = park_until_shutdown
    if trace := os.getenv("LEGAL_TEST_CACHE_TRACE"):
        import json
        from app.services import user_client
        original = user_client.forget_user

        def size(subject):
            with user_client._cache_lock:
                return sum(uid == subject for _, uid in user_client._active_cache.values())

        def observe(subject):
            before = size(subject)
            original(subject)
            with Path(trace).open("a", encoding="utf-8") as log:
                log.write(json.dumps({"user": subject, "before": before, "after": size(subject)}) + "\n")

        user_client.forget_user = observe
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]))


def seed_user(port, *, role="citizen"):
    user_id = uuid4()
    with connect("user_db", port) as db:
        db.execute("INSERT INTO public.users (id, email, full_name, role) VALUES (%s, %s, %s, %s)",
                   (user_id, f"{user_id}@example.test", "Synthetic test user", role))
    return user_id


def rows(database, query, params=()):
    with connect(database) as db:
        return db.execute(query, params).fetchall()


def require_consumer(queue):
    params = pika.URLParameters(L2_AMQP)
    params.socket_timeout = params.blocked_connection_timeout = 3
    connection = pika.BlockingConnection(params)
    try:
        def ready():
            with connection.channel() as channel:
                try:
                    return channel.queue_declare(queue=queue, passive=True).method.consumer_count
                except pika.exceptions.ChannelClosedByBroker as error:
                    if error.reply_code != 404:
                        raise
                    return False
        eventually(ready, description=f"RabbitMQ consumer for {queue}", timeout=12)
    finally:
        connection.close()


class LiveServices:
    def __init__(self, path):
        self.path = path
        self.keys = path / "keys"
        create_keys(self.keys)
        self.processes = {}
        # Reserve all ports together to avoid duplicate assignment.
        sockets = [socket.socket() for _ in HEALTH]
        try:
            for sock in sockets:
                sock.bind(("127.0.0.1", 0))
            self.ports = dict(zip(HEALTH, (sock.getsockname()[1] for sock in sockets), strict=True))
        finally:
            for sock in sockets:
                sock.close()

    def url(self, service):
        return f"http://127.0.0.1:{self.ports[service]}"

    def start(self, service, **overrides):
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {
                "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                "COMSPEC", "SYSTEMDRIVE", "APPDATA", "LOCALAPPDATA",
                "USERPROFILE", "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
            }
        }
        env.update({
            "PYTHONPATH": os.pathsep.join(map(str, (
                BE / "services" / f"{service}-service", BE / "libs" / "common_auth", BE,
            ))),
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PROCEDURE_SKIP_DOTENV": "1",
            f"{service.upper()}_DATABASE_URL": database_url(f"{service}_db"),
            "JWT_PUBLIC_KEY_PATH": str(self.keys / "jwt_public.pem"),
            "INTERNAL_TOKEN": INTERNAL_TOKEN,
            "LAWYER_SERVICE_TOKEN": LAWYER_TOKEN,
            "RABBITMQ_URL": L2_AMQP,
            "RABBITMQ_ENABLED": "true",
            "OUTBOX_ENABLED": "true",
            "OUTBOX_POLL_SECONDS": "0.25",
            "USER_ACTIVE_CACHE_SECONDS": "30",
            "LLM_BYPASS": "true",
            "RELEVANCE_CHECK": "false",
            "REDIS_URL": L2_REDIS,
        })
        env.update({f"{name.upper()}_SERVICE_URL": self.url(name) for name in self.ports})
        if service == "user":
            env["JWT_PRIVATE_KEY_PATH"] = str(self.keys / "jwt_private.pem")
        env.update(overrides)
        log = self.path / f"{service}.log"
        real_exe = str(Path(sys.executable).resolve())
        with log.open("wb") as output:
            process = subprocess.Popen(
                [real_exe, "-m", "tests_3layer.layer2_cross_service.live", str(self.ports[service])],
                cwd=self.path, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            )
        self.processes[service] = (process, log)

        def healthy():
            if process.poll() is not None:
                raise AssertionError(f"{service} exited {process.returncode}\n{log.read_text(errors='replace')}")
            try:
                return client.get(f"/api/v1/{HEALTH[service]}/health").status_code == 200
            except httpx.TransportError:
                return False

        with self.client(service, timeout=2) as client:
            eventually(healthy, description=f"{service} startup", timeout=35)

    def client(self, service, **kwargs):
        kwargs.setdefault("timeout", 30)
        return httpx.Client(base_url=self.url(service), trust_env=False, **kwargs)

    def close(self):
        for process, _ in self.processes.values():
            if process.poll() is None:
                process.terminate()
        for process, log in self.processes.values():
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            print(f"--- {log.name} ---\n" + "\n".join(log.read_text(errors="replace").splitlines()[-35:]))


@pytest.fixture
def live_services(tmp_path):
    services = LiveServices(tmp_path)
    try:
        yield services
    finally:
        services.close()


if __name__ == "__main__":
    start_uvicorn()
