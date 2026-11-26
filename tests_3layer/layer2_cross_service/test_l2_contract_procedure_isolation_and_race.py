import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

from sqlalchemy import MetaData, create_engine

from common_auth import events, outbox
from tests_3layer.layer2_cross_service.db_bootstrap import INFRA_PORT, database_url
from tests_3layer.layer2_cross_service.live import live_services, require_consumer, rows, seed_user
from tests_3layer.support import auth_headers, eventually, reference_pdf, response_data

CATALOG = "/api/v1/contracts/reference-catalog"


def start_pair(services, *, pause=False):
    admin = seed_user(INFRA_PORT, role="admin")
    services.start("user")
    services.start("procedure", LEGAL_TEST_PAUSE_PROCEDURE_RELAY="1" if pause else "0")
    services.start("contract", PROCEDURE_DATABASE_URL="", PROCEDURE_WRITE_MODE="auto")
    return auth_headers(services.keys, admin, "admin")


def upload(client, auth, name, **custom_fields):
    return client.post(CATALOG, headers=auth, data={"name": name, "domain": "land", **custom_fields},
                       files={"file": ("test.pdf", reference_pdf(name), "application/pdf")})


def test_eight_real_registrations_allocate_eight_distinct_codes(live_services):
    auth = start_pair(live_services)
    barrier = Barrier(8, timeout=10)
    suffix = uuid4().hex

    def submit(index):
        with live_services.client("contract") as client:
            barrier.wait()
            return upload(client, auth, f"Concurrent {suffix} case {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(submit, range(8), timeout=45))
    assert [reply.status_code for reply in replies] == [201] * 8, [
        (reply.status_code, reply.text) for reply in replies
    ]
    items = [response_data(response, 201) for response in replies]
    codes = {item["procedure_code"] for item in items}
    assert len(codes) == 8 and all(re.fullmatch(r"DD-\d{3}", code) for code in codes)
    persisted = rows("procedure_db", "SELECT id, code FROM public.procedures WHERE code = ANY(%s)", (list(codes),))
    assert set(persisted) == {(UUID(item["procedure_id"]), item["procedure_code"]) for item in items}


def test_invalid_form_template_rolls_back_registration(live_services):
    auth = start_pair(live_services)
    name = f"Missing template {uuid4().hex}"
    with live_services.client("contract") as client:
        response = upload(client, auth, name, form_template_id=str(uuid4()))
    assert response.status_code in {404, 422}, response.text
    assert response.json()["error"]["code"] in {"FORM_TEMPLATE_UNAVAILABLE", "PROCEDURE_NOT_FOUND"}
    assert rows("procedure_db", "SELECT id FROM public.procedures WHERE name = %s", (name,)) == []


def test_purge_redelivery_cannot_delete_reused_code(live_services):
    auth = start_pair(live_services, pause=True)
    require_consumer(events.PROCEDURE_PURGE_QUEUE)
    with live_services.client("contract") as contract, live_services.client("procedure") as procedure:
        old = response_data(upload(contract, auth, "Old registration", code="DD-099"), 201)
        old_id = old["procedure_id"]
        deleted = procedure.delete(f"/api/v1/procedures/admin/{old_id}", headers=auth)
        assert deleted.status_code == 200, deleted.text
        event_id, published = rows(
            "procedure_db", "SELECT id, published_at FROM public.event_outbox "
            "WHERE routing_key = 'procedure.purged' AND payload_json::jsonb->'payload'->>'procedure_id' = %s",
            (old_id,),
        )[0]
        assert published is None
        new = response_data(upload(contract, auth, "New registration", code="DD-099"), 201)
    assert new["procedure_id"] != old_id and new["procedure_code"] == old["procedure_code"] == "DD-099"

    def uploads():
        return rows(
            "contract_db",
            "SELECT id, metadata, file_data, page_images FROM public.contract_reference_uploads "
            "WHERE procedure_key = 'DD-099' ORDER BY id",
        )

    before = {str(row[0]): row for row in uploads()}
    assert set(before) == {old["id"], new["id"]}
    assert before[new["id"]][2] == reference_pdf("New registration")
    assert before[new["id"]][3][0].startswith(b"\x89PNG\r\n\x1a\n")

    url = database_url("procedure_db").replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    table = outbox.define_table(MetaData(), schema="public")

    async def deliver():
        transport = outbox.RabbitTransport(target_queues={"procedure.purged": events.PROCEDURE_PURGE_QUEUE})
        relay = outbox.OutboxRelay(lambda: engine, table, transport)
        try:
            assert await relay.run_once() == 1
        finally:
            await transport.close()

    try:
        asyncio.run(deliver())
    finally:
        engine.dispose()

    eventually(lambda: all(str(row[0]) != old["id"] for row in uploads()), description="old upload purge")
    assert uploads() == [before[new["id"]]]
    assert rows("contract_db", "SELECT procedure_id, name, kind FROM public.reference_classifications "
                "WHERE code = 'DD-099'") == [(UUID(new["procedure_id"]), "New registration", "project_procedure")]
    assert rows("procedure_db", "SELECT published_at IS NOT NULL FROM public.event_outbox WHERE id = %s",
                (event_id,)) == [(True,)]
