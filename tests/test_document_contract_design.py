"""ERD/API design tests, not upload/OCR/database implementation tests."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / 'services/document-service/openapi.yaml'
DOC = yaml.safe_load(DOC_PATH.read_text(encoding='utf-8'))
CONTRACT = yaml.safe_load((ROOT / 'services/contract-service/openapi.yaml').read_text(encoding='utf-8'))
METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def resolve(ref):
    node = DOC
    for segment in ref.removeprefix('#/').split('/'):
        node = node[segment.replace('~1', '/').replace('~0', '~')]
    return node


def walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_document_replaces_wrong_procedure_spec_and_tracks_implemented_operations():
    assert DOC['info']['title'] == 'Document Service API'
    assert not any(path.startswith('/procedures') for path in DOC['paths'])
    operations = [(path, op) for path, item in DOC['paths'].items()
                  for method, op in item.items() if method in METHODS]
    assert len(operations) == 19
    assert len({op['operationId'] for _, op in operations}) == 19
    for path, op in operations:
        implemented = op['operationId'] in {'getDocumentHealth', 'getDocumentAuthContext', 'uploadDocument', 'listDocuments', 'getDocument',
                                            'getDocumentDownload', 'getDocumentContent', 'deleteDocument',
                                            'createIdentityOcrSession', 'getIdentityOcrSession',
                                              'getMyIdentityProfile', 'confirmMyIdentityProfile', 'unlinkMyIdentityProfile',
                                              'getDocumentProcessing', 'getDocumentExtraction',
                                              'createLawyerCardOcr', 'getLawyerCardOcr', 'getLawyerCardInternal',
                                              'createLawyerCardViewUrlInternal'}
        assert op['x-status'] == ('implemented' if implemented else 'planned')
    assert DOC['paths']['/documents/health']['get']['security'] == []
    assert DOC['security'] == [{'BearerAuth': []}, {'CookieAuth': []}]


def test_every_document_reference_is_local_and_exists():
    for node in walk(DOC):
        if isinstance(node, dict) and '$ref' in node:
            assert node['$ref'].startswith('#/')
            assert resolve(node['$ref'])


def test_upload_collection_matches_gateway_and_does_not_accept_owner_or_url():
    assert 'location /api/documents/' in (ROOT / 'gateway/nginx.conf').read_text(encoding='utf-8')
    assert '/documents/' in DOC['paths'] and '/documents' not in DOC['paths']
    upload = DOC['paths']['/documents/']['post']
    body = upload['requestBody']['content']['multipart/form-data']['schema']
    assert body['additionalProperties'] is False
    assert set(body['required']) == {'file', 'purpose', 'document_type'}
    assert not {'owner_user_id', 'url'} & set(body['properties'])
    assert any(p['$ref'].endswith('/IdempotencyKey') for p in upload['parameters'])
    assert set(upload['responses']) >= {'202', '401', '409', '413', '415', '422', '503'}


def test_contract_handoff_paths_hash_limits_and_snapshot_are_explicit():
    description = CONTRACT['paths']['/contracts/user-templates']['post']['description']
    for suffix in ('', '/processing', '/extraction', '/content'):
        assert '/documents/{document_id}' + suffix in DOC['paths']
    for required in ('DOCUMENT_SERVICE_URL', '9 MiB', 'available', 'completed',
                     'source_sha256', '/content', 'snapshot', 'không lưu JWT'):
        assert required in description
    assert set(CONTRACT['paths']['/contracts/user-templates']['post']['responses']) >= {'502', '503', '504'}
    assert DOC['components']['schemas']['Document']['properties']['uploaded_size_bytes']['maximum'] == 9 * 1024 * 1024


def test_file_and_job_status_are_independent_and_content_is_binary():
    schemas = DOC['components']['schemas']
    assert schemas['FileStatus']['enum'] == ['pending_check', 'available', 'rejected']
    assert schemas['ProcessingStatus']['enum'] == ['queued', 'processing', 'completed', 'failed']
    assert schemas['Document']['properties']['sha256']['nullable'] is True
    binary = DOC['paths']['/documents/{document_id}/content']['get']['responses']['200']['content']
    assert 'application/json' not in binary
    assert all(item['schema'] == {'type': 'string', 'format': 'binary'} for item in binary.values())


def test_erd_has_local_relationships_and_required_sql_invariants():
    text = (ROOT / 'services/document-service/database/erd.dbml').read_text(encoding='utf-8')
    assert re.findall(r'^Table (\w+)', text, re.M) == [
        'user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles',
        'document_identity_ocr_sessions', 'document_notification_outbox', 'document_lawyer_card_ocr']
    assert 'Ref: user_files.id < document_processing_jobs.document_id' in text
    assert 'Ref: document_processing_jobs.id - document_extraction_results.job_id' in text
    assert '(owner_user_id, idempotency_key) [unique]' in text
    assert 'job_id uuid [not null, unique]' in text
    assert 'Partial UNIQUE(document_id)' in text
    assert 'Ref: users.' not in text


def schema_validator(schema, spec=DOC):
    module = pytest.importorskip('openapi_schema_validator')
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT4
    root_uri = 'urn:document-openapi'
    registry = Registry().with_resource(root_uri, Resource.from_contents(spec, default_specification=DRAFT4))
    schema = deepcopy(schema)
    for node in walk(schema):
        if isinstance(node, dict) and '$ref' in node:
            node['$ref'] = root_uri + node['$ref']
    return module.OAS30Validator(schema, registry=registry)


def validator(schema_name):
    return schema_validator({'$ref': '#/components/schemas/' + schema_name})


def test_all_openapi_json_examples_match_their_schemas():
    count = 0
    for node in walk(DOC):
        if isinstance(node, dict) and 'schema' in node and 'example' in node:
            schema_validator(node['schema']).validate(node['example'])
            count += 1
    assert count >= 6


def test_metadata_json_in_handoff_document_is_valid_and_ready():
    text = (ROOT / 'docs/api/DOCUMENT_CONTRACT_INTEGRATION.md').read_text(encoding='utf-8')
    blocks = re.findall(r'```json\n(.*?)\n```', text, re.S)
    metadata = json.loads(blocks[0])
    validator('DocumentResponse').validate(metadata)
    assert metadata['data']['file_status'] == 'available'
    assert metadata['data']['processing_status'] == 'completed'
    validator('TextBlock').validate(json.loads(blocks[1]))


def test_pending_hash_nullable_and_extra_owner_input_not_silently_allowed():
    response = DOC['paths']['/documents/']['post']['responses']['202']['content']['application/json']['example']
    validator('DocumentResponse').validate(response)
    invalid = deepcopy(response)
    invalid['data']['storage_key'] = 'private/not-for-client'
    assert list(validator('DocumentResponse').iter_errors(invalid))


def test_bbox_rejects_out_of_range_and_zero_size_and_requires_review():
    validate = validator('Bbox')
    validate.validate({'x': 0.1, 'y': 0.2, 'width': 0.12, 'height': 0.03})
    for invalid in ({'x': -1, 'y': 0.2, 'width': 0.1, 'height': 0.1},
                    {'x': 0.1, 'y': 0.2, 'width': 0, 'height': 0.1}):
        assert list(validate.iter_errors(invalid))
    # Cross-field x+width/y+height and owner/status predicates must be checked by implementation.
    field = {'key': 'full_name', 'value': '[Họ tên mẫu]', 'confidence': None,
             'source_page': None, 'source_bbox': None, 'is_review_required': True}
    validator('ExtractedField').validate(field)
    field['is_review_required'] = False
    assert list(validator('ExtractedField').iter_errors(field))


def test_identity_profile_reuses_only_confirmed_six_fields_and_revision():
    fields = DOC['components']['schemas']['IdentityFields']
    allowed = {'full_name', 'date_of_birth', 'identity_number', 'issue_date', 'issue_place', 'permanent_address'}
    assert set(fields['properties']) == allowed
    assert set(fields['required']) == allowed
    assert fields['additionalProperties'] is False
    body = DOC['paths']['/documents/identity-profile/me']['put']['requestBody']['content']['application/json']
    schema_validator(body['schema']).validate(body['example'])
    assert body['schema']['properties']['expected_revision']['minimum'] == 0
    assert 'owner_user_id' not in body['schema']['properties']
    invalid = deepcopy(body['example'])
    invalid['fields']['phone'] = '0000000000'
    assert list(schema_validator(body['schema']).iter_errors(invalid))
    assert 'current_revision' in DOC['components']['schemas']['ErrorResponse']['properties']['error']['properties']
    assert 'user_identity_profiles' in (ROOT / 'services/document-service/database/erd.dbml').read_text(encoding='utf-8')


def test_contract_suggestions_only_identity_keys_and_no_values_in_manual_fields():
    keys = CONTRACT['components']['schemas']['IdentityKey']['enum']
    assert set(keys) == set(DOC['components']['schemas']['IdentityFields']['properties'])
    response = CONTRACT['components']['schemas']['IdentitySuggestionsResponse']
    properties = response['properties']['data']['properties']
    assert 'value' not in properties['manual_fields']['items']['properties']
    schema_validator({'$ref': '#/components/schemas/IdentityKey'}, CONTRACT).validate('full_name')
    assert list(schema_validator({'$ref': '#/components/schemas/IdentityKey'}, CONTRACT).iter_errors('rent_amount'))
    assert CONTRACT['components']['schemas']['MappedField']['properties']['identity_key']['default'] is None
    op = CONTRACT['paths']['/contracts/drafts/{draft_id}/identity-suggestions']['post']
    assert op['x-status'] == 'planned'
    assert 'Không thay đổi field_values' in op['description']
    assert 'identity-profile/me' in op['description']
    assert 'IDENTITY_PROFILE_NOT_FOUND' in op['description']
    body = op['requestBody']['content']['application/json']['schema']
    assert set(body['properties']) == {'bindings'}
    example = op['responses']['200']['content']['application/json']['example']
    schema_validator(response, CONTRACT).validate(example)


def test_profile_source_cannot_be_deleted_while_active_and_drafts_are_not_auto_changed():
    delete = DOC['paths']['/documents/{document_id}']['delete']['description']
    assert 'PROFILE_SOURCE_IN_USE' in delete
    assert 'revision' in DOC['paths']['/documents/identity-profile/me']['delete']['description']
    text = (ROOT / 'docs/api/IDENTITY_PROFILE_FILLING.md').read_text(encoding='utf-8')
    assert 'Không upload CCCD lại mỗi hợp đồng' in text
    assert 'Đổi profile không thay nháp cũ.' in text
    assert 'Không tự điền phone/email' in text
