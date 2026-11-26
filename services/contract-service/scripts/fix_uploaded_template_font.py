"""Replace one uploaded reference PDF and regenerate its derived data.

From BE/services/contract-service:
    python scripts/fix_uploaded_template_font.py --verify-only
    python scripts/fix_uploaded_template_font.py

The default PDF path is relative to the repository workspace, not cwd.
The write runs in one PostgreSQL transaction and updates only the target row.
"""
import argparse
import hashlib
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
try:
    WORKSPACE = Path(__file__).resolve().parents[4]
except IndexError:
    WORKSPACE = ROOT
sys.path.insert(0, str(ROOT))

from app.core.config import DatabaseSettings
from app.services.reference_pdf import prepare_reference_pdf

TARGET = UUID("7a0a0f45-36e6-4373-a638-e5dcfe41416d")
DEFAULT_PDF = (
    WORKSPACE
    / "FE/AI_Legal/public/demo_assets"
    / "37_Mau_don_Giay_phep_xay_dung_moi_thamkhao.pdf"
)


def database_url():
    try:
        load_dotenv(ROOT.parents[1] / ".env", override=False)
    except IndexError:
        pass
    url = os.getenv("CONTRACT_DATABASE_URL") or os.getenv("DATABASE_URL") or ""
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url and urlsplit(url).hostname not in {"localhost", "127.0.0.1"}:
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
    if url:
        os.environ["CONTRACT_DATABASE_URL"] = url
    return DatabaseSettings.from_env().database_url.replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def build_replacement(row, pdf_path):
    data = pdf_path.read_bytes()
    old = row["metadata"]
    if not isinstance(old, dict):
        raise ValueError("metadata is not a JSON object")
    if str(old.get("id")) != str(TARGET):
        raise ValueError("metadata.id does not match the target row")
    if old.get("domain") != row["domain"] or old.get("procedure") != row["procedure_key"]:
        raise ValueError("metadata classification does not match the target row")

    classification = {
        "key": row["procedure_key"],
        "code": old.get("procedure_code"),
        "name": old.get("procedure_name") or old.get("name") or "Mẫu tham khảo",
    }
    generated, images = prepare_reference_pdf(
        data, pdf_path.name, old.get("name") or "Mẫu tham khảo",
        row["domain"], classification,
    )
    if not generated["text"].strip():
        raise ValueError("replacement PDF has no extractable text")
    # Require evidence of Vietnamese characters beyond WinAnsi in this
    # specifically requested replacement; inspect the file if this fails.
    if not any(
        ch in "ĂăĐđĨĩŨũƠơƯư" or "\u1ea0" <= ch <= "\u1ef9"
        for ch in generated["text"]
    ):
        raise ValueError("replacement PDF has no expected Vietnamese Unicode text")

    metadata = dict(old)
    for key in (
        "page_count", "page_dimensions", "text", "source_filename",
        "source_sha256", "pdf_sha256",
    ):
        metadata[key] = generated[key]
    metadata["pdf"] = f"/api/reference-assets/{TARGET}.pdf"
    metadata["pages"] = [
        f"/api/reference-assets/{TARGET}-page-{i}.png"
        for i in range(1, len(images) + 1)
    ]
    return data, images, metadata


def mismatches(row, data, images, metadata):
    problems = []
    digest = hashlib.sha256(data).hexdigest()
    if bytes(row["file_data"]) != data:
        problems.append("file_data")
    if row["source_sha256"].strip() != digest:
        problems.append("source_sha256")
    stored_images = [bytes(value) for value in row["page_images"]]
    if stored_images != images:
        problems.append("page_images")
    if row["metadata"] != metadata:
        problems.append("metadata")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    args = parser.parse_args()

    try:
        # Do not print the URL or exception text: either may contain credentials.
        with psycopg.connect(
            database_url(), connect_timeout=8, prepare_threshold=None,
            row_factory=dict_row,
            options="-c statement_timeout=60000 -c lock_timeout=5000",
        ) as conn:
            query = """
                SELECT id, domain, procedure_key, source_sha256, metadata,
                       file_data, page_images
                FROM public.contract_reference_uploads
                WHERE id = %s
            """
            if not args.verify_only:
                query += " FOR UPDATE"
            row = conn.execute(query, (TARGET,)).fetchone()
            if row is None:
                raise ValueError("target upload was not found")

            data, images, metadata = build_replacement(row, args.pdf)
            problems = mismatches(row, data, images, metadata)

            if args.verify_only:
                if problems:
                    print("VERIFY FAILED: " + ", ".join(problems), file=sys.stderr)
                    return 1
                print(
                    f"VERIFY OK: {TARGET}, {len(images)} pages, "
                    f"sha256={metadata['pdf_sha256']}"
                )
                return 0

            if not problems:
                print(f"Already up to date: {TARGET}")
                return 0

            result = conn.execute(
                """
                UPDATE public.contract_reference_uploads
                SET file_data = %s, page_images = %s,
                    source_sha256 = %s, metadata = %s
                WHERE id = %s
                RETURNING id
                """,
                (data, images, metadata["source_sha256"], Jsonb(metadata), TARGET),
            ).fetchone()
            if result is None:
                raise RuntimeError("target update returned no row")
            # Verify the stored values inside the same transaction. Exiting the
            # connection context commits only if all checks have succeeded.
            saved = conn.execute(query, (TARGET,)).fetchone()
            remaining = mismatches(saved, data, images, metadata)
            if remaining:
                raise RuntimeError("verification after update failed: " + ", ".join(remaining))
            print(
                f"Updated and verified: {TARGET}, {len(images)} pages, "
                f"sha256={metadata['pdf_sha256']}"
            )
        return 0
    except psycopg.errors.UniqueViolation:
        print(
            "FAILED: another upload has the same domain, procedure and PDF hash; "
            "transaction rolled back.",
            file=sys.stderr,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
    except psycopg.Error as exc:
        print(f"FAILED: database operation ({type(exc).__name__})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
