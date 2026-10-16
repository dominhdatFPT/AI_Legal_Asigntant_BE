"""Read-only HTTP checks against an already running Compose stack."""
import os
import httpx
import pytest

BASE = os.getenv('BACKEND_TEST_URL')
pytestmark = pytest.mark.skipif(not BASE, reason='Set BACKEND_TEST_URL to the running Gateway URL')

@pytest.fixture
def client():
    with httpx.Client(base_url=BASE, timeout=10, trust_env=False) as instance:
        yield instance

def test_gateway_root(client):
    r=client.get('/')
    assert r.status_code==200
    assert r.json()['status']=='ok'
    assert r.json()['service']=='gateway'

@pytest.mark.parametrize('name', ['agent','users','lawyers','procedures','rag','contracts','documents','locations','consultations','notifications'])
def test_health(client,name):
    r=client.get(f'/api/{name}/health')
    assert r.status_code==200
    assert r.json()['status']=='ok'

@pytest.mark.parametrize('message,found', [('hộ kinh doanh',True),('đăng ký kết hôn',False),('xin chào',False)])
def test_chat(client,message,found):
    r=client.post('/api/agent/chat',json={'message':message})
    assert r.status_code==200
    assert bool(r.json()['data']['procedure'])==found
    assert r.json()['request_id']

@pytest.mark.parametrize('path', ['/api/procedures','/api/procedures/'])
def test_list(client,path):
    r=client.get(path)
    assert r.status_code==200
    assert r.json()['data']['items'][0]['code']=='HKD-001'

def test_rag(client):
    r=client.post('/api/rag/search',json={'query':'hồ sơ'})
    assert r.status_code==200
    assert r.json()['data']['results'][0]['source']['id']=='SRC-DEMO'

def test_contract(client):
    token = os.getenv('BACKEND_TEST_TOKEN')
    if not token:
        pytest.skip('Set BACKEND_TEST_TOKEN to an access token signed with the running stack key')
    r=client.get('/api/contracts/templates', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code==200
    assert r.json()['data']['items'][0]['fields']

@pytest.mark.parametrize('prefix', ['contracts', 'notifications'])
def test_service_requires_jwt(client, prefix):
    r = client.get(f'/api/{prefix}/me')
    assert r.status_code == 401
    assert r.json()['error']['code'] == 'UNAUTHORIZED'

@pytest.mark.parametrize('path,key',[('agent/chat','message'),('procedures/search','query'),('rag/search','query')])
def test_invalid(client,path,key):
    r=client.post('/api/'+path,json={key:'   '})
    assert r.status_code==422
    assert r.json()['error']['code']=='VALIDATION_ERROR'

def test_cors(client):
    r=client.options('/api/agent/chat',headers={'Origin':'http://localhost:3000','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
    assert r.status_code==204
    assert r.headers['access-control-allow-origin']=='http://localhost:3000'

def test_cors_error(client):
    r=client.post('/api/agent/chat',json={'message':''},headers={'Origin':'http://localhost:3000'})
    assert r.status_code==422
    assert r.headers['access-control-allow-origin']=='http://localhost:3000'
