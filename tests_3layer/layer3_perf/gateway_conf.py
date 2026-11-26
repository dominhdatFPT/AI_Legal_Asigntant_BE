"""Derive the loopback-only load gateway configuration."""

import os
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "gateway" / "nginx.conf"
ORIGINAL_ZONE = "limit_req_zone $binary_remote_addr "
PERF_ZONE = "limit_req_zone $perf_client "
CLIENT_MAP = (
    '    map $http_cf_connecting_ip $perf_client {\n'
    '        "" $binary_remote_addr;\n'
    '        default $http_cf_connecting_ip;\n'
    '    }\n'
)


def derive(source):
    assert source.count(ORIGINAL_ZONE) == 4, "Expected four address-based limit_req_zone directives"
    assert "$perf_client" not in source, "Perf map already exists in the source gateway configuration"
    derived, blocks = re.subn(r"(?m)^http \{\n", "http {\n" + CLIENT_MAP, source)
    assert blocks == 1, "Expected exactly one http block"
    return derived.replace(ORIGINAL_ZONE, PERF_ZONE)


if __name__ == "__main__":
    destination = Path(os.environ["LEGAL_TEST_KEYS_DIR"]) / "nginx.perf.conf"
    destination.write_text(derive(SOURCE.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
