"""One bounded attempt, no user JWT. Run independently of HTTP request lifetime."""
import hashlib
import time
from uuid import uuid4

from common_auth.events import publish_event_sync

from app.core.errors import ContractError
from app.repositories.generations import GenerationRepository
from app.services.contract_renderer import ContractRenderer
from app.services.contract_storage import ContractStorage
from app.services.personal_renderer import PersonalRenderer
from app.services.catalog_source import catalog_manifest, validate_catalog, CatalogSourceRenderer


class GenerationWorker:
    def __init__(self,repo,renderer,storage):
        self.repo,self.renderer,self.storage=repo,renderer,storage

    def once(self):
        self.storage.ready()
        self.renderer.ready("pdf")
        job=self.repo.claim()
        if not job:
            return False
        if job.get("exhausted"):
            return True
        key=f"results/{job['owner_user_id']}/{job['id']}/{uuid4()}.{job['format']}"
        uploaded=False
        try:
            if job.get("source"):
                source=job["source"]
                system=catalog_manifest(source) is not None
                if system:
                    validate_catalog(source,job["input_snapshot"],final=job["kind"]=="final")
                elif not source["snapshot_storage_key"].startswith(f"snapshots/{job['owner_user_id']}/"):
                    raise ContractError("INVALID_INPUT","Snapshot không đúng chủ.",422)
                original=self.storage.read(source["snapshot_storage_key"],source["source_sha256"],source["source_size_bytes"])
                data=(CatalogSourceRenderer().render(original,job["fields"],job["input_snapshot"],job["format"],job["kind"]=="preview") if system else
                    PersonalRenderer().render(original,source["source_format"],job["fields"],job["input_snapshot"],job["format"],job["kind"]=="preview"))
            else:
                data=self.renderer.render(job["content"],job["input_snapshot"],job["format"],job["kind"]=="preview")
            # Attempt-specific key, no overwrite even after crash or expired lease.
            uploaded=True
            self.storage.put(key,data,job["format"])
            committed=self.repo.finish(job,result={"storage_key":key,"sha256":hashlib.sha256(data).hexdigest(),"size_bytes":len(data)})
            if not committed:
                self.cleanup(job,key)
            else:
                self.notify(job,"contract.generated")
        except ContractError as exc:
            if uploaded:
                self.cleanup(job,key)
            if exc.code.startswith("STORAGE_") or exc.code=="DATABASE_UNAVAILABLE":
                # Existing processing lease expires; next attempt reclaims with fencing.
                return True
            code=exc.code if exc.code in {"TEXT_OVERFLOW","INVALID_INPUT","RENDER_TIMEOUT","INVALID_RENDER_OUTPUT"} else "RENDER_FAILED"
            if self.repo.finish(job,error=code):
                self.notify(job,"contract.generation.failed",code)
        except Exception:
            if uploaded:
                self.cleanup(job,key)
            # Do not store/log exception text which can contain contract values/secrets.
            if self.repo.finish(job,error="RENDER_FAILED"):
                self.notify(job,"contract.generation.failed","RENDER_FAILED")
        return True

    @staticmethod
    def notify(job,event_type,error_code=None):
        # Chỉ bản xuất final mới báo chuông (preview người dùng đang chờ trên màn hình).
        # shortcut: phát sau khi DB đã commit, worker chết đúng lúc đó thì mất thông báo; cần chắc chắn thì phát từ contract_notification_outbox.
        if job["kind"]=="final":
            publish_event_sync(event_type,{"user_id":str(job["owner_user_id"]),"job_id":str(job["id"]),
                "draft_id":str(job.get("draft_id")),"format":job["format"],"error_code":error_code})

    def cleanup(self,job,key):
        try:
            if self.repo.can_discard(job["id"],key):
                self.storage.remove(key)
        except ContractError:
            pass # crash/orphan reconciliation is documented; never delete other keys


def main():
    worker=GenerationWorker(GenerationRepository(),ContractRenderer(),ContractStorage.from_env())
    while True:
        try:
            busy=worker.once()
        except ContractError:
            busy=False
        time.sleep(1 if busy else 5)


if __name__=="__main__":
    main()
