from unittest.mock import Mock
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.models import LawyerApplication, LawyerProfile
from test_admin import LAWYERS, _pending


def test_approve_flush_conflict_keeps_application_pending(client, db, upstream, auth, monkeypatch):
    admin = auth(upstream.user(role="admin"), "admin")
    uid, application = _pending(client, upstream, auth)
    upstream.calls.clear()
    flush = Mock(side_effect=IntegrityError("INSERT", {}, RuntimeError("duplicate bar card")))
    with db.no_autoflush, monkeypatch.context() as patch:
        patch.setattr(db, "flush", flush)
        response = client.post(f"{LAWYERS}/{application['id']}/approve", headers=admin)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BAR_CARD_NUMBER_TAKEN"
    flush.assert_called_once_with()
    assert not any(method == "PUT" and path.endswith("/role") for method, path in upstream.calls)
    assert db.get(LawyerApplication, UUID(application["id"])).status == "pending"
    assert db.query(LawyerProfile).filter_by(user_id=uid).first() is None
