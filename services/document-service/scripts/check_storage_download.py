"""Explicit opt-in cloud adapter probe: one synthetic PNG, always attempt exact cleanup.

No DB writes, no real documents, no bypassing file-availability rules in the API.
Never print keys, object paths, signed URLs, response bodies or original exceptions.
"""
import argparse
import io
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from PIL import Image
import httpx

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError
from app.services.storage import Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=SERVICE.parents[1] / ".env")
    parser.add_argument("--run-probe", action="store_true", help="Opt in to a temporary synthetic object and exact cleanup")
    args = parser.parse_args()
    if not args.run_probe:
        print("No changes made. Use --run-probe to test one synthetic private image and clean it up.")
        return 0
    load_dotenv(args.env_file, override=False)
    storage, attempted, cleanup_ok = None, False, True
    key = f"quarantine/{uuid4()}/{uuid4()}/source"
    failed = False
    try:
        settings = Settings.from_env()
        storage = Storage(settings)
        storage.check_bucket()
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
        content = buffer.getvalue()
        attempted = True
        storage.put_quarantine(key, content, "image/png")
        if storage.read_verified(key, len(content), hashlib.sha256(content).hexdigest(), "image/png") != content:
            raise DocumentError("PROBE_FAILED", "Authenticated read check did not pass.")
        url = storage.signed_download(key, "document-probe.png", ttl=60)
        with httpx.Client(timeout=settings.timeout_seconds, trust_env=False, follow_redirects=False) as client:
            response = client.get(url)
            if (response.status_code != 200 or response.content != content
                    or "attachment" not in response.headers.get("Content-Disposition", "").lower()
                    or response.headers.get("X-Content-Type-Options", "").lower() != "nosniff"):
                raise DocumentError("PROBE_FAILED", "Signed download/header check did not pass.")
        print("PASS: real Storage authenticated read/hash, signed download, attachment/nosniff, exact synthetic bytes.")
    except DocumentError as exc:
        print(f"FAIL: {exc.code}; no secret/body/signed URL printed.")
        failed = True
    except (ConfigurationError, httpx.HTTPError):
        print("FAIL: Storage adapter probe did not pass. No secret/body/signed URL printed.")
        failed = True
    finally:
        if storage is not None and attempted:
            try:
                storage.remove_quarantine(key)
                print("Synthetic probe object removed; no DB records or user files changed.")
            except DocumentError:
                cleanup_ok = False
                print("FAIL: synthetic private object cleanup needs retry; no user files were touched.")
    return 1 if failed or not cleanup_ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
