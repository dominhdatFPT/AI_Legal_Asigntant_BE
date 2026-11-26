"""Helpers shared by the host and container test layers."""

import importlib
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from common_auth import ALGORITHM, ISSUER

BE = Path(__file__).resolve().parents[1]
INTERNAL_TOKEN = "test-internal-token-e2e-strong"
LAWYER_TOKEN = "test-lawyer-token-e2e-strong"
L2_AMQP = "amqp://guest:guest@127.0.0.1:55672/"
L2_REDIS = "redis://127.0.0.1:56379/0"


def create_keys(path):
    path.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (path / "jwt_private.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    (path / "jwt_public.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))


def create_database_tls(path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "postgres-e2e")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("postgres-e2e"),
            x509.DNSName("db.zzzzzzzzzzzzzzzzzzzz.supabase.co"),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (path / "postgres_tls.key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    (path / "postgres_tls.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def auth_headers(keys, subject, role):
    now = datetime.now(timezone.utc)
    token = jwt.encode({
        "sub": str(subject), "role": role, "iss": ISSUER, "type": "access",
        "iat": now, "exp": now + timedelta(minutes=15),
    }, (keys / "jwt_private.pem").read_bytes(), algorithm=ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def service_module(service, name):
    """Each service uses the same top-level package name app."""
    previous_path = sys.path[:]
    previous_modules = {
        key: sys.modules.pop(key) for key in tuple(sys.modules)
        if key == "app" or key.startswith("app.")
    }
    sys.path.insert(0, str(BE / "services" / f"{service}-service"))
    try:
        yield importlib.import_module(name)
    finally:
        for key in tuple(sys.modules):
            if key == "app" or key.startswith("app."):
                del sys.modules[key]
        sys.modules.update(previous_modules)
        sys.path[:] = previous_path


def eventually(check, *, description, timeout=8):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {description}")
        time.sleep(0.05)


def reference_pdf(label):
    """One small valid PDF with a distinct content stream and an exact xref."""
    if not label.isascii() or not label.replace(" ", "").isalnum():
        raise ValueError("Use an ASCII alphanumeric label")
    stream = f"BT /F1 18 Tf 45 720 Td ({label}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


def response_data(response, status):
    assert response.status_code == status, response.text
    envelope = response.json()
    assert envelope["error"] is None, envelope
    return envelope["data"]


if __name__ == "__main__":
    key_dir = Path(os.environ["LEGAL_TEST_KEYS_DIR"])
    create_keys(key_dir)
    create_database_tls(key_dir)
