"""Danh mục lĩnh vực (công khai) và gán lĩnh vực cho hồ sơ luật sư."""

from app.models import LawyerProfile, LawyerProfileSpecialization
from app.services import profile_service


def test_danh_sach_linh_vuc_khong_can_dang_nhap(client):
    res = client.get("/api/v1/lawyers/specializations")
    assert res.status_code == 200
    assert res.json()["data"]["items"] == [{"code": "land", "name": "Đất đai"},
                                           {"code": "business", "name": "Hộ kinh doanh"}]


def test_gan_linh_vuc_giu_cai_con_bo_cai_thua():
    profile = LawyerProfile(source="admin_created", status="active")
    kept = LawyerProfileSpecialization(specialization_code="land")
    profile.specializations = [kept, LawyerProfileSpecialization(specialization_code="tax")]
    profile_service.set_specializations(profile, ["land", "business"])
    assert profile.specialization_codes == ["land", "business"] and profile.specializations[0] is kept
