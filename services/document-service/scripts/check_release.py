"""T8 read-only readiness, no cloud probe, no queue consumption or secrets output."""
import argparse
from pathlib import Path
import sys
import os

from dotenv import load_dotenv

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from app.core.config import Settings, require_session_database
from app.repositories.database import Database
from app.services.storage import Storage
from app.services.malware import MalwareScanner
from setup_identity_profile import verify as verify_profile
from setup_document_outbox import verify as verify_outbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true', help='Additionally check local Paddle model files and ClamAV')
    args = parser.parse_args()
    load_dotenv(SERVICE.parents[1] / '.env', override=False)
    try:
        settings = Settings.from_env()
        require_session_database(settings)
        with Database(settings).connection(read_only=True) as conn:
            tables = ['user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles', 'document_identity_ocr_sessions', 'document_notification_outbox']
            rows = conn.execute("SELECT relname,relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='public' AND relname=ANY(%s)", (tables,)).fetchall()
            if len(rows) != len(tables) or not all(r['relrowsecurity'] for r in rows):
                raise ValueError('Schema/RLS missing')
            verify_profile(conn)
            verify_outbox(conn)
        Storage(settings).check_bucket()
        if args.worker:
            root = Path(os.getenv('DOCUMENT_PADDLE_MODEL_DIR', '/opt/document-ocr'))
            if not root.is_absolute() or not all((root / name / 'inference.yml').is_file() for name in ('det', 'rec')):
                raise ValueError('Model files missing')
            MalwareScanner(os.getenv('DOCUMENT_CLAMAV_HOST', 'document-antivirus'), int(os.getenv('DOCUMENT_CLAMAV_PORT', '3310'))).scan(b'Document release readiness synthetic bytes')
        print('PASS: release schema001/003/004/005, RLS/privileges, Session TLS/private Storage' + (' and worker model files/ClamAV.' if args.worker else '.'))
        print('Read-only: no document/job/event consumed or changed. This is not OCR accuracy or end-to-end cloud validation.')
        return 0
    except Exception:
        print('FAIL: release readiness; details/credentials/document content omitted.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
