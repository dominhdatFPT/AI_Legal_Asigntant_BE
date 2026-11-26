"""Remove Contract-owned reference data after a procedure is purged."""

import asyncio
import logging
from uuid import UUID

from common_auth import events

logger = logging.getLogger(__name__)
QUEUE, ROUTING_KEY = events.PROCEDURE_PURGE_QUEUE, "procedure.purged"


def lock_procedure_keys(conn, keys) -> None:
    for key in sorted({key for key in keys if isinstance(key, str) and key}):
        conn.execute("SELECT pg_advisory_xact_lock(7202028, hashtext(%s))", (key,))


def purge_reference_data(payload: dict) -> dict:
    from app.repositories.reference_uploads import connection

    if not isinstance(payload, dict):
        raise ValueError("Procedure purge event payload must be an object")
    raw_keys = payload.get("keys")
    keys = [key for key in raw_keys if isinstance(key, str) and key] if isinstance(raw_keys, list) else []
    code, procedure_id, identifier = (
        value if isinstance(value, str) and value else None
        for value in (payload.get("code"), payload.get("procedure_id"), payload.get("identifier"))
    )
    if not any((keys, code, procedure_id, identifier)):
        raise ValueError("Procedure purge event has no identifier")
    try:
        classification_id = str(UUID(procedure_id)) if procedure_id else None
    except ValueError:
        # Old orphan records can have a textual procedure identifier.
        classification_id = None
    procedure_id = classification_id or procedure_id
    keys = sorted(set(keys).union(value for value in (code, identifier) if value))
    with connection() as conn:
        lock_procedure_keys(conn, keys)
        uploads = conn.execute("""
            DELETE FROM public.contract_reference_uploads AS u
             WHERE u.metadata->>'procedure_id' = %s
                OR (NULLIF(u.metadata->>'procedure_id', '') IS NULL
                    AND (u.procedure_key = ANY(%s) OR CAST(u.id AS text) = %s
                         OR u.metadata->>'procedure' = ANY(%s)
                         OR u.metadata->>'procedure_code' = ANY(%s))
                    AND NOT EXISTS (
                        SELECT 1 FROM public.reference_classifications owner
                         WHERE owner.procedure_id IS NOT NULL
                           AND CAST(owner.procedure_id AS text) IS DISTINCT FROM %s
                           AND (owner.key IN (u.procedure_key, u.metadata->>'procedure', u.metadata->>'procedure_code')
                                OR owner.code IN (u.procedure_key, u.metadata->>'procedure', u.metadata->>'procedure_code'))
                    ));
        """, (procedure_id, keys, identifier, keys, keys, procedure_id)).rowcount
        classifications = conn.execute("""
            DELETE FROM public.reference_classifications AS rc
             WHERE rc.kind = 'project_procedure'
               AND (rc.procedure_id = %s::uuid
                    OR (rc.procedure_id IS NULL AND (rc.code = ANY(%s) OR rc.key = ANY(%s))))
               AND NOT EXISTS (SELECT 1 FROM public.contract_reference_uploads u
                                WHERE u.metadata->>'procedure_id' = CAST(rc.procedure_id AS text)
                                   OR ((rc.procedure_id IS NULL OR NULLIF(u.metadata->>'procedure_id', '') IS NULL)
                                       AND (u.procedure_key IN (rc.key, rc.code)
                                            OR u.metadata->>'procedure' IN (rc.key, rc.code)
                                            OR u.metadata->>'procedure_code' IN (rc.key, rc.code))));
        """, (classification_id, keys, keys)).rowcount
    return {"uploads": uploads, "classifications": classifications}


async def handle_procedure_event(routing_key: str, event: dict) -> None:
    if routing_key != ROUTING_KEY:
        return
    counts = await asyncio.to_thread(purge_reference_data, event["payload"])
    from app.api import reference_catalog
    reference_catalog._invalidate_cache()
    logger.info("Procedure purge event %s removed %d uploads and %d classifications",
                event.get("event_id"), counts["uploads"], counts["classifications"])


async def start() -> None:
    await events.consume_forever(QUEUE, [ROUTING_KEY], handle_procedure_event)
