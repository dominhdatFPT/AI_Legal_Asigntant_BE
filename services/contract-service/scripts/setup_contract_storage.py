"""Explicit pinned bucket setup/verify. Never echo secrets or mutate other projects."""
import argparse
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.contract_storage import ContractStorage, StorageSettings, MIMES, LIMIT
from app.core.errors import ContractError


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--project-ref",required=True)
    parser.add_argument("--create",action="store_true")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[3]
    values=dotenv_values(root/".env",interpolate=False)
    for key in ("CONTRACT_DATABASE_URL","CONTRACT_SUPABASE_URL","CONTRACT_SUPABASE_SERVICE_ROLE_KEY","CONTRACT_STORAGE_BUCKET"):
        if values.get(key) is not None:
            os.environ[key]=values[key]
    try:
        settings=StorageSettings.from_env()
        if settings.url!=f"https://{args.project_ref}.supabase.co" or settings.bucket!="contract-files":
            raise ContractError("WRONG_PROJECT","Sai project/bucket.")
        storage=ContractStorage(settings)
        if args.create:
            # Inspect inventory first; never alter an existing bucket's access/limits.
            buckets=storage.json("GET","/bucket")
            if not isinstance(buckets,list) or any(not isinstance(item,dict) for item in buckets):
                raise ContractError("STORAGE_BAD_RESPONSE","Sai phản hồi bucket.")
            if not any(item.get("id")==settings.bucket for item in buckets):
                storage.json("POST","/bucket",{"id":settings.bucket,"name":settings.bucket,"public":False,
                    "file_size_limit":LIMIT,"allowed_mime_types":list(MIMES.values())})
        storage.ready()
        print("Contract Storage verified: private bucket, 50 MiB, PDF/DOCX only. No file upload.")
    except ContractError as exc:
        print("Contract Storage check failed: "+exc.code)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
