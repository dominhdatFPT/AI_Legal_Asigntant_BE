"""Local ONLYOFFICE integration. Source/callback URLs are signed and draft scoped."""
from hashlib import sha256
from pathlib import Path
from threading import RLock
import os
import time
from types import SimpleNamespace
from urllib.parse import urlencode, urlparse
import uuid

import jwt
import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from common_auth.core import CurrentUser
from common_auth.fastapi_dep import require_role
from app.api.custom_form_templates import MAX_WORD, _row, _validate_upload
from app.repositories.procedure_repository import db_connection

router = APIRouter()
sessions = {}
lock = RLock()


def secret():
    value = os.getenv('ONLYOFFICE_JWT_SECRET') or os.getenv('JWT_SECRET', '')
    if not value:  # mặc định dẫn xuất từ khoá RS256 sẵn có; compose của onlyoffice tính cùng công thức (sha256 hex)
        key = Path(os.getenv('JWT_PRIVATE_KEY_PATH', '/run/keys/jwt_private.pem'))
        value = sha256(key.read_bytes()).hexdigest() if key.is_file() else ''
    if len(value) < 32:
        value = '795c0dd657f0025e70bae6b58bbab940befb3213452906daf4927e51d74a4c07'
    return value


def office_url():
    return os.getenv('ONLYOFFICE_INTERNAL_URL', 'http://onlyoffice').rstrip('/')


def session_for(template_id, session_id):
    with lock:
        session = sessions.get(session_id)
        if not session or session['template_id'] != template_id or session['expires'] < time.time():
            raise HTTPException(409, 'Phiên Word hết hạn. Hãy đóng và mở lại Word.')
        return session


def ticket_session(template_id, ticket, purpose):
    try:
        claims = jwt.decode(ticket, secret(), algorithms=['HS256'], options={'require': ['exp', 'purpose', 'sid', 'template_id']})
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, 'Liên kết Word không hợp lệ.') from exc
    if claims['template_id'] != template_id or claims['purpose'] != purpose:
        raise HTTPException(401, 'Liên kết Word không đúng phạm vi.')
    return session_for(template_id, claims['sid'])


def backup_path(digest):
    base = Path(os.getenv('ONLYOFFICE_BACKUP_DIR', '/app/office-backups'))
    base.mkdir(parents=True, exist_ok=True)
    return base / f'{digest}.docx'


@router.post('/admin/form-templates/{template_id}/office/config')
def config(template_id: str, user: CurrentUser = Depends(require_role('admin'))):
    try:
        health = requests.get(office_url() + '/healthcheck', timeout=4)
        if not health.ok or health.text.strip().lower() != 'true':
            raise requests.RequestException()
    except requests.RequestException as exc:
        raise HTTPException(503, 'ONLYOFFICE đang khởi động. Vui lòng mở lại sau ít phút.') from exc
    with db_connection() as conn:
        row = _row(conn, template_id)
    original = bytes(row.get('word_data') or b'')
    if not original:
        raise HTTPException(404, 'Tải file Word lên trước khi mở trình soạn.')
    digest = sha256(original).hexdigest()
    path = backup_path(digest)
    try:
        with path.open('xb') as stream:
            stream.write(original)
    except FileExistsError:
        pass
    sid = uuid.uuid4().hex
    expires = int(time.time()) + 8 * 3600
    key = sha256(f'{template_id}:{sid}'.encode()).hexdigest()
    with lock:
        for old_sid in list(sessions):
            if sessions[old_sid]['expires'] < time.time():
                del sessions[old_sid]
        if len(sessions) >= 100:
            raise HTTPException(503, 'Quá nhiều phiên Word đang mở.')
        sessions[sid] = dict(template_id=template_id, expires=expires, key=key,
                             original=digest, latest=digest, revision=0)
    base = os.getenv('ONLYOFFICE_CALLBACK_BASE', 'http://procedure-service:8000/api/v1/procedures').rstrip('/')
    def url(action):
        token = jwt.encode(dict(template_id=template_id, sid=sid, purpose=action, exp=expires), secret(), algorithm='HS256')
        return f'{base}/office/{template_id}/{action}?' + urlencode({'ticket': token})
    document = {'fileType': 'docx', 'key': key, 'title': row.get('word_filename') or 'mau-don.docx',
                'url': url('source'), 'permissions': {'edit': True, 'download': True, 'print': True}}
    editor_config = {'mode': 'edit', 'lang': 'vi', 'callbackUrl': url('callback'),
                     'user': {'id': str(user.id), 'name': 'Quản trị viên'},
                     'customization': {'forcesave': True, 'autosave': True}}
    editor = {'document': document, 'documentType': 'word', 'editorConfig': editor_config,
              'width': '100%', 'height': '100%', 'type': 'desktop'}
    editor['token'] = jwt.encode(editor, secret(), algorithm='HS256')
    public = os.getenv('ONLYOFFICE_PUBLIC_URL', 'http://localhost:8082').rstrip('/')
    return {'data': {'session': sid, 'revision': 0, 'script': public + '/web-apps/apps/api/documents/api.js', 'config': editor}, 'error': None}


@router.get('/office/{template_id}/source')
def source(template_id: str, ticket: str):
    session = ticket_session(template_id, ticket, 'source')
    # A signed session serves the immutable opening snapshot, not a live draft.
    with db_connection() as conn:
        _row(conn, template_id)
    return Response(backup_path(session['original']).read_bytes(),
                    media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Cache-Control': 'no-store'})


@router.post('/office/{template_id}/callback')
async def callback(template_id: str, ticket: str, request: Request):
    session = ticket_session(template_id, ticket, 'callback')
    body = await request.json()
    token = body.get('token') or request.headers.get('authorization', '').removeprefix('Bearer ')
    try:
        signed = jwt.decode(token, secret(), algorithms=['HS256'])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, 'Callback Word không hợp lệ.') from exc
    signed = signed.get('payload', signed)
    for key in ('key', 'status', 'url'):
        if body.get(key) != signed.get(key):
            raise HTTPException(401, 'Nội dung callback không khớp chữ ký.')
    if body.get('key') != session['key']:
        raise HTTPException(409, 'Sai phiên Word.')
    if body.get('status') not in (2, 6):
        return {'error': 0}
    target = urlparse(body.get('url', ''))
    expected = urlparse(office_url())
    public = urlparse(os.getenv('ONLYOFFICE_PUBLIC_URL', 'http://localhost:8082'))
    if (target.scheme, target.netloc) not in {(expected.scheme, expected.netloc), (public.scheme, public.netloc)} or not target.path.startswith('/cache/'):
        raise HTTPException(400, 'Địa chỉ lưu Word không hợp lệ.')
    download = office_url() + target.path + ('?' + target.query if target.query else '')
    try:
        with requests.get(download, timeout=20, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise requests.RequestException()
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_WORD:
                    raise HTTPException(413, 'File Word vượt dung lượng cho phép.')
    except requests.RequestException as exc:
        raise HTTPException(503, 'Không nhận được file Word từ trình soạn.') from exc
    updated = bytes(data)
    _validate_upload(SimpleNamespace(filename='mau.docx'), updated)
    with lock, db_connection(write=True) as conn:
        row = _row(conn, template_id, lock=True)
        if sha256(bytes(row.get('word_data') or b'')).hexdigest() != session['latest']:
            raise HTTPException(409, 'File đã thay đổi ở phiên khác; không ghi đè.')
        conn.execute(text('UPDATE public.custom_form_templates SET word_data=:data,updated_at=now() WHERE id=:id'), {'data': updated, 'id': template_id})
        session['latest'] = sha256(updated).hexdigest()
        session['revision'] += 1
    return {'error': 0}


class SaveCommand(BaseModel):
    session: str = Field(pattern=r'^[a-f0-9]{32}$')


@router.post('/admin/form-templates/{template_id}/office/save')
def force_save(template_id: str, body: SaveCommand, user: CurrentUser = Depends(require_role('admin'))):
    with db_connection() as conn:
        row = _row(conn, template_id)
    session = session_for(template_id, body.session)
    if sha256(bytes(row.get('word_data') or b'')).hexdigest() != session['latest']:
        raise HTTPException(409, 'File Word đã thay đổi ở phiên khác. Hãy mở lại trước khi lưu.')
    command = {'c': 'forcesave', 'key': session['key']}
    revision = session['revision']
    command['token'] = jwt.encode(command, secret(), algorithm='HS256')
    try:
        response = requests.post(office_url() + '/command', json=command, timeout=8)
        response.raise_for_status()
        error = response.json().get('error')
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(503, 'Không gửi được lệnh lưu Word.') from exc
    if error not in (0, 4):
        raise HTTPException(503, 'Trình Word chưa sẵn sàng để lưu. Hãy thử lại.')
    return {'data': {'revision': revision, 'unchanged': error == 4}, 'error': None}


@router.get('/admin/form-templates/{template_id}/office/status')
def save_status(template_id: str, session: str, user: CurrentUser = Depends(require_role('admin'))):
    with db_connection() as conn:
        _row(conn, template_id)
    state = session_for(template_id, session)
    return {'data': {'revision': state['revision']}, 'error': None}
