"""Task 5: disposable SQL, JWT, user confirmation, CAS/races, legacy preservation.

Only synthetic identity data. No cloud/Storage writes, no real user keys or CCCD.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
import threading
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from fastapi.testclient import TestClient

from test_document_foundation import foundation, config_env
from test_document_upload import upload_database, MemoryStorage, SERVICE


def fields():
    return {"full_name":"NGUYỄN VĂN AN", "identity_number":"000000000001", "date_of_birth":"2000-01-01",
            "issue_date":"2020-01-01", "issue_place":"TEST AUTHORITY", "permanent_address":"TEST ADDRESS"}


def add_document(conn, owner, *, status="completed", identity=True):
    document, job = uuid4(), uuid4()
    conn.execute("INSERT INTO user_files (id,owner_user_id,purpose,document_type,original_filename,media_type,"
                 "uploaded_size_bytes,uploaded_sha256,storage_key,idempotency_key,request_hash) "
                 "VALUES (%s,%s,'supporting_document',%s,'synthetic.png','image/png',64,%s,%s,%s,%s)",
                 (document,owner,"identity_card" if identity else "other","a"*64,
                  f"quarantine/{owner}/{document}/source",uuid4(),"b"*64))
    conn.execute("INSERT INTO document_processing_jobs (id,document_id) VALUES (%s,%s)",(job,document))
    result=None
    if status in {"completed","failed"}:
        conn.execute("UPDATE user_files SET file_status='available',size_bytes=64,sha256=%s WHERE id=%s",("a"*64,document))
        if status=="completed":
            result=uuid4()
            conn.execute("INSERT INTO document_extraction_results (id,job_id,source_sha256,source_kind,engine_version) "
                         "VALUES (%s,%s,%s,'image','synthetic-test')",(result,job,"a"*64))
        conn.execute("UPDATE document_processing_jobs SET status=%s,completed_at=now(),error_code=%s WHERE id=%s",
                     (status,"OCR_FAILED" if status=="failed" else None,job))
    return document,result


@pytest.fixture(scope="session")
def profile_database(upload_database):
    legacy_owner=str(uuid4()); legacy_document=None
    with psycopg.connect(upload_database,autocommit=True) as conn:
        if not conn.execute("SELECT to_regclass('public.document_identity_ocr_sessions')").fetchone()[0]:
            conn.execute((SERVICE/"database/003_identity_ocr_sessions.sql").read_text(encoding="utf-8"))
        exists=conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                            "AND table_name='user_identity_profiles' AND column_name='source_session_id'").fetchone()
        if not exists:
            with conn.transaction():
                legacy_document, result=add_document(conn,legacy_owner)
                conn.execute("INSERT INTO user_identity_profiles (owner_user_id,source_document_id,source_result_id,full_name,confirmed_at) "
                             "VALUES (%s,%s,%s,'BEFORE MIGRATION',now())",(legacy_owner,legacy_document,result))
            conn.execute((SERVICE/"database/004_identity_profile_session.sql").read_text(encoding="utf-8"))
    return upload_database,legacy_owner,legacy_document


@pytest.fixture
def profiles(foundation,config_env,profile_database):
    from app.repositories.identity_profile import IdentityProfile
    settings=replace(foundation[1].from_env(),database_url=profile_database[0])
    return IdentityProfile(foundation[5](settings))


@pytest.fixture
def source(profiles):
    def create(owner=None, status="completed"):
        owner=owner or str(uuid4()); session=uuid4(); prefix=f"quarantine/{owner}/identity/{session}/"
        with profiles.database.connection() as conn:
            conn.execute("INSERT INTO document_identity_ocr_sessions (id,owner_user_id,idempotency_key,request_hash,status,"
                "front_storage_key,back_storage_key,front_sha256,back_sha256,front_size_bytes,back_size_bytes,"
                "raw_front,raw_back,fields,error_code,error_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,64,65,%s,%s,%s,%s,%s)",
                (session,owner,uuid4(),"a"*64,status,prefix+"front.jpg",prefix+"back.jpg","b"*64,"c"*64,
                 Jsonb({"engine":"synthetic-test"}) if status=="completed" else None,
                 Jsonb({"engine":"synthetic-test"}) if status=="completed" else None,
                 Jsonb(fields()) if status=="completed" else None,
                 "OCR_FAILED" if status=="failed" else None,503 if status=="failed" else None))
        return owner,session
    return create


def body(session, revision=0, *, values=None):
    from app.schemas.identity_profile import ConfirmIdentityProfile
    return ConfirmIdentityProfile.model_validate({"source_session_id":str(session),"expected_revision":revision,"fields":values or fields()})


def test_migration_preserves_legacy_rows(profiles,profile_database):
    if profile_database[2] is None: pytest.skip("Legacy fixture only when applying fresh 004")
    row=profiles.get(profile_database[1])
    assert row["source_document_id"]==str(profile_database[2]) and row["source_session_id"] is None
    assert row["revision"]==1 and row["fields"]["full_name"]=="BEFORE MIGRATION"


def test_setup_verifier_read_only(profiles):
    spec=importlib.util.spec_from_file_location("task5_setup",SERVICE/"scripts/setup_identity_profile.py")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with profiles.database.connection(read_only=True) as conn:
        before=conn.execute("SELECT count(*) AS n FROM user_identity_profiles").fetchone()["n"]
        module.verify(conn)
        assert conn.execute("SELECT count(*) AS n FROM user_identity_profiles").fetchone()["n"]==before


def test_live_swagger_profile_body(foundation):
    from app.main import app
    schema=app.openapi()
    body_schema=schema["paths"]["/api/v1/documents/identity-profile/me"]["put"]["requestBody"]
    assert body_schema["required"]
    body_schema=body_schema["content"]["application/json"]["schema"]
    assert {"expected_revision","fields"} <= set(body_schema["required"])
    assert set(body_schema["properties"]["fields"]["required"])==set(fields())
    assert len(body_schema["oneOf"])==2 and "$defs" not in body_schema
    pytest.importorskip("openapi_spec_validator").validate(schema)


def test_confirm_manual_fields_not_raw_ocr_and_no_auto_profile(profiles,source,foundation):
    owner,session=source()
    with pytest.raises(foundation[3]) as caught: profiles.get(owner)
    assert caught.value.current_revision==0 and caught.value.code=="IDENTITY_PROFILE_NOT_FOUND"
    values=fields(); values["full_name"]="  USER CORRECTED NAME  "; values["issue_place"]=None
    row=profiles.confirm(owner,body(session,values=values))
    assert row["revision"]==1 and row["fields"]["full_name"]=="USER CORRECTED NAME" and row["fields"]["issue_place"] is None
    assert row["source_document_id"] is None and row["source_session_id"]==str(session)
    assert profiles.get(owner)==row
    with profiles.database.connection(read_only=True) as conn:
        raw=conn.execute("SELECT fields FROM document_identity_ocr_sessions WHERE id=%s",(session,)).fetchone()["fields"]
        assert raw==fields()
        assert not conn.execute("SELECT 1 FROM user_files WHERE owner_user_id=%s",(owner,)).fetchone()
    assert not {"raw_front","storage_key","source_result_id","status","deleted_at"}&set(row)


def test_replace_same_source_full_fields_and_stale_cas(profiles,source,foundation):
    owner,session=source(); profiles.confirm(owner,body(session))
    with pytest.raises(foundation[3]) as caught: profiles.confirm(owner,body(session))
    assert caught.value.code=="REVISION_CONFLICT" and caught.value.current_revision==1
    values={key:None for key in fields()}; values["full_name"]="ONLY CONFIRMED NAME"
    row=profiles.confirm(owner,body(session,1,values=values))
    assert row["revision"]==2 and row["fields"]==values


def test_replace_source_and_unlink_recreate_never_reset_revision(profiles,source,foundation):
    owner,first=source(); _,second=source(owner)
    profiles.confirm(owner,body(first)); row=profiles.confirm(owner,body(second,1))
    assert row["source_session_id"]==str(second) and row["revision"]==2
    profiles.unlink(owner,2); profiles.unlink(owner,2); profiles.unlink(owner,3)
    with pytest.raises(foundation[3]) as caught: profiles.get(owner)
    assert caught.value.current_revision==3
    with pytest.raises(foundation[3]) as caught: profiles.confirm(owner,body(first,0))
    assert caught.value.current_revision==3
    assert profiles.confirm(owner,body(first,3))["revision"]==4
    with pytest.raises(foundation[3]) as caught: profiles.unlink(owner,2)
    assert caught.value.code=="REVISION_CONFLICT" and profiles.get(owner)["revision"]==4


@pytest.mark.parametrize("status",["processing","failed"])
def test_source_must_be_completed_and_current_owner(profiles,source,foundation,status):
    owner,session=source(status=status)
    with pytest.raises(foundation[3]) as caught: profiles.confirm(owner,body(session))
    assert caught.value.code=="IDENTITY_SOURCE_NOT_READY"
    with pytest.raises(foundation[3]) as caught: profiles.confirm(str(uuid4()),body(session))
    assert caught.value.status==404 and caught.value.code=="IDENTITY_SOURCE_NOT_FOUND"


def test_nonexistent_source_and_profile_do_not_leak(profiles,foundation):
    owner=str(uuid4())
    with pytest.raises(foundation[3]) as caught: profiles.confirm(owner,body(uuid4()))
    assert caught.value.status==404
    with pytest.raises(foundation[3]) as caught: profiles.unlink(owner,1)
    assert caught.value.code=="IDENTITY_PROFILE_NOT_FOUND" and caught.value.current_revision==0


def test_document_branch_and_replacement_releases_old_file(profiles,source,foundation):
    from app.repositories.files import Files
    from app.schemas.identity_profile import ConfirmIdentityProfile
    owner=str(uuid4())
    with profiles.database.connection() as conn: document,result=add_document(conn,owner)
    payload=ConfirmIdentityProfile.model_validate({"source_document_id":str(document),"expected_revision":0,"fields":fields()})
    row=profiles.confirm(owner,payload)
    assert row["source_document_id"]==str(document) and row["source_session_id"] is None
    files=Files(profiles.database,MemoryStorage())
    with pytest.raises(foundation[3]) as caught: files.delete(owner,document)
    assert caught.value.code=="PROFILE_SOURCE_IN_USE"
    _,session=source(owner); profiles.confirm(owner,body(session,1))
    files.delete(owner,document)
    assert profiles.get(owner)["source_session_id"]==str(session)


@pytest.mark.parametrize("status,identity,expected",[("queued",True,409),("failed",True,409),("completed",False,422)])
def test_legacy_source_state_and_type(profiles,foundation,status,identity,expected):
    from app.schemas.identity_profile import ConfirmIdentityProfile
    owner=str(uuid4())
    with profiles.database.connection() as conn: document,_=add_document(conn,owner,status=status,identity=identity)
    payload=ConfirmIdentityProfile.model_validate({"source_document_id":str(document),"expected_revision":0,"fields":fields()})
    with pytest.raises(foundation[3]) as caught: profiles.confirm(owner,payload)
    assert caught.value.status==expected


def test_concurrent_first_confirm_and_update_delete_cas(profiles,source,foundation):
    owner,session=source()
    def attempt(fn):
        try: return fn(),None
        except foundation[3] as error: return None,error.code
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:attempt(lambda:profiles.confirm(owner,body(session))),range(2)))
    assert sum(error is None for _,error in results)==1
    assert [error for _,error in results if error]==["REVISION_CONFLICT"]
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(attempt,lambda:profiles.confirm(owner,body(session,1)))
        second=pool.submit(attempt,lambda:profiles.unlink(owner,1))
        results=[first.result(),second.result()]
    assert sum(error is None for _,error in results)==1
    assert [error for _,error in results if error]==["REVISION_CONFLICT"]


def test_confirm_holds_source_before_profile_and_blocks_file_delete(profiles,foundation,monkeypatch):
    from app.repositories.files import Files
    from app.schemas.identity_profile import ConfirmIdentityProfile
    owner=str(uuid4())
    with profiles.database.connection() as conn: document,_=add_document(conn,owner)
    payload=ConfirmIdentityProfile.model_validate({"source_document_id":str(document),"expected_revision":0,"fields":fields()})
    files=Files(profiles.database,MemoryStorage()); entered,release=threading.Event(),threading.Event(); original=profiles._lock
    def slow(*args,**kwargs):
        row=original(*args,**kwargs); entered.set(); assert release.wait(5); return row
    monkeypatch.setattr(profiles,"_lock",slow)
    with ThreadPoolExecutor(2) as pool:
        confirming=pool.submit(profiles.confirm,owner,payload)
        try:
            assert entered.wait(5)
            deleting=pool.submit(files.delete,owner,document)
            release.set()
            assert confirming.result()["revision"]==1
            with pytest.raises(foundation[3]) as caught: deleting.result()
            assert caught.value.code=="PROFILE_SOURCE_IN_USE"
        finally: release.set()


def test_delete_wins_before_confirm_source_is_not_resurrected(profiles,foundation):
    from app.repositories.files import Files
    from app.schemas.identity_profile import ConfirmIdentityProfile
    owner=str(uuid4())
    with profiles.database.connection() as conn: document,_=add_document(conn,owner)
    Files(profiles.database,MemoryStorage()).delete(owner,document)
    payload=ConfirmIdentityProfile.model_validate({"source_document_id":str(document),"expected_revision":0,"fields":fields()})
    with pytest.raises(foundation[3]) as caught: profiles.confirm(owner,payload)
    assert caught.value.status==404


@pytest.mark.parametrize("change",["owner","fields","status","storage"])
def test_completed_session_immutable_at_sql(profiles,source,change):
    owner,session=source()
    assignments={"owner":("owner_user_id",str(uuid4())),"fields":("fields",Jsonb({"full_name":"CHANGED"})),
                 "status":("status","processing"),"storage":("front_storage_key","unsafe")}
    column,value=assignments[change]
    with psycopg.connect(profiles.database.settings.database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(f"UPDATE document_identity_ocr_sessions SET {column}=%s WHERE id=%s",(value,session))


def test_profile_sql_owner_xor_cas_rls_and_no_hard_delete(profiles,source):
    owner,session=source(); profiles.confirm(owner,body(session))
    url=profiles.database.settings.database_url
    for role in ("anon","authenticated"):
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE "+role)
            with pytest.raises(psycopg.errors.InsufficientPrivilege): conn.execute("SELECT * FROM user_identity_profiles")
    for statement,args in [("UPDATE user_identity_profiles SET revision=revision WHERE owner_user_id=%s",(owner,)),
                           ("DELETE FROM user_identity_profiles WHERE owner_user_id=%s",(owner,)),
                           ("INSERT INTO user_identity_profiles (owner_user_id,source_session_id,full_name,confirmed_at) VALUES (%s,%s,'TEST',now())",(str(uuid4()),session)),
                           ("UPDATE user_identity_profiles SET revision=revision+1,source_document_id=%s WHERE owner_user_id=%s",(uuid4(),owner))]:
        with psycopg.connect(url) as conn:
            with pytest.raises((psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation)): conn.execute(statement,args)


@pytest.fixture
def api(profiles,foundation,service_auth):
    from app.api.identity_profile import get_identity_profile
    foundation[0].dependency_overrides[get_identity_profile]=lambda:profiles
    return TestClient(foundation[0]),lambda owner: {"Authorization":"Bearer "+service_auth(sub=owner)}


def test_api_group_contract_204_missing_cas_and_owner(api,source,profiles,service_auth):
    from test_document_contract_design import DOC,schema_validator
    client,headers=api; owner,session=source(); path="/api/v1/documents/identity-profile/me"
    assert client.get(path).status_code==401
    missing=client.get(path,headers=headers(owner)); assert missing.status_code==404
    schema_validator(DOC["components"]["schemas"]["ErrorResponse"],DOC).validate(missing.json())
    assert missing.json()["error"]["current_revision"]==0
    payload={"source_session_id":str(session),"expected_revision":0,"fields":fields()}
    response=client.put(path,headers=headers(owner),json=payload)
    assert response.status_code==200,response.text
    schema_validator(DOC["components"]["schemas"]["IdentityProfileResponse"],DOC).validate(response.json())
    assert response.headers["Cache-Control"]=="no-store"
    stale=client.put(path,headers=headers(owner),json=payload); assert stale.status_code==409
    schema_validator(DOC["components"]["schemas"]["ErrorResponse"],DOC).validate(stale.json())
    assert stale.json()["error"]["current_revision"]==1
    admin={"Authorization":"Bearer "+service_auth(sub=str(uuid4()),role="admin")}
    assert client.put(path,headers=admin,json=payload).status_code==404
    assert client.get(path,headers=admin,params={"user_id":owner}).status_code==404
    deleted=client.delete(path,headers=headers(owner),params={"expected_revision":1})
    assert deleted.status_code==204 and not deleted.content
    assert client.delete(path,headers=headers(owner),params={"expected_revision":1}).status_code==204
    assert client.get(path,headers=headers(owner)).json()["error"]["current_revision"]==2


@pytest.mark.parametrize("case",["extra_owner","extra_phone","missing_field","empty_all","blank","number_as_int","bad_id",
    "bad_date","impossible_date","future_date","issue_before_birth","date_as_number","control","format_char","too_long",
    "revision_bool","revision_float","revision_str","revision_negative","revision_overflow","both_sources","no_source","bad_uuid"])
def test_input_validation_no_pii_and_no_writes(api,profiles,case):
    from test_document_contract_design import DOC,schema_validator
    client,headers=api; owner=str(uuid4()); payload={"source_session_id":str(uuid4()),"expected_revision":0,"fields":fields()}
    if case=="extra_owner": payload["owner_user_id"]="private input"
    elif case=="extra_phone": payload["fields"]["phone"]="private input"
    elif case=="missing_field": payload["fields"].pop("full_name")
    elif case=="empty_all": payload["fields"]={key:None for key in fields()}
    elif case=="blank": payload["fields"]["full_name"]="   "
    elif case=="number_as_int": payload["fields"]["identity_number"]=123456789012
    elif case=="bad_id": payload["fields"]["identity_number"]="123"
    elif case=="bad_date": payload["fields"]["date_of_birth"]="01/01/2000"
    elif case=="impossible_date": payload["fields"]["date_of_birth"]="2000-02-31"
    elif case=="future_date": payload["fields"]["date_of_birth"]="2999-01-01"
    elif case=="issue_before_birth": payload["fields"]["issue_date"]="1990-01-01"
    elif case=="date_as_number": payload["fields"]["date_of_birth"]=12345
    elif case=="control": payload["fields"]["full_name"]="private\ninput"
    elif case=="format_char": payload["fields"]["full_name"]="private\u200binput"
    elif case=="too_long": payload["fields"]["permanent_address"]="x"*2001
    elif case=="revision_bool": payload["expected_revision"]=True
    elif case=="revision_float": payload["expected_revision"]=1.0
    elif case=="revision_str": payload["expected_revision"]="0"
    elif case=="revision_negative": payload["expected_revision"]=-1
    elif case=="revision_overflow": payload["expected_revision"]=2147483648
    elif case=="both_sources": payload["source_document_id"]=str(uuid4())
    elif case=="no_source": payload.pop("source_session_id")
    elif case=="bad_uuid": payload["source_session_id"]="private input"
    response=client.put("/api/v1/documents/identity-profile/me",headers=headers(owner),json=payload)
    assert response.status_code==422,response.text
    assert "private" not in response.text and "NGUYỄN" not in response.text
    schema_validator(DOC["components"]["schemas"]["ErrorResponse"],DOC).validate(response.json())
    with profiles.database.connection(read_only=True) as conn:
        assert not conn.execute("SELECT 1 FROM user_identity_profiles WHERE owner_user_id=%s",(owner,)).fetchone()


@pytest.mark.parametrize("content",[b'{"expected_revision":0,"expected_revision":1}',b'{"value":NaN}',b'{broken',b'\xff'])
def test_bad_json_rejected_without_echo(api,content):
    client,headers=api
    response=client.put("/api/v1/documents/identity-profile/me",headers={**headers(str(uuid4())),"Content-Type":"application/json"},content=content)
    assert response.status_code==400 and "broken" not in response.text


def test_json_bound_including_chunked_and_content_type(api):
    client,headers=api; auth=headers(str(uuid4())); path="/api/v1/documents/identity-profile/me"
    assert client.put(path,headers=auth,content=b"{}").status_code==400
    assert client.put(path,headers={**auth,"Content-Type":"application/json"},content=b"x"*16385).status_code==413
    response=client.put(path,headers={**auth,"Content-Type":"application/json"},content=iter([b"x"*9000,b"y"*9000]))
    assert response.status_code==413


@pytest.mark.parametrize("query",["", "0", "-1", "true", "1.0", "2147483648", "1&expected_revision=2"])
def test_delete_revision_admission(api,query):
    client,headers=api
    response=client.delete("/api/v1/documents/identity-profile/me?expected_revision="+query,headers=headers(str(uuid4())))
    assert response.status_code==422


def test_cookie_origin_required_for_confirm_unlink(api,source,service_auth):
    client,_=api; owner,session=source(); client.cookies.set("access_token",service_auth(sub=owner))
    payload={"source_session_id":str(session),"expected_revision":0,"fields":fields()}; path="/api/v1/documents/identity-profile/me"
    assert client.put(path,json=payload).status_code==403
    assert client.put(path,json=payload,headers={"Origin":"https://evil.invalid"}).status_code==403
    assert client.put(path,json=payload,headers={"Origin":"http://localhost:3000"}).status_code==200
    assert client.delete(path,params={"expected_revision":1}).status_code==403
    assert client.delete(path,params={"expected_revision":1},headers={"Origin":"http://localhost:3000"}).status_code==204
