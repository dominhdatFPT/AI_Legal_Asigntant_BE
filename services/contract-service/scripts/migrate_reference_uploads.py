"""Add the PDF-upload table to the configured Contract DB; no changes to existing templates.

python scripts/migrate_reference_uploads.py          # check only
python scripts/migrate_reference_uploads.py --apply  # apply migration 003
"""
import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import DatabaseSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT.parents[1] / '.env', override=False)
    url = os.getenv('CONTRACT_DATABASE_URL', '').replace('postgresql+psycopg://', 'postgresql://', 1)
    if url and urlsplit(url).hostname not in {'localhost', '127.0.0.1'} and 'sslmode=' not in url:
        url += ('&' if '?' in url else '?') + 'sslmode=require'
    os.environ['CONTRACT_DATABASE_URL'] = url
    url = DatabaseSettings.from_env().database_url
    try:
        with psycopg.connect(url, connect_timeout=8, prepare_threshold=None, autocommit=True,
                             options='-c statement_timeout=15000 -c lock_timeout=3000') as conn:
            if args.apply:
                conn.execute((ROOT / 'database/003_reference_uploads.sql').read_text(encoding='utf-8'))
            ready = conn.execute("SELECT to_regclass('public.contract_reference_uploads') IS NOT NULL").fetchone()[0]
            print('Reference PDF upload schema: ' + ('ready' if ready else 'migration 003 required'))
    except psycopg.Error as exc:
        print('Migration failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
