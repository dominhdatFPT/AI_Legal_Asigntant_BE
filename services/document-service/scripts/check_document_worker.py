"""Offline engine smoke using synthetic DOCX/image/PDF, no DB/cloud/user files."""
import argparse
import asyncio
import hashlib
import io
import os
from pathlib import Path
import sys
import zipfile
import traceback
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,NumberObject,DecodedStreamObject
from app.services.document_processor import DocumentProcessor


def scanned_pdf(png):
    from pypdf.generic import NumberObject
    with Image.open(io.BytesIO(png)) as image:
        stream=io.BytesIO();image.convert("RGB").save(stream,"JPEG");jpeg=stream.getvalue();width,height=image.size
    writer=PdfWriter();page=writer.add_blank_page(width=500,height=150)
    picture=DecodedStreamObject();picture.set_data(jpeg)
    picture.update({NameObject("/Type"):NameObject("/XObject"),NameObject("/Subtype"):NameObject("/Image"),
        NameObject("/Width"):NumberObject(width),NameObject("/Height"):NumberObject(height),
        NameObject("/ColorSpace"):NameObject("/DeviceRGB"),NameObject("/BitsPerComponent"):NumberObject(8),NameObject("/Filter"):NameObject("/DCTDecode")})
    page[NameObject("/Resources")]=DictionaryObject({NameObject("/XObject"):DictionaryObject({NameObject("/Im0"):writer._add_object(picture)})})
    paint=DecodedStreamObject();paint.set_data(b"q 500 0 0 150 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")]=writer._add_object(paint)
    stream=io.BytesIO();writer.write(stream);return stream.getvalue()


def samples():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w") as archive:
        archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>TEST DOCUMENT ONLY</w:t></w:r></w:p></w:body></w:document>')
    docx=stream.getvalue()
    image=Image.new("RGB",(1000,300),"white")
    ImageDraw.Draw(image).text((40,70),"TEST DOCUMENT ONLY",font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",48),fill="black")
    stream=io.BytesIO();image.save(stream,"PNG");png=stream.getvalue()
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=400)
    font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    text=DecodedStreamObject();text.set_data(b"BT /F1 18 Tf 30 330 Td (TEST DOCUMENT ONLY) Tj ET")
    page[NameObject("/Contents")]=writer._add_object(text)
    stream=io.BytesIO();writer.write(stream)
    text_pdf=stream.getvalue();scan_pdf=scanned_pdf(png)
    from pypdf import PdfReader
    mixed=PdfWriter()
    mixed.add_page(PdfReader(io.BytesIO(text_pdf)).pages[0]);mixed.add_page(PdfReader(io.BytesIO(scan_pdf)).pages[0])
    stream=io.BytesIO();mixed.write(stream)
    return [("test.docx","application/vnd.openxmlformats-officedocument.wordprocessingml.document",docx,"docx"),
            ("test.pdf","application/pdf",text_pdf,"pdf_text"),("test.png","image/png",png,"image"),
            ("scan.pdf","application/pdf",scan_pdf,"pdf_scan"),("mixed.pdf","application/pdf",stream.getvalue(),"pdf_mixed")]


async def api_smoke(url,scanner_port):
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from common_auth import ALGORITHM,ISSUER
    from app.main import app
    from app.api.files import get_files
    from app.api.processing import get_processing
    from app.core.config import Settings
    from app.repositories.database import Database
    from app.repositories.files import Files
    from app.repositories.processing import Processing
    from app.services.malware import MalwareScanner
    from app.worker import Worker
    database=Database(Settings(url,"https://synthetic-only.invalid","unused"))
    class MemoryStorage:
        def __init__(self):self.objects={}
        def put_quarantine(self,key,content,mime):self.objects[key]=content
        def remove_quarantine(self,key):self.objects.pop(key,None)
        def put_safe(self,key,content,mime):
            if key in self.objects:assert self.objects[key]==content
            self.objects[key]=content
        def read_verified(self,key,size,digest,mime):
            value=self.objects[key]
            assert len(value)==size and hashlib.sha256(value).hexdigest()==digest
            return value
    storage=MemoryStorage();files=Files(database,storage);processing=Processing(database)
    worker=Worker(database,storage,MalwareScanner("host.docker.internal",scanner_port),DocumentProcessor())
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    os.environ["JWT_PUBLIC_KEY"]=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    os.environ.pop("JWT_PUBLIC_KEY_PATH",None)
    owner=str(uuid4());other=str(uuid4())
    def token(subject):
        now=datetime.now(timezone.utc)
        return jwt.encode({"sub":subject,"role":"citizen","type":"access","iss":ISSUER,"iat":now,"exp":now+timedelta(minutes=10)},private,algorithm=ALGORITHM)
    app.dependency_overrides[get_files]=lambda:files
    app.dependency_overrides[get_processing]=lambda:processing
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            for name,mime,content,kind in samples():
                headers={"Authorization":"Bearer "+token(owner),"Idempotency-Key":str(uuid4())}
                response=await client.post("/api/v1/documents/",headers=headers,files={"file":(name,content,mime)},
                    data={"purpose":"supporting_document","document_type":"other"})
                assert response.status_code==202
                document=response.json()["data"]["document_id"];path="/api/v1/documents/"+document
                assert (await client.get(path+"/extraction",headers=headers)).status_code==409
                # Only this disposable local DB: never consume a cloud/team queue.
                assert worker.run_once(document_id=document)
                response=await client.get(path+"/extraction",headers=headers)
                assert response.status_code==200
                value=response.json()["data"]
                assert value["source_kind"]==kind and "TEST DOCUMENT ONLY" in value["text"]
                assert (await client.get(path+"/processing",headers=headers)).json()["data"]["status"]=="completed"
                downloaded=await client.get(path+"/content",headers=headers)
                assert hashlib.sha256(downloaded.content).hexdigest()==value["source_sha256"]
                assert (await client.get(path+"/extraction",headers={"Authorization":"Bearer "+token(other)})).status_code==404
                assert (await client.delete(path,headers=headers)).status_code==204
            # Official EICAR harmless anti-virus test string, held in RAM only.
            eicar=b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
            _,mime,safe_docx,_=samples()[0]
            stream=io.BytesIO(safe_docx)
            with zipfile.ZipFile(stream,"a") as archive:
                archive.writestr("word/media/antivirus-test.txt",eicar)
            headers={"Authorization":"Bearer "+token(owner),"Idempotency-Key":str(uuid4())}
            response=await client.post("/api/v1/documents/",headers=headers,files={"file":("test.docx",stream.getvalue(),mime)},
                data={"purpose":"supporting_document","document_type":"other"})
            assert response.status_code==202
            document=response.json()["data"]["document_id"]
            path="/api/v1/documents/"+document
            assert worker.run_once(document_id=document)
            state=(await client.get(path+"/processing",headers=headers)).json()["data"]
            assert state["file_status"]=="rejected" and state["error_code"]=="MALWARE_DETECTED"
            assert (await client.get(path+"/content",headers=headers)).status_code==409
        print("PASS: signed JWT/HTTP upload -> actual ClamAV -> isolated extraction/PaddleOCR -> local SQL -> owner-only extraction/content/hash; EICAR rejected.")
    finally:
        app.dependency_overrides.clear()
    # Synthetic rows remain only in disposable local DB; remove its container.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-test",action="store_true")
    parser.add_argument("--scanner-port",type=int,default=3310)
    args=parser.parse_args()
    if args.run_local_test:
        url=os.getenv("DOCUMENT_TEST_DATABASE_URL","");parsed=urlsplit(url)
        if (parsed.scheme not in {"postgres","postgresql"} or parsed.hostname not in {"localhost","127.0.0.1","host.docker.internal"}
                or parsed.path!="/document_schema_test" or parsed.query or parsed.fragment or not 1<=args.scanner_port<=65535):
            print("No changes: local smoke requires guarded disposable database.");return 1
        try:asyncio.run(api_smoke(url,args.scanner_port));return 0
        except Exception as exc:
            frame=traceback.extract_tb(exc.__traceback__)[-1]
            print(f"FAIL: local worker API smoke ({type(exc).__name__}, {Path(frame.filename).name}:{frame.lineno}); no content/credentials printed.")
            return 1
    processor=DocumentProcessor()
    try:
        for filename,mime,content,kind in samples():
            row={"original_filename":filename,"media_type":mime,"purpose":"supporting_document","document_type":"other"}
            clean=processor.run("sanitize",content,row)
            result=processor.run("extract",clean,row)
            assert result["source_kind"]==kind and "TEST DOCUMENT ONLY" in result["text"]
            assert result["extracted_fields"]==[]
        print("PASS: actual isolated DOCX/PDF text and pretrained PaddleOCR image; synthetic data, no network/DB/cloud.")
    except Exception:
        print("FAIL: worker engine smoke; content/error details omitted.");return 1
    return 0


if __name__=="__main__":raise SystemExit(main())
