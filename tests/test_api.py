import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

# (thư mục service, prefix của API)
SERVICES = [
    ('agent', 'agent'), ('user', 'users'), ('lawyer', 'lawyers'), ('procedure', 'procedures'),
    ('rag', 'rag'), ('contract', 'contracts'), ('document', 'documents'), ('location', 'locations'),
    ('consultation', 'consultations'), ('notification', 'notifications'),
]


def load(name):
    service_dir = str(ROOT / 'services' / name)
    loaded = sys.modules.get('app')
    if loaded is not None and not str(getattr(loaded, '__file__', '')).startswith(service_dir):
        for module_name in [m for m in sys.modules if m == 'app' or m.startswith('app.')]:
            del sys.modules[module_name]
    sys.path.insert(0, service_dir)
    try:
        spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'services' / name / 'app/main.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(service_dir)
        # Không để lại package `app` của service vừa load: test của service khác
        # (VD services/user-service/tests) cũng `import app` và sẽ lấy nhầm bản này.
        for module_name in [m for m in sys.modules if m == 'app' or m.startswith('app.')]:
            del sys.modules[module_name]
    return module


@pytest.mark.parametrize('name,path', SERVICES)
def test_health(name, path):
    module = load(name + '-service')
    client = TestClient(module.app)
    r = client.get('/api/v1/' + path + '/health')
    assert r.status_code == 200
    body = r.json()
    assert body == {'status': 'ok', 'service': name + '-service'}


def test_procedure_search_without_db_is_503_not_sample(monkeypatch):
    # Không có database thì báo lỗi, không trả thủ tục mẫu (kết quả tìm kiếm thật test ở services/procedure-service/tests).
    monkeypatch.setenv('PROCEDURE_DATABASE_URL', '')
    r = TestClient(load('procedure-service').app).post('/api/v1/procedures/search', json={'query': 'hộ kinh doanh'})
    assert r.status_code == 503
    assert r.json()['error']['code'] == 'PROCEDURE_DB_UNAVAILABLE'


@pytest.mark.parametrize('service,path,key', [('agent', 'agent/chat', 'message'), ('procedure', 'procedures/search', 'query'), ('rag', 'rag/search', 'query')])
@pytest.mark.parametrize('value', ['', '   ', 'x' * 2001, None])
def test_invalid_input(service, path, key, value):
    r = TestClient(load(service + '-service').app, raise_server_exceptions=False).post('/api/v1/' + path, json={key: value})
    assert r.status_code == 422
    assert r.json()['error']['code'] == 'VALIDATION_ERROR'


def test_templates_request_id(service_auth):
    r = TestClient(load('contract-service').app).get('/api/v1/contracts/templates', headers={'X-Request-ID': 'test-id', 'Authorization': f'Bearer {service_auth()}'})
    assert r.json()['request_id'] == 'test-id'
    assert r.json()['data']['items'][0]['fields']


def test_rag_is_demo():
    r = TestClient(load('rag-service').app).post('/api/v1/rag/search', json={'query': 'hồ sơ'})
    assert r.status_code == 200
