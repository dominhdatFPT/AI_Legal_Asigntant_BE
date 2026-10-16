"""Document handoff adapter for Contract to integrate, NOT a Contract import endpoint.

Caller supplies the validated user's owner UUID/access token. Never store JWT in a
job. Returned bytes+extraction must be copied into private Contract snapshots.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx

MAX_FILE = 9 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
MIMES = {'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


class HandoffError(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Snapshot:
    document_id: str
    job_id: str
    sha256: str
    media_type: str
    metadata: dict = field(repr=False)
    extraction: dict = field(repr=False)
    content: bytes = field(repr=False)


def read_document_snapshot(base, document_id, owner, access_token, *, client=None, wait=time.sleep):
    try:
        document_id, owner = str(UUID(str(document_id))), str(UUID(str(owner)))
        parsed = urlsplit(base)
        valid = (parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.path.strip('/') and not parsed.query and not parsed.fragment
            and (parsed.scheme == 'https' or parsed.hostname in {'document-service', 'localhost', '127.0.0.1'}))
        parsed.port
    except (ValueError, TypeError):
        valid = False
    if not valid or not access_token or any(c.isspace() for c in access_token):
        raise HandoffError('DOCUMENT_CONFIG_INVALID', 503)
    owns = client is None
    instance = client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False)
    headers = {'Authorization': 'Bearer ' + access_token, 'X-Request-ID': 'doc-import-' + document_id}

    def read(path, limit, binary=False):
        code, status = 'DOCUMENT_UNAVAILABLE', 503
        for attempt in range(3):
            try:
                with instance.stream('GET', base.rstrip('/') + '/api/v1/documents/' + document_id + path,
                                     headers=headers, timeout=5, follow_redirects=False) as response:
                    if response.status_code in {401, 403, 404, 409}:
                        raise HandoffError('DOCUMENT_HTTP_' + str(response.status_code), response.status_code)
                    if response.status_code != 200:
                        raise HandoffError('DOCUMENT_BAD_RESPONSE', 502)
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > limit:
                            raise HandoffError('DOCUMENT_BAD_RESPONSE', 502)
                    if binary:
                        return bytes(data), response.headers
                    try:
                        body = json.loads(data)
                        if body['error'] is not None or not isinstance(body['request_id'], str) or not isinstance(body['data'], dict):
                            raise ValueError()
                        return body['data']
                    except (ValueError, TypeError, KeyError):
                        raise HandoffError('DOCUMENT_BAD_RESPONSE', 502) from None
            except httpx.TimeoutException:
                code, status = 'DOCUMENT_TIMEOUT', 504
            except httpx.TransportError:
                code, status = 'DOCUMENT_UNAVAILABLE', 503
            except HandoffError as exc:
                if exc.status < 500:
                    raise
                code, status = exc.code, exc.status
            if attempt < 2:
                wait((0.5, 1)[attempt])
        raise HandoffError(code, status)

    try:
        metadata = read('', 65536)
        try:
            if metadata['document_id'] != document_id or metadata['owner_user_id'] != owner:
                raise ValueError()
            if (metadata['purpose'] != 'contract_template' or metadata['document_type'] != 'contract'
                or metadata['media_type'] not in MIMES):
                raise HandoffError('DOCUMENT_NOT_CONTRACT_TEMPLATE', 422)
            if metadata['file_status'] != 'available' or metadata['processing_status'] != 'completed':
                raise HandoffError('EXTRACTION_NOT_READY', 409)
            job = str(UUID(metadata['processing_job_id']))
            digest = metadata['sha256']
            size = metadata['size_bytes']
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or type(size) is not int or not 1 <= size <= MAX_FILE:
                raise ValueError()
            processing = read('/processing', 65536)
            if processing['document_id'] != document_id or processing['job_id'] != job:
                raise ValueError()
            if processing['status'] != 'completed' or processing['file_status'] != 'available':
                raise HandoffError('EXTRACTION_NOT_READY', 409)
            extraction = read('/extraction', MAX_JSON)
            expected_kind = {'pdf_text', 'pdf_scan', 'pdf_mixed'} if metadata['media_type'] == 'application/pdf' else {'docx'}
            if (extraction['document_id'] != document_id or extraction['job_id'] != job or extraction['schema_version'] != '1.0'
                or extraction['source_sha256'] != digest or extraction['source_kind'] not in expected_kind
                or not isinstance(extraction['text'], str) or len(extraction['text']) > 1000000
                or not isinstance(extraction['pages'], list) or not isinstance(extraction['warnings'], list)
                or not isinstance(extraction['extracted_fields'], list)):
                raise ValueError()
            content, h = read('/content', MAX_FILE, True)
            if (len(content) != size or hashlib.sha256(content).hexdigest() != digest
                or h.get('X-Content-SHA256') != digest or h.get('Content-Length') != str(size)
                or h.get('Content-Type', '').split(';')[0] != metadata['media_type']
                or h.get('X-Content-Type-Options') != 'nosniff'):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise HandoffError('DOCUMENT_BAD_RESPONSE', 502) from None
        return Snapshot(document_id, job, digest, metadata['media_type'], metadata, extraction, content)
    finally:
        if owns:
            instance.close()
