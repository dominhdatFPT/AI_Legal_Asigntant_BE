from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests_3layer.layer2_cross_service.db_bootstrap import connect

ROLES = ("anon", "authenticated")


def act_as(db, role, subject):
    db.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
    db.execute("SELECT set_config('request.jwt.claim.role', %s, true)", (role,))
    db.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(subject) if role != "anon" else "",))
    assert db.execute("SELECT current_user, auth.uid()").fetchone() == (
        role, subject if role != "anon" else None,
    )


@pytest.mark.parametrize("database", ["procedure_db", "lawyer_db", "user_db"])
@pytest.mark.parametrize("role", ROLES)
def test_event_outbox_rejects_each_sql_verb(database, role):
    eid = f"rls-{uuid4().hex}"
    with connect(database) as db:
        try:
            db.execute("INSERT INTO public.event_outbox (id, routing_key, payload_json) VALUES (%s, 'test.rls', '{}')",
                       (eid,))
            for query, args in (
                ("SELECT id FROM public.event_outbox WHERE id = %s", (eid,)),
                ("INSERT INTO public.event_outbox (id, routing_key, payload_json) VALUES (%s, 'test.rls', '{}')",
                 (f"rls-{uuid4().hex}",)),
                ("UPDATE public.event_outbox SET attempts = 1 WHERE id = %s", (eid,)),
                ("DELETE FROM public.event_outbox WHERE id = %s", (eid,)),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                    act_as(db, role, uuid4())
                    db.execute(query, args)
            assert db.execute("SELECT attempts FROM public.event_outbox WHERE id = %s", (eid,)).fetchone() == (0,)
        finally:
            db.rollback()


@pytest.mark.parametrize("database,table,columns,values,mutable,cast_ids", [
    ("user_db", "users", "id,email,full_name", "%s,%s,'private user'", "full_name", False),
    ("procedure_db", "land_procedure_records", "id,user_id,procedure_code", "%s,%s,'DD-001'",
     "procedure_code", False),
    ("contract_db", "contract_templates", "id,owner_user_id,kind,name", "%s,%s,'personal','private template'",
     "name", False),
    ("document_db", "user_files",
     "id,owner_user_id,purpose,document_type,original_filename,media_type,uploaded_size_bytes,uploaded_sha256,"
     "storage_key,idempotency_key,request_hash",
     "%s,%s,'supporting_document','other','private.pdf','application/pdf',1,repeat('a',64),"
     "gen_random_uuid()::text,gen_random_uuid(),repeat('b',64)", "original_filename", False),
    ("agent_db", "conversations", "id,user_id,title", "%s,%s,'private conversation'", "title", False),
    ("notification_db", "notifications", "id,user_id,event_type,title,message",
     "%s,%s,'test.rls','private notification','private data'", "title", True),
])
@pytest.mark.parametrize("role", ROLES)
def test_browser_role_cannot_read_or_modify_another_users_rows(database, table, columns, values, mutable,
                                                            cast_ids, role):
    owner, outsider, row_id, second_row = uuid4(), uuid4(), uuid4(), uuid4()
    relation = sql.Identifier("public", table)
    field = sql.Identifier(mutable)
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        relation, sql.SQL(columns), sql.SQL(values),
    )

    def record_args(identity):
        if table == "users":
            return identity, f"{identity}@example.test"
        if cast_ids:
            return str(identity), str(owner)
        return identity, owner

    def denied(db, query, params):
        try:
            with db.transaction(force_rollback=True):
                act_as(db, role, outsider)
                result = db.execute(query, params).fetchall()
                assert result == [], (database, table, role, result)
        except psycopg.errors.InsufficientPrivilege:
            return

    with connect(database) as db:
        try:
            db.execute(insert, record_args(row_id))
            key = str(row_id) if cast_ids else row_id
            select = sql.SQL("SELECT {} FROM {} WHERE id = %s").format(field, relation)
            baseline = db.execute(select, (key,)).fetchall()
            assert len(baseline) == 1
            assert db.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
                              (f"public.{table}",)).fetchone() == (True,)
            for query, params in (
                (select, (key,)),
                (insert, record_args(second_row)),
                (sql.SQL("UPDATE {} SET {} = 'tampered' WHERE id = %s RETURNING id").format(relation, field),
                 (key,)),
                (sql.SQL("DELETE FROM {} WHERE id = %s RETURNING id").format(relation), (key,)),
            ):
                denied(db, query, params)
            assert db.execute(select, (key,)).fetchall() == baseline
        finally:
            db.rollback()
