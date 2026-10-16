"""Static regression checks; nginx -t/live HTTP checks verify runtime separately."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / "gateway/nginx.conf").read_text(encoding="utf-8")


def test_all_upstreams_use_docker_dns_resolution():
    assert "resolver 127.0.0.11 valid=10s ipv6=off;" in CONFIG
    for service in ["agent", "user", "lawyer", "procedure", "rag", "contract", "document", "location", "consultation", "notification"]:
        block = re.search(r"upstream " + service + r"_service\s*\{([^}]+)\}", CONFIG)
        assert block is not None
        assert f"zone {service}_service 64k;" in block.group(1)
        assert f"server {service}-service:8000 resolve;" in block.group(1)


def test_contract_notification_gateway_routes_and_no_internal_route():
    for prefix in ["contracts", "notifications"]:
        assert f"location /api/{prefix}/" in CONFIG
        assert f"/api/v1/{prefix}/;" in CONFIG
    assert re.search(r"location\s+(?:=\s+)?/internal", CONFIG) is None


def test_idempotency_header_allowed_for_frontend():
    assert 'Access-Control-Allow-Headers "Content-Type, Authorization, X-Request-ID, Idempotency-Key"' in CONFIG


def test_document_download_metadata_headers_exposed_to_frontend():
    assert 'Access-Control-Expose-Headers "X-Request-ID, Content-Disposition, X-Content-SHA256"' in CONFIG


def test_legacy_document_port_is_backup_not_primary():
    assert "server document-service:8000 resolve;" in CONFIG
    assert "server document-service:5000 resolve backup;" in CONFIG
