"""Static consistency checks; these do not replace docker compose build/up."""
import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIRS = sorted(p.name for p in (ROOT / 'services').iterdir() if (p / 'app/main.py').is_file())


def test_python_sources_parse():
    for source in (ROOT / 'services').rglob('*.py'):
        ast.parse(source.read_text(encoding='utf-8'))


def test_compose_paths_and_health_dependencies():
    config = yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))
    services = config['services']
    assert sorted(name for name in services if name != 'gateway') == SERVICE_DIRS
    for name, settings in services.items():
        if name == 'gateway':
            continue
        context = ROOT / settings['build']['context']
        assert settings['build']['additional_contexts']['libs'] == './libs'
        assert settings['environment']['JWT_PUBLIC_KEY_PATH'] == '/run/keys/jwt_public.pem'
        assert (context / 'Dockerfile').is_file()
        assert (context / 'requirements.txt').is_file()
        assert (context / 'app/main.py').is_file()
        assert settings['healthcheck']['test'][:2] == ['CMD', 'python']
        assert services['gateway']['depends_on'][name]['condition'] == 'service_healthy'
        assert 'ports' not in settings
    assert (ROOT / 'gateway/nginx.conf').is_file()


def test_example_json_valid():
    example = json.loads((ROOT / 'contracts/chat.example.json').read_text(encoding='utf-8'))
    assert example['response']['data']['procedure']['code'] == 'HKD-001'
    assert example['not_found_example']['response']['data']['procedure'] is None
