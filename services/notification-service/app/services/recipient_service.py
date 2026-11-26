import asyncio
import os
import uuid
from typing import List, Optional

import httpx
from app.services.report_service import ReportDomainError

_http_client: Optional[httpx.AsyncClient] = None
_http_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client, _http_loop
    loop = asyncio.get_running_loop()
    if _http_client is None or _http_client.is_closed or _http_loop is not loop:
        _http_loop = loop
        _http_client = httpx.AsyncClient(
            timeout=3.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client, _http_loop
    client = _http_client
    _http_client = None
    _http_loop = None
    if client is not None and not client.is_closed:
        await client.aclose()


def _parse_uid(raw) -> str:
    try:
        return str(uuid.UUID(str(raw).strip()))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ReportDomainError(422, "INVALID_RECIPIENT", "Mã người nhận không hợp lệ.") from exc


def _check_user(user: dict | None, audience: str) -> dict:
    if user is None:
        raise ReportDomainError(422, "INVALID_RECIPIENT", "Người nhận không tồn tại.")
    if user.get("deleted_at") or user.get("status") != "active":
        raise ReportDomainError(422, "INVALID_RECIPIENT", "Tài khoản không hoạt động.")
    if audience != "all" and user.get("role") != audience:
        raise ReportDomainError(422, "RECIPIENT_ROLE_MISMATCH", "Vai trò người nhận không khớp bộ lọc.")
    return user


async def validate_recipient(
    recipient_id: str,
    audience: str,
    request,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    parsed_uid = _parse_uid(recipient_id)

    headers = {key: request.headers[key] for key in ("authorization", "cookie", "x-request-id") if key in request.headers}
    http = client or _get_http_client()
    base_url = os.getenv("USER_SERVICE_URL", "http://user-service:8000").rstrip("/")
    try:
        response = await http.get(f"{base_url}/api/v1/users/{parsed_uid}", headers=headers)
    except httpx.HTTPError as exc:
        raise ReportDomainError(503, "RECIPIENT_LOOKUP_UNAVAILABLE", "Chưa kiểm tra được người nhận.") from exc
    if response.status_code == 404:
        return _check_user(None, audience)
    if response.status_code != 200:
        raise ReportDomainError(503, "RECIPIENT_LOOKUP_UNAVAILABLE", "Chưa kiểm tra được người nhận.")
    try:
        user = response.json()["data"]
        if not isinstance(user, dict):
            raise ValueError("Invalid response")
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportDomainError(503, "RECIPIENT_LOOKUP_UNAVAILABLE", "Chưa kiểm tra được người nhận.") from exc
    return _check_user(user, audience)


async def _lookup_users(uids: list[str], request) -> dict[str, dict] | None:
    token = os.getenv("INTERNAL_TOKEN", "")
    if not token:
        return None
    headers = {"X-Internal-Token": token}
    if rid := request.headers.get("x-request-id"):
        headers["X-Request-ID"] = rid
    base_url = os.getenv("USER_SERVICE_URL", "http://user-service:8000").rstrip("/")
    url = f"{base_url}/internal/v1/users/lookup"
    http = _get_http_client()
    found = {}
    for offset in range(0, len(uids), 100):
        try:
            # An explicit request bypasses the shared client's cookie jar.
            lookup = httpx.Request("POST", url, headers=headers, json={"user_ids": uids[offset:offset + 100]})
            response = await http.send(lookup, auth=None, follow_redirects=False)
            if response.status_code in (404, 405):
                return None
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Invalid lookup status")
            items = response.json()["data"]["items"]
            if not isinstance(items, list) or not all(
                isinstance(item, dict) and all(isinstance(item.get(field), str) and item[field]
                                               for field in ("id", "status", "role"))
                for item in items
            ):
                raise ValueError("Invalid lookup items")
            found.update((str(item["id"]), item) for item in items)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, KeyError, TypeError) as exc:
            raise ReportDomainError(503, "RECIPIENT_LOOKUP_UNAVAILABLE", "Chưa kiểm tra được người nhận.") from exc
    return found


async def validate_recipients(recipient_ids: List[str], audience: str, request) -> List[dict]:
    unique_ids = list(dict.fromkeys(_parse_uid(r) for r in recipient_ids if r and str(r).strip()))
    if not unique_ids:
        return []
    found = await _lookup_users(unique_ids, request)
    if found is not None:
        return [_check_user(found.get(uid), audience) for uid in unique_ids]
    client = _get_http_client()
    sem = asyncio.Semaphore(5)

    async def _check(uid: str) -> dict:
        async with sem:
            return await validate_recipient(uid, audience, request, client=client)

    return list(await asyncio.gather(*(_check(uid) for uid in unique_ids)))
