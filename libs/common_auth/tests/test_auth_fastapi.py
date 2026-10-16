import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from common_auth.fastapi_dep import add_auth_error_handler, get_current_user, require_role

app = FastAPI()
add_auth_error_handler(app)


@app.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user.id, "role": user.role}


@app.get("/lawyer-only")
def lawyer_only(user=Depends(require_role("lawyer", "admin"))):
    return {"ok": True}


client = TestClient(app)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _error(res):
    body = res.json()
    assert body["data"] is None
    assert body["request_id"] == res.headers["X-Request-ID"]
    return body["error"]


def test_khong_co_header_tra_401_unauthorized():
    res = client.get("/me")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert _error(res) == {"code": "UNAUTHORIZED", "message": "Bạn cần đăng nhập để tiếp tục."}


def test_token_het_han_tra_401_token_expired(make_token, expired):
    res = client.get("/me", headers=_auth(make_token(exp=expired)))
    assert res.status_code == 401
    assert _error(res)["code"] == "TOKEN_EXPIRED"


def test_token_ky_bang_key_khac_tra_401(make_token, other_private_key):
    res = client.get("/me", headers=_auth(make_token(signing_key=other_private_key)))
    assert res.status_code == 401
    assert _error(res)["code"] == "UNAUTHORIZED"


def test_request_id_tu_client_duoc_tra_lai():
    res = client.get("/me", headers={"X-Request-ID": "req-abc"})
    assert res.json()["request_id"] == "req-abc"
    assert res.headers["X-Request-ID"] == "req-abc"


def test_token_dung_tra_200(make_token):
    res = client.get("/me", headers=_auth(make_token(sub="7")))
    assert res.status_code == 200
    assert res.json() == {"id": "7", "role": "citizen"}


def test_sai_role_tra_403_forbidden(make_token):
    res = client.get("/lawyer-only", headers=_auth(make_token(role="citizen")))
    assert res.status_code == 403
    assert "WWW-Authenticate" not in res.headers
    assert _error(res) == {"code": "FORBIDDEN", "message": "Bạn không có quyền thực hiện thao tác này."}


@pytest.mark.parametrize("role", ["lawyer", "admin"])
def test_dung_role_tra_200(make_token, role):
    res = client.get("/lawyer-only", headers=_auth(make_token(role=role)))
    assert res.status_code == 200


def test_token_hong_tra_401_truoc_khi_xet_role(make_token, expired):
    res = client.get("/lawyer-only", headers=_auth(make_token(role="citizen", exp=expired)))
    assert res.status_code == 401


def test_quen_dang_ky_handler_van_tra_401_khong_phai_500():
    bare = FastAPI()

    @bare.get("/me")
    def bare_me(user=Depends(get_current_user)):
        return {}

    res = TestClient(bare).get("/me")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED"


# ---------- HttpOnly cookie ----------

def test_token_trong_cookie_tra_200(make_token):
    res = TestClient(app, cookies={"access_token": make_token(sub="9")}).get("/me")
    assert res.status_code == 200
    assert res.json() == {"id": "9", "role": "citizen"}


def test_cookie_het_han_tra_401_token_expired(make_token, expired):
    res = TestClient(app, cookies={"access_token": make_token(exp=expired)}).get("/me")
    assert res.status_code == 401
    assert _error(res)["code"] == "TOKEN_EXPIRED"


def test_header_bearer_uu_tien_hon_cookie(make_token):
    c = TestClient(app, cookies={"access_token": make_token(sub="cookie-user")})
    res = c.get("/me", headers=_auth(make_token(sub="header-user")))
    assert res.json()["id"] == "header-user"


def test_header_sai_dinh_dang_khong_fallback_sang_cookie(make_token):
    c = TestClient(app, cookies={"access_token": make_token()})
    res = c.get("/me", headers={"Authorization": "Basic abc"})
    assert res.status_code == 401


def test_role_check_hoat_dong_voi_cookie(make_token):
    c = TestClient(app, cookies={"access_token": make_token(role="citizen")})
    assert c.get("/lawyer-only").status_code == 403
