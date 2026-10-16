import jwt
import pytest

from common_auth import (
    TOKEN_EXPIRED,
    UNAUTHORIZED,
    AuthError,
    CurrentUser,
    decode_access_token,
    extract_bearer,
)


def test_extract_bearer_lay_dung_token():
    assert extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"


@pytest.mark.parametrize("header", [None, "", "abc.def.ghi", "Basic abc", "bearer abc", "Bearer ", "Bearer    "])
def test_extract_bearer_header_sai(header):
    with pytest.raises(AuthError) as err:
        extract_bearer(header)
    assert (err.value.status, err.value.code) == (401, UNAUTHORIZED)


def test_token_hop_le_tra_ve_current_user(make_token):
    user = decode_access_token(make_token(sub="42", role="lawyer"))
    assert user == CurrentUser(id="42", role="lawyer")


def test_doc_public_key_tu_file(monkeypatch, tmp_path, key_pair, make_token):
    key_file = tmp_path / "jwt_public.pem"
    key_file.write_text(key_pair[1], encoding="utf-8")
    monkeypatch.delenv("JWT_PUBLIC_KEY")
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(key_file))
    assert decode_access_token(make_token()).id == "123"


def test_public_key_viet_tren_mot_dong(monkeypatch, key_pair, make_token):
    monkeypatch.setenv("JWT_PUBLIC_KEY", key_pair[1].strip().replace("\n", "\\n"))
    assert decode_access_token(make_token()).role == "citizen"


def _assert_error(token, code):
    with pytest.raises(AuthError) as err:
        decode_access_token(token)
    assert (err.value.status, err.value.code) == (401, code)


def test_token_het_han(make_token, expired):
    _assert_error(make_token(exp=expired), TOKEN_EXPIRED)


def test_token_ky_bang_key_khac(make_token, other_private_key):
    _assert_error(make_token(signing_key=other_private_key), UNAUTHORIZED)


def test_token_bi_sua_payload(make_token):
    header, _, signature = make_token().split(".")
    fake_payload = jwt.utils.base64url_encode(b'{"sub":"123","role":"admin"}').decode()
    _assert_error(f"{header}.{fake_payload}.{signature}", UNAUTHORIZED)


def test_token_sai_issuer(make_token):
    _assert_error(make_token(iss="hacker-service"), UNAUTHORIZED)


@pytest.mark.parametrize("field", ["sub", "role", "exp", "iss"])
def test_token_thieu_truong_bat_buoc(make_token, field):
    _assert_error(make_token(drop=[field]), UNAUTHORIZED)


def test_refresh_token_khong_dung_thay_access_token(make_token):
    _assert_error(make_token(type="refresh"), UNAUTHORIZED)


def test_token_alg_none_bi_chan():
    unsigned = jwt.encode({"sub": "1", "role": "admin", "type": "access"}, None, algorithm="none")
    _assert_error(unsigned, UNAUTHORIZED)


def test_chuoi_rac():
    _assert_error("day.khong.phai-jwt", UNAUTHORIZED)


def test_thieu_cau_hinh_public_key_la_loi_server(monkeypatch, make_token):
    token = make_token()
    monkeypatch.delenv("JWT_PUBLIC_KEY")
    with pytest.raises(RuntimeError):
        decode_access_token(token)
