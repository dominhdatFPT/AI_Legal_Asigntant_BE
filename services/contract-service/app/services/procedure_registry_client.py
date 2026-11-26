"""Client for the internal procedure registration API."""

import logging
import os

import httpx
from common_auth.context import sanitize_request_id

from app.core.errors import ContractError
from app.core.http import http_client

logger = logging.getLogger(__name__)
transport: httpx.BaseTransport | None = None


class EndpointMissing(Exception):
    pass


def _unavailable() -> ContractError:
    return ContractError(
        "PROCEDURE_SERVICE_UNAVAILABLE", "Dịch vụ thủ tục tạm thời không phản hồi, vui lòng thử lại.", 503,
    )


def _request(method: str, path: str, *, timeout: float, request_id: str | None,
             retry_timeout: bool = False, **kwargs) -> dict:
    base_url = os.environ.get("PROCEDURE_SERVICE_URL", "").rstrip("/")
    if not base_url:
        raise _unavailable()
    headers = {"X-Internal-Token": os.getenv("INTERNAL_TOKEN", "")}
    if rid := sanitize_request_id(request_id):
        headers["X-Request-ID"] = rid
    try:
        with http_client(transport, timeout=timeout, follow_redirects=False, trust_env=False) as client:
            try:
                response = client.request(method, base_url + path, headers=headers, **kwargs)
            except httpx.TimeoutException:
                if not retry_timeout:
                    raise
                response = client.request(method, base_url + path, headers=headers, **kwargs)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("Procedure registry request failed (%s)", type(exc).__name__)
        raise _unavailable() from exc

    status = response.status_code
    if response.is_success or status in (404, 409, 422):
        try:
            body = response.json()
            if response.is_success and body.get("error") is None and isinstance(body["data"], dict):
                return body["data"]
            error = body["error"]
            expected_codes = {
                404: ("PROCEDURE_NOT_FOUND",),
                409: ("PROCEDURE_CODE_EXISTS", "PROCEDURE_CODE_CONFLICT"),
                422: ("FORM_TEMPLATE_UNAVAILABLE", "PDF_TOO_LARGE"),
            }
            if error["code"] in expected_codes.get(status, ()) and isinstance(error["message"], str):
                raise ContractError(error["code"], error["message"], status)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    if status in (404, 405):
        raise EndpointMissing()
    logger.warning("Procedure registry returned status %d", status)
    raise _unavailable()


def next_code(domain: str, *, floor: int, request_id: str | None = None) -> str:
    data = _request("GET", "/internal/v1/procedures/next-code", timeout=10, request_id=request_id,
                    params={"domain": domain, "floor": floor})
    number = data.get("number")
    prefix = "DD-" if domain == "land" else "HKD-"
    if type(number) is not int or number <= floor or data.get("code") != f"{prefix}{number:03d}":
        logger.warning("Procedure registry next-code returned invalid data")
        raise _unavailable()
    return data["code"]


def register(
    *, procedure_id: str, name: str, domain: str, code: str | None, floor: int,
    form_template_id: str | None, pdf_data: bytes | None, pdf_filename: str | None,
    request_id: str | None = None,
) -> dict:
    form = {"procedure_id": procedure_id, "name": name, "domain": domain, "floor": str(floor)}
    if code is not None:
        form["code"] = code
    if form_template_id:
        form["form_template_id"] = form_template_id
    files = None
    if form_template_id and pdf_data is not None:
        files = {"reference_pdf": (pdf_filename or "reference.pdf", pdf_data, "application/pdf")}
    data = _request("POST", "/internal/v1/procedures", timeout=30, request_id=request_id,
                    retry_timeout=True, data=form, files=files)
    classification = data.get("classification")
    if (
        data.get("procedure_id") != procedure_id
        or type(data.get("created")) is not bool
        or not isinstance(classification, dict)
        or classification.get("procedure_id") != procedure_id
        or classification.get("domain") != domain
        or classification.get("kind") != "project_procedure"
        or not all(isinstance(classification.get(field), str) and classification[field].strip()
                   for field in ("key", "code", "name"))
        or data.get("code") != classification["code"]
    ):
        logger.warning("Procedure registry registration returned invalid data")
        raise _unavailable()
    return classification


def ensure_registered(procedure_id: str, *, request_id: str | None = None) -> None:
    data = _request("GET", f"/internal/v1/procedures/{procedure_id}", timeout=5, request_id=request_id)
    if data.get("procedure_id") != procedure_id:
        raise _unavailable()
