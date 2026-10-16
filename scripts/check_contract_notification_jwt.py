"""Read-only HTTP smoke test, signing dev tokens without printing them.

Run at the backend root after starting docker-compose.jwt-local.yml:
    python scripts/check_contract_notification_jwt.py
Keys are read using the same .env rules as mint_dev_token.py.
"""
import argparse
from datetime import datetime, timedelta, timezone
import sys

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from common_auth import ALGORITHM, ISSUER
from mint_dev_token import load_private_key

USER_ID = "11111111-1111-4111-8111-111111111111"


def run(contract_url, notification_url, private_key, gateway_url=None):
    now = datetime.now(timezone.utc)
    baseline = {
        "sub": USER_ID, "role": "citizen", "type": "access", "iss": ISSUER,
        "iat": now, "exp": now + timedelta(minutes=15),
    }
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(signing_key=None, **changes):
        return jwt.encode({**baseline, **changes}, signing_key or private_key, algorithm=ALGORITHM)

    checks = 0
    with httpx.Client(timeout=10, trust_env=False) as client:
        def check(name, url, expected, access_token=None, code=None, identity=None):
            nonlocal checks
            headers = {"X-Request-ID": "req-docker-jwt-check"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            response = client.get(url, headers=headers)
            body = response.json()
            # Never include request headers/tokens or private data in failures.
            if response.status_code != expected:
                raise RuntimeError(f"{name}: HTTP {response.status_code}, expected {expected}")
            if code:
                if body.get("data") is not None or body.get("error", {}).get("code") != code:
                    raise RuntimeError(f"{name}: wrong error envelope")
                # Gateway generates its own request_id; direct calls echo the supplied ID.
                response_id = response.headers.get("X-Request-ID")
                if not response_id or body.get("request_id") != response_id:
                    raise RuntimeError(f"{name}: request_id mismatch")
                if not gateway_url and response_id != headers["X-Request-ID"]:
                    raise RuntimeError(f"{name}: direct request_id not echoed")
            if identity is not None and body.get("data") != identity:
                raise RuntimeError(f"{name}: identity did not come from token")
            checks += 1
            print(f"PASS {name}: HTTP {expected}")

        for service, base in [("contracts", contract_url), ("notifications", notification_url)]:
            root = gateway_url.rstrip("/") + "/api/" + service if gateway_url else base.rstrip("/") + "/api/v1/" + service
            check(f"{service} health public", root + "/health", 200)
            for role in ["citizen", "lawyer", "admin"]:
                check(f"{service} valid {role}", root + "/me", 200, token(role=role), identity={"user_id": USER_ID, "role": role})
            check(f"{service} missing token", root + "/me", 401, code="UNAUTHORIZED")
            check(f"{service} malformed token", root + "/me", 401, "not-a-jwt", code="UNAUTHORIZED")
            check(f"{service} expired token", root + "/me", 401, token(exp=now - timedelta(minutes=1)), code="TOKEN_EXPIRED")
            check(f"{service} wrong issuer", root + "/me", 401, token(iss="wrong-issuer"), code="UNAUTHORIZED")
            check(f"{service} refresh token", root + "/me", 401, token(type="refresh"), code="UNAUTHORIZED")
            check(f"{service} wrong signature", root + "/me", 401, token(signing_key=wrong_key), code="UNAUTHORIZED")
            check(f"{service} ignores spoofed user", root + "/me?user_id=someone-else&role=admin", 200, token(), identity={"user_id": USER_ID, "role": "citizen"})
        root = gateway_url.rstrip("/") + "/api/contracts/templates" if gateway_url else contract_url.rstrip("/") + "/api/v1/contracts/templates"
        check("templates missing token", root, 401, code="UNAUTHORIZED")
        check("templates valid token", root, 200, token())
    print(f"ALL {checks} live HTTP JWT checks PASS. No DB writes, key/token output, or business API tests.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-url", default="http://127.0.0.1:8006")
    parser.add_argument("--notification-url", default="http://127.0.0.1:8009")
    parser.add_argument("--key-file")
    parser.add_argument("--gateway-url", help="Test /api/... through Gateway instead of direct service ports")
    args = parser.parse_args()
    try:
        run(args.contract_url, args.notification_url, load_private_key(args.key_file), args.gateway_url)
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
        print(f"JWT check failed ({type(exc).__name__}); check container status, URLs and matching public key.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
