"""Design consistency only: these tests do not imply API implementation."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'services/contract-service'
MAIN = yaml.safe_load((SERVICE / 'openapi.yaml').read_text(encoding='utf-8'))
FEATURE = yaml.safe_load((SERVICE / 'openapi-user-templates.yaml').read_text(encoding='utf-8'))
METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def canonical_refs(node):
    if isinstance(node, dict):
        return {key: value.replace('./openapi.yaml#/', '#/') if key == '$ref' else canonical_refs(value)
                for key, value in node.items()}
    if isinstance(node, list):
        return [canonical_refs(value) for value in node]
    return node


def test_all_ten_operations_are_bundled_and_planned():
    count = 0
    for url, item in FEATURE['paths'].items():
        for method, operation in item.items():
            if method in METHODS:
                count += 1
                assert operation['x-status'] == 'planned'
                assert MAIN['paths'][url][method] == canonical_refs(operation)
    assert count == 10


def test_feature_components_match_single_file_contract():
    for section in ('parameters', 'schemas', 'responses'):
        for name, value in FEATURE['components'][section].items():
            assert MAIN['components'][section][name] == canonical_refs(value)


def test_private_template_import_does_not_accept_owner_or_url():
    schema = FEATURE['paths']['/contracts/user-templates']['post']['requestBody']['content']['application/json']['schema']
    assert schema['additionalProperties'] is False
    assert 'document_id' in schema['required']
    assert not {'owner_user_id', 'url'} & set(schema['properties'])


def test_mapping_and_preview_contract():
    schemas = MAIN['components']['schemas']
    assert 'name' in schemas['MappedField']['required']
    assert 'preview_boxes' in schemas['DocxPlacement']['required']
    assert schemas['PdfPlacement']['properties']['page']['minimum'] == 1
    assert schemas['Generation']['properties']['kind']['enum'] == ['preview', 'final']
    assert 'template_version_id' in schemas['DraftInput']['required']
