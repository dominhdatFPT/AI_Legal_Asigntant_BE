"""Versioned, source-controlled form templates seeded into the shared form table.

The original reference-catalog PDFs are immutable. These entries are derived
fillable copies, with their own Word files and schemas, and are inserted only
when their fixed IDs are absent so administrator-published versions are kept.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text


BUILTIN_DIR = Path(__file__).resolve().parent / "builtin_forms"
BUILTIN_ROWS = (
    {
        "id": "07a24ddb-2306-555e-86d9-3e09e5b392af",
        "group_id": "b7206f96-e02d-56d4-a6c9-8ac1e22bdb66",
        "name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-dang-ky-bien-dong-mau-18.json",
        "word_file": "don-dang-ky-bien-dong-mau-18.docx",
        "reference_catalog_names": [
            "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 18)",
            "Đơn đăng ký biến động đất đai do chuyển nhượng (Mẫu 11/ĐK - Bản Word)",
            "Đơn đăng ký biến động đất đai do chuyển nhượng (Mẫu 11/ĐK - Bản PDF gốc)",
        ],
    },
    {
        "id": "8fbf5042-88f8-5f8c-b97b-d25cc34fa806",
        "group_id": "837d22cf-590d-5a54-b97a-f3a32a285483",
        "name": "Đơn đề nghị chuyển mục đích sử dụng đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "mau02-chuyen-muc-dich-su-dung-dat.json",
        "word_file": "mau02-chuyen-muc-dich-su-dung-dat.docx",
        "reference_catalog_names": [
            "Đơn đề nghị chuyển mục đích sử dụng đất (Mẫu 01/ĐK - Bản Word)",
            "Đơn đề nghị chuyển mục đích sử dụng đất (Mẫu 01/ĐK - Bản PDF gốc)",
        ],
    },
    {
        "id": "4f64505e-46bf-5a16-9535-f1e60144d330",
        "group_id": "b799974e-e10e-5dbd-ac76-56400c20d0c1",
        "name": "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "de-nghi-giao-thue-dat.json",
        "word_file": "de-nghi-giao-thue-dat.docx",
        "reference_catalog_names": [
            "Đơn đề nghị giao đất, thuê đất, cho phép chuyển mục đích sử dụng đất",
            "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản DOCX)",
            "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản có ghi chú cập nhật)",
            "Đơn xin thuê đất và phương án sản xuất kinh doanh",
        ],
    },
    {
        "id": "941aa9c0-dfa9-55a3-8f2a-6064f353583b",
        "group_id": "061a1ed0-bdb2-52e6-90e9-5084c62e6488",
        "name": "Đơn đề nghị hòa giải tranh chấp đất đai",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-de-nghi-hoa-giai-tranh-chap-dat-dai.json",
        "word_file": "don-de-nghi-hoa-giai-tranh-chap-dat-dai.docx",
        "reference_catalog_names": [
            "Đơn đề nghị hòa giải tranh chấp đất đai tại UBND cấp xã",
        ],
    },
    {
        "id": "4d748781-3064-5595-b4c2-7914e94235b6",
        "group_id": "7831b420-3c5b-59d8-a811-c327f8da1a19",
        "name": "Đơn đề nghị cấp lại Giấy chứng nhận quyền sử dụng đất bị mất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "de-nghi-cap-lai-giay-chung-nhan-bi-mat.json",
        "word_file": "de-nghi-cap-lai-giay-chung-nhan-bi-mat.docx",
        "reference_catalog_names": [
            "Đơn đề nghị cấp lại Giấy chứng nhận quyền sử dụng đất bị mất (Mẫu 10/ĐK)",
        ],
    },
    {
        "id": "2b2d239f-36af-521e-8b14-cd35501c0ccf",
        "group_id": "a190d8a6-3b29-54a2-b62c-aad0d57df7c2",
        "name": "Đơn đề nghị đính chính thông tin Giấy chứng nhận đã cấp",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-de-nghi-dinh-chinh-giay-chung-nhan.json",
        "word_file": "don-de-nghi-dinh-chinh-giay-chung-nhan.docx",
        "reference_catalog_names": [
            "Đơn đề nghị đính chính thông tin Giấy chứng nhận đã cấp",
        ],
    },
    {
        "id": "0511f021-56bb-5388-a7d8-c69a8d2a00c3",
        "group_id": "6539f24b-ad8d-5180-9a44-f678c3eb1146",
        "name": "Phiếu yêu cầu cung cấp thông tin, dữ liệu đất đai và trích lục bản đồ địa chính",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "phieu-yeu-cau-cung-cap-thong-tin-dat-dai.json",
        "word_file": "phieu-yeu-cau-cung-cap-thong-tin-dat-dai.docx",
        "reference_catalog_names": [
            "Phiếu yêu cầu cung cấp thông tin, dữ liệu đất đai và trích lục bản đồ địa chính",
        ],
    },
    {
        "id": "eab29df9-b3e3-53c5-9cae-2ed2b317e406",
        "group_id": "1d09f58d-b041-557f-a49f-02e735a2cffe",
        "name": "Đơn yêu cầu đo đạc đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-yeu-cau-do-dac-dat.json",
        "word_file": "don-yeu-cau-do-dac-dat.docx",
        "reference_catalog_names": [
            "Mẫu đơn xin đo đạc lại đất phục vụ cấp đổi Giấy chứng nhận",
        ],
    },
    {
        "id": "c62afdb9-69be-5715-8591-4e8b116af0ce",
        "group_id": "f78d3c1b-dd7a-5db3-824e-6047a78f7c70",
        "name": "Đơn đề nghị gia hạn thời hạn sử dụng đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-xin-gia-han-su-dung-dat.json",
        "word_file": "don-xin-gia-han-su-dung-dat.docx",
        "reference_catalog_names": [
            "Đơn đề nghị gia hạn thời hạn sử dụng đất",
        ],
    },
    {
        "id": "ca5e6a25-5b26-5b8b-a195-6f53d8b668c9",
        "group_id": "54d0b40a-6cf0-5d90-9f46-38fa075f6a01",
        "name": "Đơn xin xác nhận lại thời hạn sử dụng đất nông nghiệp",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-xac-nhan-thoi-han-dat-nong-nghiep.json",
        "word_file": "don-xac-nhan-thoi-han-dat-nong-nghiep.docx",
        "reference_catalog_names": [
            "Đơn xin xác nhận tiếp tục sử dụng đất nông nghiệp của hộ gia đình, cá nhân",
        ],
    },
    {
        "id": "ab9ef046-0251-51a3-96b6-bcf03fe1135e",
        "group_id": "2fbd2bf3-87f8-5ec9-9d84-5e1921cc3b87",
        "name": "Đơn đề nghị tách thửa đất, hợp thửa đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-de-nghi-tach-thua-hop-thua-dat.json",
        "word_file": "don-de-nghi-tach-thua-hop-thua-dat.docx",
        "reference_catalog_names": [
            "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK - Bản Word)",
            "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK - Bản PDF gốc)",
        ],
    },
    {
        "id": "fb807cd5-4508-5b3f-a6f0-49a098dc39af",
        "group_id": "7fd600a7-d7de-5564-a69e-5ce28212884a",
        "name": "Đơn đăng ký đất đai, tài sản gắn liền với đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-dang-ky-dat-dai-lan-dau.json",
        "word_file": "don-dang-ky-dat-dai-lan-dau.docx",
        "reference_catalog_names": [
            "Đơn đăng ký đất đai, cấp Giấy chứng nhận lần đầu (Mẫu 04/ĐK - Bản Word)",
            "Đơn đăng ký đất đai, cấp Giấy chứng nhận lần đầu (Mẫu 04/ĐK - Bản PDF gốc)",
        ],
    },
    {
        "id": "842b76aa-4a1b-585e-a304-c89f52cdc421",
        "group_id": "3d652425-a50e-5d5d-9a1e-821e40aa7276",
        "name": "Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "phieu-yeu-cau-xoa-dang-ky-the-chap.json",
        "word_file": "phieu-yeu-cau-xoa-dang-ky-the-chap.docx",
        "reference_catalog_names": [
            "Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất (Giải chấp Sổ đỏ)",
            "Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (03a)",
        ],
    },
    {
        "id": "dd3f6e78-fef9-5c5f-8838-e09e6a112b6a",
        "group_id": "12f33844-baae-5a8f-a27c-ce74fd149e3d",
        "name": "Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "phieu-dang-ky-the-chap-quyen-su-dung-dat.json",
        "word_file": "phieu-dang-ky-the-chap-quyen-su-dung-dat.docx",
        "reference_catalog_names": [
            "Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất",
            "Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (01a)",
        ],
    },
    {
        "id": "06c4029e-2456-5bc4-b95c-31b88b1d1eb2",
        "group_id": "a9a78aea-a6bb-54fc-9641-a678cdfd6a7a",
        "name": "Phương án sử dụng tầng đất mặt",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "phuong-an-su-dung-tang-dat-mat.json",
        "word_file": "phuong-an-su-dung-tang-dat-mat.docx",
        "reference_catalog_names": [
            "Phương án bóc tách, sử dụng tầng đất mặt khi chuyển mục đích sử dụng",
        ],
    },
    {
        "id": "c7948690-dad7-5a96-8529-d668e97478f4",
        "group_id": "f847651f-89ae-5f53-8711-0443dbb4128a",
        "name": "Đơn đề nghị chuyển hình thức giao đất, cho thuê đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "don-de-nghi-chuyen-hinh-thuc-giao-dat.json",
        "word_file": "don-de-nghi-chuyen-hinh-thuc-giao-dat.docx",
        "reference_catalog_names": [
            "Đơn đề nghị chuyển hình thức giao đất, cho thuê đất (Mẫu số 02)",
            "Đơn xin giao đất đối với cá nhân, tổ chức (Bản Word)",
        ],
    },
    {
        "id": "81efbc00-5f83-5cac-92ae-63deb7a1b8bf",
        "group_id": "80ac1fb8-77b1-508a-93be-bcc28413f9d6",
        "name": "Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "hop-dong-tang-cho-dat.json",
        "word_file": "hop-dong-tang-cho-dat.docx",
        "reference_catalog_names": ["Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất"],
    },
    {
        "id": "b3206c17-4dd2-5381-8e1a-35d70f910454",
        "group_id": "cd19a390-e097-5185-8862-c3b73c102119",
        "name": "Văn bản nhận tài sản thừa kế của người thừa kế duy nhất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "van-ban-nhan-tai-san-thua-ke-duy-nhat.json",
        "word_file": "van-ban-nhan-tai-san-thua-ke-duy-nhat.docx",
        "reference_catalog_names": [
            "Văn bản nhận tài sản thừa kế (người thừa kế duy nhất)",
            "Văn bản khai nhận di sản thừa kế quyền sử dụng đất",
        ],
    },
    {
        "id": "5c725543-7cc0-5b43-9c4f-a343c201aaaf",
        "group_id": "7e0c336b-90a6-554c-9588-906bf8d3baac",
        "name": "Văn bản thỏa thuận phân chia di sản thừa kế quyền sử dụng đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "van-ban-thoa-thuan-phan-chia-di-san.json",
        "word_file": "van-ban-thoa-thuan-phan-chia-di-san.docx",
        "reference_catalog_names": [
            "Văn bản thỏa thuận phân chia di sản thừa kế (tình huống hai người, phần di sản 1/2)",
            "Văn bản thỏa thuận phân chia di sản thừa kế quyền sử dụng đất",
        ],
    },
    {
        "id": "315cfed1-0a58-5379-8e73-62579c33124b",
        "group_id": "76c6c93c-e09f-500e-b55c-c181fa6597b7",
        "name": "Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền với đất",
        "domain": "dat_dai",
        "procedure_id": None,
        "content_file": "hop-dong-the-chap-quyen-su-dung-dat.json",
        "word_file": "hop-dong-the-chap-quyen-su-dung-dat.docx",
        "reference_catalog_names": [
            "Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền với đất",
            "Hợp đồng thế chấp quyền sử dụng đất bảo đảm nghĩa vụ vay vốn",
        ],
    },
    {
        "id": "803fb578-cda5-5bb7-9077-84c5d90e0c2e",
        "group_id": "bfd9be8a-f5fc-56db-9cae-3f768ee09ad7",
        "name": "Hợp đồng cho thuê mặt bằng kinh doanh",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "hop-dong-thue-mat-bang-kinh-doanh.json",
        "word_file": "hop-dong-thue-mat-bang-kinh-doanh.docx",
        "reference_catalog_names": ["Hợp đồng cho thuê mặt bằng kinh doanh"],
    },
    {
        "id": "41765aae-e937-5e0e-bd80-79b0ea74377b",
        "group_id": "ba96cd8e-9cd4-5123-8311-f5b65010ecbc",
        "name": "Giấy đề nghị đăng ký hộ kinh doanh",
        "domain": "ho_kinh_doanh",
        "procedure_id": None,
        "content_file": "giay-de-nghi-dang-ky-ho-kinh-doanh.json",
        "word_file": "giay-de-nghi-dang-ky-ho-kinh-doanh.docx",
        "reference_catalog_names": ["Giấy đề nghị đăng ký hộ kinh doanh"],
    },
    {
        "id": "c2addce4-1587-5382-aaed-9d2732a041a3",
        "group_id": "1b405ba4-3056-57f5-af7e-cb94e4a7befa",
        "name": "Thông báo về việc giải thể doanh nghiệp",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "thong-bao-giai-the-doanh-nghiep.json",
        "word_file": "thong-bao-giai-the-doanh-nghiep.docx",
        "reference_catalog_names": ["Thông báo về việc giải thể doanh nghiệp"],
    },
    {
        "id": "2b9087fd-ba00-548b-98e4-d1202b8f7228",
        "group_id": "1a42b052-e170-5068-aed3-1c18f09dc712",
        "name": "Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh",
        "domain": "ho_kinh_doanh",
        "procedure_id": None,
        "content_file": "giay-de-nghi-thay-doi-dang-ky-ho-kinh-doanh.json",
        "word_file": "giay-de-nghi-thay-doi-dang-ky-ho-kinh-doanh.docx",
        "reference_catalog_names": ["Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh"],
    },
    {
        "id": "7c8970f5-b15b-51ae-bee9-d96dd7820de2",
        "group_id": "b3afb618-3ccf-566c-b366-4d854509ec99",
        "name": "Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "to-khai-thue-gtgt-01-gtgt.json",
        "word_file": "to-khai-thue-gtgt-01-gtgt.docx",
        "reference_catalog_names": ["Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)"],
    },
    {
        "id": "ca9486f1-25b7-54e9-a216-66d6aa77225c",
        "group_id": "54fcabb6-1a88-5bc5-9ff0-d6dbe8817487",
        "name": "Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "giay-de-nghi-dang-ky-cong-ty-co-phan.json",
        "word_file": "giay-de-nghi-dang-ky-cong-ty-co-phan.docx",
        "reference_catalog_names": ["Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần"],
    },
    {
        "id": "a9a8dac5-024c-5702-91bb-e6a01bf4e531",
        "group_id": "df45b3a1-1fa7-54bd-85cd-059b5bdd5ea7",
        "name": "Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "giay-de-nghi-dang-ky-cong-ty-hop-danh.json",
        "word_file": "giay-de-nghi-dang-ky-cong-ty-hop-danh.docx",
        "reference_catalog_names": ["Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh"],
    },
    {
        "id": "f3bb51a9-f2f9-55cc-bf79-eccc85409294",
        "group_id": "e590d54b-911a-538c-b408-0e07892dce77",
        "name": "Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "giay-de-nghi-dang-ky-cong-ty-tnhh-mot-thanh-vien.json",
        "word_file": "giay-de-nghi-dang-ky-cong-ty-tnhh-mot-thanh-vien.docx",
        "reference_catalog_names": ["Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên"],
    },
    {
        "id": "bf6e5276-7854-512d-a16b-e45ed26b1fd8",
        "group_id": "8b93c3f0-bbf1-5ac1-b554-5c26dac14c9c",
        "name": "Giấy đề nghị đăng ký doanh nghiệp tư nhân",
        "domain": "doanh_nghiep",
        "procedure_id": None,
        "content_file": "giay-de-nghi-dang-ky-doanh-nghiep-tu-nhan.json",
        "word_file": "giay-de-nghi-dang-ky-doanh-nghiep-tu-nhan.docx",
        "reference_catalog_names": ["Giấy đề nghị đăng ký doanh nghiệp tư nhân"],
    },
    {
        "id": "5a8278c7-ce25-50a6-afc3-7f06bcc9702a",
        "group_id": "a6e4e940-e3d0-558d-8ec5-fcfbc0ef94c8",
        "name": "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh",
        "domain": "ho_kinh_doanh",
        "procedure_id": None,
        "content_file": "giay-de-nghi-cap-lai-giay-chung-nhan-ho-kinh-doanh.json",
        "word_file": "giay-de-nghi-cap-lai-giay-chung-nhan-ho-kinh-doanh.docx",
        "reference_catalog_names": ["Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh"],
    },
)


def seed_builtin_form_templates(conn) -> None:
    """Insert built-in forms once; never overwrite a version an admin changed."""
    all_builtin_ids = [item["id"] for item in BUILTIN_ROWS]
    try:
        res = conn.execute(
            text("SELECT id::text FROM public.custom_form_templates WHERE id = ANY(:ids)"),
            {"ids": all_builtin_ids},
        )
        existing_ids = set(res.scalars().all())
    except Exception:
        existing_ids = set()

    for item in BUILTIN_ROWS:
        if item["id"] in existing_ids:
            continue
        content = json.loads((BUILTIN_DIR / item["content_file"]).read_text(encoding="utf-8"))
        content.setdefault("reference_catalog_names", item.get("reference_catalog_names", []))
        word_data = (BUILTIN_DIR / item["word_file"]).read_bytes()
        procedure_id = item["procedure_id"]
        procedure_code = content.get("procedure_code")
        if not procedure_id and procedure_code:
            procedure_id = conn.execute(
                text("SELECT id FROM public.procedures WHERE upper(code)=upper(:code) LIMIT 1"),
                {"code": procedure_code},
            ).scalar_one_or_none()
        conn.execute(
            text(
                """INSERT INTO public.custom_form_templates
                  (id,template_group_id,version,status,name,domain,procedure_id,content,word_data,word_filename)
                  VALUES (:id,:group_id,1,'published',:name,:domain,:procedure_id,CAST(:content AS jsonb),:word_data,:word_filename)
                  ON CONFLICT DO NOTHING"""
            ),
            {
                "id": item["id"],
                "group_id": item["group_id"],
                "name": item["name"],
                "domain": item["domain"],
                "procedure_id": procedure_id,
                "content": json.dumps(content, ensure_ascii=False),
                "word_data": word_data,
                "word_filename": item["word_file"],
            },
        )
        aliases = item.get("reference_catalog_names", [])
        if aliases:
            conn.execute(
                text(
                    """UPDATE public.custom_form_templates
                       SET content = jsonb_set(
                           COALESCE(content, '{}'::jsonb),
                           '{reference_catalog_names}',
                           COALESCE((
                               SELECT jsonb_agg(alias_value ORDER BY alias_value)
                               FROM (
                                   SELECT DISTINCT value AS alias_value
                                   FROM jsonb_array_elements_text(
                                       COALESCE(content->'reference_catalog_names', '[]'::jsonb)
                                       || CAST(:aliases AS jsonb)
                                   ) AS aliases(value)
                               ) AS merged_aliases
                           ), '[]'::jsonb),
                           true
                       )
                       WHERE id = :id
                         AND EXISTS (
                             SELECT 1
                             FROM jsonb_array_elements_text(CAST(:aliases AS jsonb)) AS requested(value)
                             WHERE NOT (COALESCE(content->'reference_catalog_names', '[]'::jsonb) ? requested.value)
                         )"""
                ),
                {"id": item["id"], "aliases": json.dumps(aliases, ensure_ascii=False)},
            )
