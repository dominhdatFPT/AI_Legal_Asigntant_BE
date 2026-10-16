from urllib.parse import quote, urlsplit, parse_qs, urlencode
import hashlib
import io
import re

import httpx

from app.core.config import Settings
from app.core.errors import DocumentError
from app.core.downloads import download_filename

MIME_TYPES = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "image/jpeg", "image/png"}


class Storage:
    def __init__(self, settings: Settings, *, transport=None):
        self.settings = settings
        self.transport = transport

    def _object_request(self, method, path, *, content=None, json=None, media_type=None):
        headers = {"apikey": self.settings.storage_key, "x-upsert": "false", "cache-control": "no-store, max-age=0"}
        if not self.settings.storage_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.settings.storage_key}"
        if media_type:
            headers["Content-Type"] = media_type
        try:
            with httpx.Client(timeout=self.settings.timeout_seconds, transport=self.transport,
                              follow_redirects=False, trust_env=False) as client:
                response = client.request(method, self.settings.supabase_url + "/storage/v1" + path,
                                          content=content, json=json, headers=headers)
                response.raise_for_status()
                return response
        except httpx.TimeoutException:
            raise DocumentError("STORAGE_TIMEOUT", "Storage Document quá thời gian phản hồi.", 504) from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or 300 <= exc.response.status_code < 400:
                raise DocumentError("STORAGE_BAD_RESPONSE", "Storage Document phản hồi không hợp lệ.", 502) from None
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Storage không chấp nhận thao tác lưu file.") from None
        except httpx.HTTPError:
            raise DocumentError("STORAGE_UNAVAILABLE", "Không thể truy cập Storage Document.") from None

    def put_quarantine(self, key, content, media_type):
        if not key.startswith("quarantine/") or ".." in key or "\\" in key:
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Đường dẫn quarantine không hợp lệ.")
        self._object_request("POST", "/object/" + quote(self.settings.storage_bucket, safe="") + "/"
                             + quote(key, safe="/"), content=content, media_type=media_type)

    def remove_quarantine(self, key):
        if not key.startswith("quarantine/") or ".." in key or "\\" in key:
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Đường dẫn quarantine không hợp lệ.")
        self._object_request("DELETE", "/object/" + quote(self.settings.storage_bucket, safe=""), json={"prefixes": [key]})

    def put_safe(self, key, content, media_type):
        if not key.startswith("safe/") or not 1 <= len(content) <= 9437184 or media_type not in MIME_TYPES:
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Bản an toàn không hợp lệ.")
        path=self._object_path(key)
        try:
            self._object_request("POST", "/object/"+path,content=content,media_type=media_type)
        except DocumentError as exc:
            if exc.code!="STORAGE_CONFIGURATION_ERROR": raise
            # Retry after upload/commit ambiguity: accept ONLY verified same bytes.
            if self.read_verified(key,len(content),hashlib.sha256(content).hexdigest(),media_type)!=content:
                raise DocumentError("STORAGE_BAD_RESPONSE","Bản an toàn không khớp.",502) from None

    def _object_path(self, key):
        if (not isinstance(key, str) or not re.fullmatch(r"(?:quarantine|safe)/[a-zA-Z0-9_./-]+", key)
                or any(part in {"", ".", ".."} for part in key.split("/"))):
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Đường dẫn file không hợp lệ.")
        return quote(self.settings.storage_bucket, safe="") + "/" + quote(key, safe="/")

    def signed_download(self, key, filename, *, ttl=300):
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 300:
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Thời hạn tải không hợp lệ.")
        path = self._object_path(key)
        response = self._object_request("POST", "/object/sign/" + path, json={"expiresIn": ttl})
        try:
            data = response.json()
            value = data["signedURL"]
            if not isinstance(value, str) or len(value) > 8192:
                raise ValueError
            parsed = urlsplit(value)
            base = urlsplit(self.settings.supabase_url)
            expected = "/object/sign/" + path
            if parsed.fragment or parsed.username or parsed.password:
                raise ValueError
            if parsed.netloc:
                if parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.path != "/storage/v1" + expected:
                    raise ValueError
            elif parsed.scheme or parsed.path not in {expected, "/storage/v1" + expected}:
                raise ValueError
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (set(query) - {"token", "download"} or len(query.get("token", [])) != 1
                    or not query["token"][0] or any(ord(ch) < 32 for ch in query["token"][0])):
                raise ValueError
            # Rebuild from the trusted base/path, not an arbitrary provider URL or query.
            return self.settings.supabase_url + "/storage/v1" + expected + "?" + urlencode(
                {"token": query["token"][0], "download": download_filename(filename)})
        except (ValueError, KeyError, TypeError):
            raise DocumentError("STORAGE_BAD_RESPONSE", "Storage trả link tải không hợp lệ.", 502) from None

    def read_verified(self, key, expected_size, expected_sha256, media_type):
        if (not isinstance(expected_size, int) or not 1 <= expected_size <= 9437184
                or not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
                or media_type not in MIME_TYPES):
            raise DocumentError("STORAGE_BAD_RESPONSE", "Metadata file không hợp lệ.", 502)
        path = self._object_path(key)
        headers = {"apikey": self.settings.storage_key, "Accept-Encoding": "identity"}
        if not self.settings.storage_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.settings.storage_key}"
        try:
            with httpx.Client(timeout=self.settings.timeout_seconds, transport=self.transport,
                              follow_redirects=False, trust_env=False) as client:
                with client.stream("GET", self.settings.supabase_url + "/storage/v1/object/authenticated/" + path,
                                   headers=headers) as response:
                    response.raise_for_status()
                    length = response.headers.get("Content-Length")
                    if ((length is not None and int(length) != expected_size)
                            or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                            or response.headers.get("Content-Type", "").split(";", 1)[0].lower() != media_type):
                        raise ValueError
                    output, digest, size = io.BytesIO(), hashlib.sha256(), 0
                    for chunk in response.iter_bytes(chunk_size=65536):
                        size += len(chunk)
                        if size > expected_size:
                            raise ValueError
                        output.write(chunk)
                        digest.update(chunk)
                    if size != expected_size or digest.hexdigest() != expected_sha256:
                        raise ValueError
                    return output.getvalue()
        except httpx.TimeoutException:
            raise DocumentError("STORAGE_TIMEOUT", "Storage Document quá thời gian phản hồi.", 504) from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or 300 <= exc.response.status_code < 400:
                raise DocumentError("STORAGE_BAD_RESPONSE", "Storage Document phản hồi không hợp lệ.", 502) from None
            raise DocumentError("STORAGE_UNAVAILABLE", "Không thể lấy nội dung file từ Storage.") from None
        except httpx.DecodingError:
            raise DocumentError("STORAGE_BAD_RESPONSE", "Storage trả mã hóa nội dung không hợp lệ.", 502) from None
        except httpx.HTTPError:
            raise DocumentError("STORAGE_UNAVAILABLE", "Không thể đọc đầy đủ file từ Storage.") from None
        except ValueError:
            raise DocumentError("STORAGE_BAD_RESPONSE", "Nội dung file không khớp metadata.", 502) from None

    def check_bucket(self):
        headers = {"apikey": self.settings.storage_key}
        # Supabase sb_secret is an API key, NOT a JWT. Legacy service_role is a JWT.
        if not self.settings.storage_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.settings.storage_key}"
        try:
            with httpx.Client(timeout=self.settings.timeout_seconds, transport=self.transport,
                              follow_redirects=False, trust_env=False) as client:
                response = client.get(self.settings.supabase_url + "/storage/v1/bucket/"
                                      + quote(self.settings.storage_bucket, safe=""), headers=headers)
                response.raise_for_status()
                bucket = response.json()
        except httpx.TimeoutException:
            raise DocumentError("STORAGE_TIMEOUT", "Storage Document quá thời gian phản hồi.", 504) from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or 300 <= exc.response.status_code < 400:
                raise DocumentError("STORAGE_BAD_RESPONSE", "Storage Document phản hồi không hợp lệ.", 502) from None
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Không thể truy cập bucket bằng cấu hình Storage hiện tại.") from None
        except httpx.HTTPError:
            raise DocumentError("STORAGE_UNAVAILABLE", "Không thể kiểm tra Storage Document.") from None
        except ValueError:
            raise DocumentError("STORAGE_BAD_RESPONSE", "Storage Document phản hồi không hợp lệ.", 502) from None
        if (not isinstance(bucket, dict) or bucket.get("id") != self.settings.storage_bucket
                or bucket.get("public") is not False or bucket.get("file_size_limit") != 9437184
                or not isinstance(bucket.get("allowed_mime_types"), list)
                or not all(isinstance(mime, str) for mime in bucket["allowed_mime_types"])
                or set(bucket.get("allowed_mime_types") or []) != MIME_TYPES):
            raise DocumentError("STORAGE_CONFIGURATION_ERROR", "Bucket Document không đúng cấu hình riêng tư/giới hạn file.")
        return {"bucket": self.settings.storage_bucket, "private": True}
