"""Isolated ONLYOFFICE smoke app: synthetic document, memory DB, localhost only.

Run as an ephemeral container on the same Docker network. Never import this into
the production FastAPI application: its admin override is test-only.
"""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path

from docx import Document
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import jwt
import requests
import uvicorn

from app.api import office_editor as office
from common_auth.core import CurrentUser
from common_auth.fastapi_dep import get_current_user

doc = Document()
doc.add_heading('KIỂM TRA WORD TRÊN WEB', 0)
doc.add_paragraph('Họ và tên: {{ho_va_ten}}')
doc.add_paragraph('Chỉ là tài liệu thử, không chứa hồ sơ người dùng.')
table = doc.add_table(rows=2, cols=2)
table.style = 'Table Grid'
table.cell(0, 0).text = 'STT'
table.cell(0, 1).text = 'Tên người'
table.cell(1, 0).text = '{{#people}}{{people.index}}'
table.cell(1, 1).text = '{{people.name}}{{/people}}'
stream = BytesIO()
doc.save(stream)
original = stream.getvalue()
record = dict(id='smoke', status='draft', word_data=original, word_filename='kiem-tra.docx')
fixture_content = {'schema_version': 1, 'name': 'Mẫu kiểm thử Word', 'code': '', 'domain': 'dat_dai', 'procedure_id': None,
                   'sections': [{'id': 'sec_1', 'title': 'Thông tin người làm đơn', 'order': 1, 'blocks': []}]}


class Connection:
    def execute(self, statement, params):
        if 'UPDATE' in str(statement):
            record['word_data'] = params['data']


@contextmanager
def memory_db(**kwargs):
    yield Connection()


office.db_connection = memory_db
office._row = lambda conn, template_id, **kwargs: dict(record)
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:8082'], allow_methods=['GET'], allow_headers=['*'])
app.dependency_overrides[get_current_user] = lambda: CurrentUser(id='smoke-admin', role='admin')
app.include_router(office.router, prefix='/api/v1/procedures')
for route in app.routes:
    for dependency in getattr(getattr(route, 'dependant', None), 'dependencies', []):
        if dependency.name == 'user':
            app.dependency_overrides[dependency.call] = lambda: CurrentUser(id='smoke-admin', role='admin')


@app.get('/result')
def result():
    return {'original_unchanged': sha256(original).hexdigest() == sha256(office.backup_path(sha256(original).hexdigest()).read_bytes()).hexdigest(),
            'saved_changed': record['word_data'] != original,
            'revisions': [session['revision'] for session in office.sessions.values()]}


@app.post('/test-config')
def test_config():
    result = office.config('smoke', CurrentUser(id='smoke-admin', role='admin'))
    config = result['data']['config']
    config.pop('token', None)
    config['editorConfig']['plugins'] = {
        'autostart': ['asc.{AA6337B9-8052-4311-BF7C-F452EF351E76}'],
        'pluginsData': ['http://localhost:8766/plugin/config.json'],
    }
    config['token'] = jwt.encode(config, office.secret(), algorithm='HS256')
    return result


@app.get('/plugin/config.json')
def plugin_config():
    return {'name': 'Save smoke test', 'guid': 'asc.{AA6337B9-8052-4311-BF7C-F452EF351E76}',
            'baseUrl': 'http://localhost:8766/plugin/', 'variations': [{
                'description': 'Synthetic document test only', 'url': 'index.html', 'isViewer': False,
                'EditorsSupport': ['word'], 'isVisual': False, 'initDataType': 'none', 'initData': '', 'buttons': []}]}


@app.get('/plugin/index.html')
def plugin_page():
    return HTMLResponse('''<!doctype html><html><head><script src="http://localhost:8082/sdkjs-plugins/v1/plugins.js"></script></head>
<body><script>window.Asc.plugin.init=function(){window.Asc.plugin.executeMethod('PasteText',['TEST-SAVE-ONLYOFFICE '],function(){window.Asc.plugin.executeCommand('close','');});};window.Asc.plugin.button=function(){this.executeCommand('close','');};</script></body></html>''')


@app.get('/convert')
def convert():
    config = office.config('smoke', CurrentUser(id='smoke-admin', role='admin'))['data']
    command = {'async': False, 'filetype': 'docx', 'outputtype': 'docx',
               'key': config['config']['document']['key'], 'url': config['config']['document']['url'], 'title': 'kiem-tra.docx'}
    command['token'] = jwt.encode(command, office.secret(), algorithm='HS256')
    response = requests.post(office.office_url() + '/converter', json=command, headers={'Accept': 'application/json'}, timeout=90)
    response.raise_for_status()
    converted = response.json()
    return {'converted': converted, 'callback': config['config']['editorConfig']['callbackUrl'], 'key': config['config']['document']['key']}


@app.get('/auth-api.js')
def auth_stub():
    return Response('export const fetchProcedureNames=async()=>[]; export const formatDate=()=>"Ngày thử"; export const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll("\\\"", "&quot;");', media_type='text/javascript')


@app.get('/form-template-service.js')
def api_stub():
    return Response('''const base='/api/v1/procedures/admin/form-templates/';
async function api(url, body){const r=await fetch(url,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const p=await r.json();if(!r.ok)throw Error(p.detail);return p.data;}
export const USE_MOCK=false, DOMAINS={dat_dai:'Đất đai'}, STATUSES={draft:'Bản nháp'}, PROCEDURE_PREFIX={dat_dai:'DD-'};
export const formTemplates={listAdmin:async()=>({items:[await api('/fixture')]}),getAdmin:()=>api('/fixture'),update:(id,body)=>api('/fixture',body),
officeConfig:id=>api('/test-config',{}),officeSave:(id,session)=>api(base+id+'/office/save',{session}),officeStatus:(id,session)=>api(base+id+'/office/status?session='+session)};''', media_type='text/javascript')


@app.get('/fixture')
def fixture_row():
    return {'data': {'id': 'smoke', 'version': 1, 'status': 'draft', 'name': fixture_content['name'], 'domain': 'dat_dai',
                     'has_word_file': True, 'word_filename': 'kiem-tra.docx', 'content': fixture_content}, 'error': None}


@app.post('/fixture')
async def update_fixture(request: Request):
    global fixture_content
    fixture_content = await request.json()
    return fixture_row()


@app.get('/admin')
def admin_page():
    return HTMLResponse('''<!doctype html><html lang="vi"><head><meta charset="utf-8"><link rel="stylesheet" href="/style.css"></head>
<body><button id="add-template-card">Thêm mẫu</button><div id="ft-app"></div><script type="module" src="/form-templates-admin.js" data-domains="dat_dai"></script></body></html>''')


@app.get('/{asset}')
def admin_asset(asset: str):
    if asset not in {'form-templates-admin.js', 'form-blocks.js', 'form-renderer.js', 'field-rules.js', 'onlyoffice-template-editor.js', 'style.css'}:
        from fastapi import HTTPException
        raise HTTPException(404)
    return Response((Path('/qa') / ('form-templates.css' if asset == 'style.css' else asset)).read_text(encoding='utf-8'), media_type='text/css' if asset == 'style.css' else 'text/javascript')


@app.get('/onlyoffice-template-editor.js')
def frontend_module():
    return Response(Path('/qa/onlyoffice-template-editor.js').read_text(encoding='utf-8'), media_type='text/javascript')


@app.get('/style.css')
def frontend_style():
    return Response(Path('/qa/form-templates.css').read_text(encoding='utf-8'), media_type='text/css')


@app.get('/')
def page():
    return HTMLResponse('''<!doctype html><html lang="vi"><head><meta charset="utf-8"><link rel="stylesheet" href="/style.css"></head>
<body><h1>Kiểm tra ONLYOFFICE — tài liệu thử</h1><button id="save">Lưu Word thử</button><p id="result"></p>
<script type="module">import {createWordEditor} from '/onlyoffice-template-editor.js';
try{const panel=await createWordEditor('smoke',()=>{document.querySelector('#result').textContent='Có thay đổi';});
panel.refreshTokens({sections:[{blocks:[{fields:[{label:'Họ và tên',word_key:'ho_va_ten'}]}]}],repeat_groups:[]});
document.querySelector('#save').onclick=async()=>{try{await panel.save();document.querySelector('#result').textContent='Đã lưu';}catch(e){document.querySelector('#result').textContent=e.message}};
}catch(e){document.querySelector('#result').textContent=e.message}</script></body></html>''')


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8766)
