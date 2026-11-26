"""Authoritative values for account-bound fields; never trust submitted identity text."""
import os

from common_auth.context import request_id_var
from fastapi import HTTPException
import requests

from app.services.http_session import session as http
from app.services.submission_recipient import is_submission_recipient


PROFILE_KEYS = {
    'profile.full_name', 'profile.dob', 'profile.birth_year', 'profile.gender',
    'profile.cccd_number', 'profile.cccd_issue_date', 'profile.cccd_issue_place',
    'profile.permanent_address', 'profile.current_address', 'profile.phone',
    'profile.email', 'profile.tax_code',
}


def profile_values(profile):
    def address(item):
        if not isinstance(item, dict):
            return str(item or '')
        return ', '.join(str(item[key]) for key in ('detail', 'ward', 'province') if item.get(key))

    permanent = address(profile.get('permanent_address'))
    dob = str(profile.get('date_of_birth') or '')
    return {
        'profile.full_name': profile.get('full_name') or '',
        'profile.dob': dob, 'profile.birth_year': dob[:4],
        'profile.gender': profile.get('gender') or '',
        'profile.cccd_number': profile.get('id_number') or '',
        'profile.cccd_issue_date': profile.get('issue_date') or '',
        'profile.cccd_issue_place': profile.get('issue_place') or '',
        'profile.permanent_address': permanent,
        'profile.current_address': permanent if profile.get('current_same_as_permanent') else address(profile.get('current_address')),
        'profile.phone': profile.get('phone') or '', 'profile.email': profile.get('email') or '',
        'profile.tax_code': profile.get('tax_code') or '',
    }


def load_profile_values(user_id):
    from common_auth import breaker

    token = os.getenv('INTERNAL_TOKEN', '')
    if not token:
        raise HTTPException(503, 'Chưa cấu hình kết nối xác nhận hồ sơ cá nhân.')
    base = os.getenv('USER_SERVICE_URL', 'http://user-service:8000').rstrip('/')
    gate = breaker.get("procedure:USER")
    unavailable = 'Chưa xác nhận được hồ sơ cá nhân. Vui lòng thử lại.'
    if not gate.allow():
        raise HTTPException(503, unavailable)
    try:
        headers = {'X-Internal-Token': token, **({'X-Request-ID': rid} if (rid := request_id_var.get()) else {})}
        response = http.get(f'{base}/internal/v1/users/{user_id}/citizen-profile', headers=headers, timeout=8)
    except requests.RequestException as exc:
        gate.failure()
        raise HTTPException(503, unavailable) from exc
    if isinstance(response.status_code, int) and response.status_code >= 500:
        gate.failure()
    else:
        gate.success()
    try:
        response.raise_for_status()
        profile = response.json().get('data')
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(503, unavailable) from exc
    if not profile or not profile.get('id_number'):
        raise HTTPException(409, 'Vui lòng cập nhật hồ sơ CCCD trong Tài khoản trước khi tạo hồ sơ.')
    return profile_values(profile)


def bind_custom_profile(content, payload, user_id):
    bindings = [(f"{block['instance_id']}.{field['key']}", field['autofill'])
                for section in content.get('sections', []) for block in section.get('blocks', [])
                if block.get('block_key') != 'repeat_group' for field in block.get('fields', [])
                if field.get('autofill') in PROFILE_KEYS
                and not is_submission_recipient(field, block.get('block_key'))]
    if not bindings:
        return
    values = load_profile_values(user_id)
    for path, source in bindings:
        payload.values[path] = str(values[source])


def bind_application_profile(schema, form_data, user_id):
    mapping = {
        'applicant_name': 'profile.full_name', 'applicant_dob': 'profile.dob',
        'applicant_gender': 'profile.gender', 'applicant_id_number': 'profile.cccd_number',
        'applicant_id_issue_date': 'profile.cccd_issue_date', 'applicant_id_issue_place': 'profile.cccd_issue_place',
        'applicant_permanent_address': 'profile.permanent_address', 'applicant_current_address': 'profile.current_address',
        'applicant_phone': 'profile.phone', 'applicant_email': 'profile.email',
    }
    names = {field.name for field in schema.fields}
    if not user_id or not names.intersection(mapping):
        return form_data
    values = load_profile_values(user_id)
    result = dict(form_data)
    for name, source in mapping.items():
        if name in names:
            result[name] = str(values[source])
    if 'applicant_id_type' in names:
        result['applicant_id_type'] = 'CCCD'
    return result
