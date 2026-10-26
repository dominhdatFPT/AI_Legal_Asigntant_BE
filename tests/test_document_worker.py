"""T6 synthetic data, real disposable PostgreSQL; no cloud writes/model downloads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import io
import json
import socket
import threading
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject

from test_document_foundation import foundation,config_env
from test_document_upload import upload_database,docx,image,pdf,SERVICE


class MemoryStorage:
    def __init__(self):self.objects={};self.reads=[];self.writes=[]
    def put_quarantine(self,key,content,mime):self.objects[key]=content
    def remove_quarantine(self,key):self.objects.pop(key,None)
    def put_safe(self,key,content,mime):
        assert key.startswith("safe/")
        if key in self.objects:assert self.objects[key]==content
        self.objects[key]=content;self.writes.append(key)
    def read_verified(self,key,size,digest,mime):
        value=self.objects[key];assert len(value)==size and hashlib.sha256(value).hexdigest()==digest
        self.reads.append(key);return value


class Scanner:
    def __init__(self):self.calls=0;self.code=None
    def scan(self,value):
        self.calls+=1
        if self.code:
            from app.core.errors import DocumentError
            raise DocumentError(self.code,"Synthetic scanner error",422)


class Processor:
    def __init__(self):self.calls=[];self.code=None
    def run(self,mode,content,row):
        from app.services.document_engine import sanitize,extract
        self.calls.append(mode)
        if self.code and mode=="extract":
            from app.core.errors import DocumentError
            raise DocumentError(self.code,"Synthetic extraction error",422)
        if mode=="sanitize":return sanitize(content,{**row,"filename":row["original_filename"]})
        class Ocr:
            def blocks(self,image):return []
        return extract(content,row,ocr_factory=Ocr)


@pytest.fixture
def runtime(foundation,config_env,upload_database):
    from app.worker import Worker
    from app.repositories.files import Files
    from app.repositories.processing import Processing
    from app.services.file_validation import inspect_file
    database=foundation[5](replace(foundation[1].from_env(),database_url=upload_database))
    storage=MemoryStorage();scanner=Scanner();processor=Processor()
    files=Files(database,storage);worker=Worker(database,storage,scanner,processor)
    def upload(content=None,name="test.docx",mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",owner=None):
        owner=owner or str(uuid4())
        row=files.upload(owner,uuid4(),inspect_file(name,mime,content or docx(),"supporting_document","other"))
        return owner,row["document_id"]
    # Keep jobs from other tests out of this worker by completing each fixture's
    # own queue before yield; previous tests should leave terminal jobs only.
    while worker.run_once():pass
    return worker,files,Processing(database),storage,scanner,processor,upload


def test_docx_worker_atomic_and_content_hash(runtime):
    worker,files,processing,storage,scanner,processor,upload=runtime
    body='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>TEST ONLY</w:t></w:r></w:p></w:body></w:document>'
    owner,document=upload(docx(body=body))
    assert processing.status(owner,document)["status"]=="queued"
    assert worker.run_once()
    status=processing.status(owner,document);result=processing.extraction(owner,document)
    assert status["status"]=="completed" and status["file_status"]=="available"
    assert result["text"]=="TEST ONLY" and result["pages"]==[] and result["extracted_fields"]==[]
    assert scanner.calls==2 and processor.calls==["sanitize","extract"]
    content,mime,_,digest=files.content(owner,document)
    assert digest==result["source_sha256"]==hashlib.sha256(content).hexdigest()
    assert len(storage.writes)==1 and not worker.run_once()
    assert not any(key in result for key in ("storage_key","owner_user_id"))


def test_image_normalized_metadata_and_trailing_payload(runtime):
    worker,files,processing,_,_,_,upload=runtime
    raw=image()+b"TRAILING_PRIVATE_MARKER"
    owner,document=upload(raw,"image.png","image/png")
    assert worker.run_once()
    content=files.content(owner,document)[0]
    assert b"TRAILING_PRIVATE_MARKER" not in content and hashlib.sha256(content).hexdigest()!=hashlib.sha256(raw).hexdigest()
    result=processing.extraction(owner,document)
    assert result["source_kind"]=="image" and result["pages"][0]["blocks"]==[]


@pytest.mark.parametrize("code",["MALWARE_DETECTED","SCAN_UNAVAILABLE"])
def test_scanner_fail_closed(runtime,code):
    worker,files,processing,storage,scanner,_,upload=runtime
    owner,document=upload();scanner.code=code
    assert worker.run_once()
    row=processing.status(owner,document)
    assert row["status"]==("failed" if code=="MALWARE_DETECTED" else "queued")
    assert row["file_status"]==("rejected" if code=="MALWARE_DETECTED" else "pending_check") and not storage.writes
    with pytest.raises(Exception):files.content(owner,document)
    if code=="SCAN_UNAVAILABLE":
        # End this fixture's own retryable job without waiting on backoff.
        scanner.code="MALWARE_DETECTED"
        with worker.database.connection() as conn:
            conn.execute("UPDATE document_processing_jobs SET updated_at=now()-interval '1 minute' WHERE id=%s",(row["job_id"],))
        # guard_job resets updated_at; force eligibility by explicit retry_count0.
        with worker.database.connection() as conn:conn.execute("UPDATE document_processing_jobs SET retry_count=0 WHERE id=%s",(row["job_id"],))
        assert worker.run_once()


@pytest.mark.parametrize("code",["OCR_MODEL_UNAVAILABLE","PROCESSING_TIMEOUT","EXTRACTION_TOO_LARGE"])
def test_extraction_failure_preserves_safe_download(runtime,code):
    worker,files,processing,_,_,processor,upload=runtime
    owner,document=upload();processor.code=code
    assert worker.run_once()
    row=processing.status(owner,document)
    assert row["file_status"]=="available" and row["status"]=="failed" and row["error_code"]==code
    assert files.content(owner,document)[0]
    with pytest.raises(Exception) as exc:processing.extraction(owner,document)
    assert exc.value.code=="EXTRACTION_FAILED"


def test_pdf_blank_valid(runtime):
    worker,_,processing,_,_,_,upload=runtime
    owner,document=upload(pdf(),"blank.pdf","application/pdf")
    worker.run_once();value=processing.extraction(owner,document)
    assert value["source_kind"]=="pdf_scan" and value["text"]=="" and value["pages"][0]["blocks"]==[]


def test_crash_recovery_same_job_no_duplicate(runtime):
    worker,_,processing,storage,_,_,upload=runtime
    owner,document=upload();before=processing.status(owner,document)
    with worker.database.connection() as conn:
        conn.execute("SELECT id FROM user_files WHERE id=%s FOR UPDATE",(document,))
        conn.execute("UPDATE document_processing_jobs SET status='processing',started_at=now() WHERE id=%s",(before["job_id"],))
    assert worker.run_once()
    after=processing.status(owner,document)
    assert after["status"]=="completed" and after["job_id"]==before["job_id"] and len(storage.writes)==1


def test_stale_worker_fence(runtime):
    worker,_,processing,_,_,_,upload=runtime
    owner,document=upload()
    with worker.database.connection() as conn:
        row,job,lock=worker._claim(conn)
        conn.execute("UPDATE document_processing_jobs SET started_at=started_at+interval '1 second' WHERE id=%s",(job["id"],));conn.commit()
        with pytest.raises(Exception) as exc:worker._fence(conn,row,job)
        assert exc.value.code=="WORKER_STALE"
        conn.rollback();conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",(lock,));conn.commit()
    assert worker.run_once() and processing.status(owner,document)["status"]=="completed"


def test_two_workers_single_claim(runtime):
    worker,_,processing,storage,scanner,_,upload=runtime
    owner,document=upload();entered=threading.Event();release=threading.Event()
    original=scanner.scan
    def wait(content):entered.set();assert release.wait(5);return original(content)
    scanner.scan=wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(worker.run_once);assert entered.wait(3)
        second=pool.submit(worker.run_once)
        assert second.result(timeout=3) is False
        release.set();assert first.result(timeout=5)
    assert len(storage.writes)==1 and processing.status(owner,document)["status"]=="completed"


def test_processing_extraction_http_owner_and_state(runtime,foundation,service_auth):
    worker,files,processing,_,_,_,upload=runtime
    from app.api.processing import get_processing
    foundation[0].dependency_overrides[get_processing]=lambda:processing
    owner,document=upload();client=TestClient(foundation[0]);path=f"/api/v1/documents/{document}"
    headers={"Authorization":"Bearer "+service_auth(sub=owner)}
    assert client.get(path+"/processing").status_code==401
    assert client.get(path+"/extraction",headers=headers).status_code==409
    worker.run_once()
    for endpoint in ("processing","extraction"):
        response=client.get(path+"/"+endpoint,headers=headers)
        assert response.status_code==200 and response.headers["cache-control"]=="no-store"
        assert client.get(path+"/"+endpoint,headers={"Authorization":"Bearer "+service_auth(role="admin")}).status_code==404
    files.delete(owner,document)
    assert client.get(path+"/processing",headers=headers).status_code==404
    foundation[0].dependency_overrides.clear()


@pytest.mark.parametrize("extra",[("word/_rels/document.xml.rels",'<Relationships><Relationship TargetMode="External" Target="https://unsafe.invalid"/></Relationships>'),
                                 ("word/embeddings/object.bin","FAKE EMBEDDED")])
def test_unsafe_docx_rejected(runtime,extra):
    worker,_,processing,storage,_,_,upload=runtime
    owner,document=upload(docx(extra=extra));worker.run_once()
    assert processing.status(owner,document)["file_status"]=="rejected" and not storage.writes


W='xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DOCX_ROW={"filename":"test.docx","media_type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","purpose":"supporting_document","document_type":"other"}


def footer(*runs):
    return ("word/footer1.xml",f'<w:ftr {W}><w:p>{"".join(runs)}</w:p></w:ftr>')


def field_runs(instruction):
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> {instruction} </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')


@pytest.mark.parametrize("instruction",["PAGE",r"PAGE \* MERGEFORMAT","NUMPAGES",r'DATE \@ "dd/MM/yyyy"'])
def test_docx_page_number_fields_allowed(foundation,instruction):
    from app.services.document_engine import sanitize
    content=docx(extra=footer(field_runs(instruction)))
    assert sanitize(content,DOCX_ROW)==content


@pytest.mark.parametrize("instruction",[r'INCLUDETEXT "C:\secret.docx"',r"DDEAUTO c:\windows\system32\cmd.exe",'HYPERLINK "https://unsafe.invalid"',"MACROBUTTON Run x"])
def test_docx_dangerous_fields_rejected(foundation,instruction):
    from app.services.document_engine import sanitize
    with pytest.raises(foundation[3]) as exc:sanitize(docx(extra=footer(field_runs(instruction))),DOCX_ROW)
    assert exc.value.code=="UNSAFE_FILE"


def test_docx_dangerous_simple_field_and_unclosed_field_rejected(foundation):
    from app.services.document_engine import sanitize
    simple=f'<w:fldSimple {W} w:instr="INCLUDETEXT x"><w:r><w:t>1</w:t></w:r></w:fldSimple>'
    unclosed='<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>PAGE</w:instrText></w:r>'
    for part in (("word/footer1.xml",f'<w:ftr {W}><w:p>{simple}</w:p></w:ftr>'),footer(unclosed)):
        with pytest.raises(foundation[3]) as exc:sanitize(docx(extra=part),DOCX_ROW)
        assert exc.value.code=="UNSAFE_FILE"


def test_pdf_javascript_rejected(runtime):
    worker,_,processing,storage,_,_,upload=runtime
    writer=PdfWriter();writer.add_blank_page(width=300,height=400);writer.add_js("app.alert('synthetic')")
    stream=io.BytesIO();writer.write(stream)
    owner,document=upload(stream.getvalue(),"active.pdf","application/pdf");worker.run_once()
    assert processing.status(owner,document)["file_status"]=="rejected" and not storage.writes


@pytest.mark.parametrize("rotation",[0,90,180,270])
def test_pdf_native_text_crop_rotation(foundation,rotation):
    pytest.importorskip("pypdfium2")
    from app.services.document_engine import extract
    from app.schemas.extraction import ExtractionResult
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=400)
    page.cropbox.lower_left=(20,30);page.cropbox.upper_right=(280,370);page.rotate(rotation)
    font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    content=DecodedStreamObject();content.set_data(b"BT /F1 18 Tf 40 330 Td (TEST ONLY) Tj ET")
    page[NameObject("/Contents")]=writer._add_object(content);stream=io.BytesIO();writer.write(stream)
    result=extract(stream.getvalue(),{"media_type":"application/pdf"})
    ExtractionResult.model_validate(result)
    assert result["source_kind"]=="pdf_text" and "TEST ONLY" in result["text"]
    value=result["pages"][0]
    assert value["rotation_degrees"]==rotation and value["blocks"][0]["confidence"] is None
    assert value["width_pt"]==(260 if rotation in {0,180} else 340)


@pytest.mark.parametrize("reply,code",[(b"stream: OK\0",None),(b"stream: Synthetic FOUND\0","MALWARE_DETECTED"),
                                    (b"stream: ERROR\0","SCAN_UNAVAILABLE"),(b"stream: OK","SCAN_UNAVAILABLE")])
def test_clamav_protocol(foundation,reply,code):
    from app.services.malware import MalwareScanner
    server=socket.socket();server.bind(("127.0.0.1",0));server.listen();port=server.getsockname()[1]
    def serve():
        with server:
            with server.accept()[0] as conn:
                received=b""
                while not received.endswith(b"\0\0\0\0"):
                    received+=conn.recv(65536)
                assert received.startswith(b"zINSTREAM\0") and b"TEST ONLY" in received
                conn.sendall(reply)
    thread=threading.Thread(target=serve);thread.start()
    scanner=MalwareScanner("127.0.0.1",port,2)
    if code:
        with pytest.raises(foundation[3]) as exc:scanner.scan(b"TEST ONLY")
        assert exc.value.code==code
    else:scanner.scan(b"TEST ONLY")
    thread.join(timeout=3);assert not thread.is_alive()


def test_processor_actual_docx_child(foundation):
    from app.services.document_processor import DocumentProcessor
    processor=DocumentProcessor(timeout=10)
    row={"original_filename":"test.docx","media_type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "purpose":"supporting_document","document_type":"other"}
    clean=processor.run("sanitize",docx(),row)
    result=processor.run("extract",clean,row)
    assert result["source_kind"]=="docx" and result["pages"]==[]


def test_retry_budget_and_recovery_exhaustion(runtime):
    worker,_,processing,storage,scanner,_,upload=runtime
    owner,document=upload();worker.max_retries=0;scanner.code="SCAN_UNAVAILABLE"
    assert worker.run_once()
    assert processing.status(owner,document)["status"]=="failed" and not storage.writes
    owner,document=upload();job=processing.status(owner,document)["job_id"]
    with worker.database.connection() as conn:
        conn.execute("SELECT id FROM user_files WHERE id=%s FOR UPDATE",(document,))
        conn.execute("UPDATE document_processing_jobs SET status='processing',started_at=now(),retry_count=3 WHERE id=%s",(job,))
    assert not worker.run_once()
    assert processing.status(owner,document)["error_code"]=="WORKER_RECOVERY_EXHAUSTED"


def test_only_latest_attempt_no_old_result_fallback(runtime):
    worker,_,processing,_,_,processor,upload=runtime
    # Other fixtures may leave backoff jobs that become eligible mid-test.
    # This case tests attempt selection, not global queue scheduling.
    owner,document=upload();assert worker.run_once(document_id=document);first=processing.extraction(owner,document)
    with worker.database.connection() as conn:
        conn.execute("SELECT id FROM user_files WHERE id=%s FOR UPDATE",(document,))
        conn.execute("INSERT INTO document_processing_jobs (document_id,attempt) VALUES (%s,2)",(document,))
    with pytest.raises(Exception) as exc:processing.extraction(owner,document)
    assert exc.value.code=="EXTRACTION_NOT_READY"
    processor.code="PROCESSING_TIMEOUT";assert worker.run_once(document_id=document)
    assert processing.status(owner,document)["attempt"]==2
    with pytest.raises(Exception) as exc:processing.extraction(owner,document)
    assert exc.value.code=="EXTRACTION_FAILED"
    with worker.database.connection(read_only=True) as conn:
        assert conn.execute("SELECT 1 FROM document_extraction_results WHERE job_id=%s",(first["job_id"],)).fetchone()


def test_worker_result_shape_rejected_without_partial_complete(runtime):
    worker,_,processing,_,_,processor,upload=runtime
    owner,document=upload();original=processor.run
    def invalid(mode,content,row):
        result=original(mode,content,row)
        if mode=="extract":result["pages"]=[{"page":1,"private_marker":"NOT_FOR_RESPONSE"}]
        return result
    processor.run=invalid;worker.run_once()
    row=processing.status(owner,document)
    assert row["status"]=="failed" and row["file_status"]=="available"
    with worker.database.connection(read_only=True) as conn:
        assert not conn.execute("SELECT 1 FROM document_extraction_results WHERE job_id=%s",(row["job_id"],)).fetchone()


def test_docx_tables_tabs_breaks_and_metadata(foundation):
    from app.services.document_engine import extract,sanitize
    body='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
    result=extract(docx(body=body),{"media_type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
    assert result["text"]=="A\tB\nC" and result["pages"]==[]
    stream=io.BytesIO();source=Image.new("RGB",(10,20),"white");exif=Image.Exif();exif[274]=6;exif[315]="SYNTHETIC PRIVATE MARKER"
    source.save(stream,"JPEG",exif=exif)
    row={"filename":"test.jpg","media_type":"image/jpeg","purpose":"supporting_document","document_type":"other"}
    clean=sanitize(stream.getvalue(),row)
    with Image.open(io.BytesIO(clean)) as image:
        assert not image.getexif() and image.size==(20,10)
    assert b"SYNTHETIC PRIVATE MARKER" not in clean


@pytest.mark.parametrize("mutate",[lambda r:r.update(text="X"*1000001),lambda r:r.update(extra="forbidden"),
    lambda r:r.update(pages=[{"page":2,"width_pt":1.,"height_pt":1.,"rotation_degrees":0,"method":"ocr","blocks":[]}]),
    lambda r:r.update(extracted_fields=[{"key":"phone","value":"synthetic"}])])
def test_extraction_limits_and_no_auto_identity(foundation,mutate):
    from app.schemas.extraction import ExtractionResult
    result={"schema_version":"1.0","source_kind":"image","text":"", "pages":[],"extracted_fields":[],"warnings":[],"engine_version":"test"}
    mutate(result)
    with pytest.raises(Exception):ExtractionResult.model_validate(result)


def test_child_timeout_reaped_and_no_secrets(foundation,monkeypatch):
    import subprocess
    from app.services.document_processor import DocumentProcessor
    monkeypatch.setenv("DOCUMENT_SUPABASE_SERVICE_ROLE_KEY","SYNTHETIC SECRET MUST NOT BE PASSED")
    monkeypatch.setenv("JWT_PRIVATE_KEY","SYNTHETIC PRIVATE KEY")
    class Child:
        calls=0;killed=False
        def communicate(self,*args,**kwargs):
            self.calls+=1
            if self.calls==1:raise subprocess.TimeoutExpired("synthetic",10)
            return b"",b""
        def kill(self):self.killed=True
    child=Child()
    def spawn(*args,**kwargs):
        assert "DOCUMENT_SUPABASE_SERVICE_ROLE_KEY" not in kwargs["env"] and "JWT_PRIVATE_KEY" not in kwargs["env"]
        return child
    monkeypatch.setattr(subprocess,"Popen",spawn)
    row={"original_filename":"test.docx","media_type":"application/vnd.openxmlformats-officedocument.wordprocessingml.document","purpose":"supporting_document","document_type":"other"}
    with pytest.raises(foundation[3]) as exc:DocumentProcessor(timeout=10).run("extract",docx(),row)
    assert exc.value.code=="PROCESSING_TIMEOUT" and child.killed and child.calls==2


def test_safe_storage_retry_verifies_existing_bytes(foundation,config_env):
    content=b"SYNTHETIC";key=f"safe/{uuid4()}/{uuid4()}/source";calls=[]
    def handler(request):
        calls.append(request)
        if request.method=="POST":
            assert request.headers["x-upsert"]=="false" and request.content==content
            return httpx.Response(409)
        return httpx.Response(200,content=content,headers={"content-type":"image/png"})
    storage=foundation[6](foundation[1].from_env(),transport=httpx.MockTransport(handler))
    storage.put_safe(key,content,"image/png")
    assert len(calls)==2 and calls[1].method=="GET"


def test_targeted_smoke_does_not_consume_other_jobs(runtime):
    worker,_,processing,_,_,_,upload=runtime
    first_owner,first=upload();second_owner,second=upload()
    assert worker.run_once(document_id=second)
    assert processing.status(first_owner,first)["status"]=="queued"
    assert processing.status(second_owner,second)["status"]=="completed"
    assert worker.run_once(document_id=first)


def test_worker_overlay_no_key_mount_and_healthy_scanner():
    import yaml
    from pathlib import Path
    config=yaml.safe_load((SERVICE.parents[1]/"docker-compose.document-worker.yml").read_text(encoding="utf-8"))
    worker=config["services"]["document-worker"]
    assert worker["depends_on"]["document-antivirus"]["condition"]=="service_healthy"
    assert "volumes" not in worker and "JWT_PRIVATE_KEY" not in worker["environment"] and "JWT_PUBLIC_KEY_PATH" not in worker["environment"]
    assert "@sha256:" in config["services"]["document-antivirus"]["image"]
    assert (SERVICE/worker["build"]["dockerfile"]).is_file()
