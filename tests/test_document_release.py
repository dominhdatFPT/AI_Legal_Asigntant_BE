"""T8 hardening and sanitized read-only readiness regression."""
from dataclasses import replace
import os
from pathlib import Path
import time

import pytest
import yaml

from test_document_foundation import foundation, config_env
from test_document_local_ocr import local, Process, pair

ROOT = Path(__file__).resolve().parents[1]


def test_document_containers_nonroot_readonly_no_private_key():
    base = yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))
    worker = yaml.safe_load((ROOT / 'docker-compose.document-worker.yml').read_text(encoding='utf-8'))
    for service in (base['services']['document-service'], worker['services']['document-worker']):
        assert service['user'] == '65532:65532'
        assert service['read_only'] and service['init']
        assert service['cap_drop'] == ['ALL']
        assert service['security_opt'] == ['no-new-privileges:true']
        assert service['pids_limit'] <= 256 and service['mem_limit']
        assert 'ports' not in service
        assert 'JWT_PRIVATE_KEY_PATH' not in service['environment']
    api = base['services']['document-service']
    assert len(api['volumes']) == 1 and api['volumes'][0]['target'] == '/run/keys/jwt_public.pem'
    assert api['volumes'][0]['read_only'] and not api['volumes'][0]['bind']['create_host_path']
    assert 'notification-service' not in worker['services']['document-worker'].get('depends_on', {})


def test_tesseract_allowlist_excludes_arbitrary_keys_and_loader(local, monkeypatch):
    module, engine = local
    for name in ('AWS_ACCESS_KEY_ID', 'CUSTOM_API_KEY', 'LD_PRELOAD', 'PYTHONPATH', 'INTERNAL_TOKEN'):
        monkeypatch.setenv(name, 'must-not-reach-inference')
    def spawn(command, **kwargs):
        assert not {'AWS_ACCESS_KEY_ID', 'CUSTOM_API_KEY', 'LD_PRELOAD', 'PYTHONPATH', 'INTERNAL_TOKEN'} & kwargs['env'].keys()
        assert set(kwargs['env']) <= {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'OMP_THREAD_LIMIT'}
        return Process()
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    engine.read_image(pair()[0], deadline=time.monotonic() + 20, on_response=lambda _: None)


def test_settings_rejects_transaction_pooler_for_all_writers(foundation, config_env, monkeypatch):
    ref = 'a' * 20
    monkeypatch.setenv('DOCUMENT_DATABASE_URL', f'postgresql://postgres.{ref}:synthetic@aws-0-test.pooler.supabase.com:6543/postgres')
    with pytest.raises(foundation[2]) as exc: foundation[1].from_env()
    assert '5432' in str(exc.value) and 'synthetic' not in str(exc.value)
    monkeypatch.setenv('DOCUMENT_DATABASE_POOLER_HOST', 'aws-0-test.pooler.supabase.com')
    assert 'port=5432' in foundation[1].from_env().database_url or ':5432/' in foundation[1].from_env().database_url


def test_readiness_script_is_readonly_by_default():
    source = (ROOT / 'services/document-service/scripts/check_release.py').read_text(encoding='utf-8')
    assert 'connection(read_only=True)' in source
    assert 'worker.run_once' not in source and 'dispatcher.run_once' not in source
    assert 'put_quarantine' not in source and '--apply' not in source
