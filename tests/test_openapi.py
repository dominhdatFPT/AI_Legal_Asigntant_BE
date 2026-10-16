"""Kiểm tra mọi services/*/openapi.yaml theo API_GUIDELINES mục 12."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = sorted((ROOT / 'services').glob('*/openapi.yaml'))
METHODS = {'get', 'post', 'put', 'patch', 'delete'}
X_STATUS = {'implemented', 'partial', 'planned'}


def load(path):
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def walk_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == '$ref' and isinstance(value, str):
                yield value
            else:
                yield from walk_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_refs(item)


def resolve(spec, ref):
    node = spec
    for part in ref.lstrip('#/').split('/'):
        node = node[part]
    return node


def test_procedure_contract_exists():
    assert (ROOT / 'services/procedure-service/openapi.yaml') in CONTRACTS


@pytest.mark.parametrize('path', CONTRACTS, ids=lambda p: p.parent.name)
def test_contract_basics(path):
    spec = load(path)
    assert spec['openapi'] == '3.0.3'
    assert spec['info']['title'] and spec['info']['version']
    assert spec['paths']


@pytest.mark.parametrize('path', CONTRACTS, ids=lambda p: p.parent.name)
def test_every_operation_is_documented(path):
    spec = load(path)
    seen = set()
    for url, item in spec['paths'].items():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            where = f'{method.upper()} {url}'
            assert operation.get('operationId'), f'{where}: thiếu operationId'
            assert operation['operationId'] not in seen, f'{where}: operationId bị trùng'
            seen.add(operation['operationId'])
            assert operation.get('x-status') in X_STATUS, f'{where}: x-status phải là {sorted(X_STATUS)}'
            assert operation.get('responses'), f'{where}: thiếu responses'


@pytest.mark.parametrize('path', CONTRACTS, ids=lambda p: p.parent.name)
def test_local_refs_resolve(path):
    spec = load(path)
    for ref in walk_refs(spec):
        assert ref.startswith('#/'), f'Chỉ dùng $ref trong cùng file: {ref}'
        try:
            resolve(spec, ref)
        except (KeyError, TypeError):
            pytest.fail(f'$ref không tồn tại: {ref}')
