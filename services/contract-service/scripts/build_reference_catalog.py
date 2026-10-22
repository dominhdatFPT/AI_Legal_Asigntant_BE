"""Package the reviewed source inventory for read-only viewing, never for filling.

Uses immutable prepared PDFs and renders reader pages at 216 DPI. Source files,
cloud catalog versions, personal drafts and signature areas are never modified.
Run from any directory with --sync-frontend to export the same snapshot to FE.
"""
import argparse
import hashlib
import json
import shutil
import pymupdf
from pathlib import Path
from uuid import UUID, uuid5

SERVICE = Path(__file__).resolve().parents[1]
BACKEND = SERVICE.parents[1]
WORKSPACE = BACKEND.parent
LOCAL = SERVICE / 'templates/.local-c2'
NAMESPACE = UUID('2e99226d-5a6e-4acc-9fdb-05b31283100f')


def spec(name, classification, description, *, notes=(), status='source', related=()):
    return dict(name=name, classification=classification, description=description,
                notes=list(notes), status=status, related=list(related))


# Names come from the visible document heading/body, not the Drive filename.
SPECS = {
    1: spec('Hợp đồng cho thuê mặt bằng kinh doanh', 'DN-006', 'Thỏa thuận về mặt bằng thuê, thời hạn, giá thuê và quyền, nghĩa vụ của hai bên.'),
    2: spec('Giấy đề nghị đăng ký hộ kinh doanh', 'HKD-001', 'Giấy đề nghị đăng ký hộ kinh doanh, kèm nội dung kê khai và hướng dẫn.'),
    3: spec('Giấy đề nghị đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã', 'coop-branch-registration', 'Biểu mẫu dành cho hợp tác xã, gồm thông tin địa điểm và người đứng đầu.', notes=['Tên file Drive là “Mẫu đề nghị hợp tác”; nội dung thực tế là biểu mẫu của hợp tác xã.']),
    4: spec('Thông báo về việc giải thể doanh nghiệp', 'DN-009', 'Thông báo giải thể doanh nghiệp theo nội dung bản nguồn, mẫu số 30.'),
    5: spec('Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh', 'HKD-002', 'Gồm các trang thay đổi tên, trụ sở, chủ hộ, thành viên, vốn, ngành nghề và thông tin đăng ký thuế.', notes=['Đây là biểu mẫu hộ kinh doanh, không phải mẫu thay đổi đăng ký doanh nghiệp.']),
    6: spec('Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)', 'vat-declaration', 'Tờ khai GTGT, các chỉ tiêu tính thuế và hướng dẫn kê khai trong bản nguồn.', notes=['Không gắn vào thủ tục đăng ký thuế ban đầu chỉ vì tên file là “Mẫu tờ khai thuế”.']),
    7: spec('Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần', 'DN-003', 'Biểu mẫu đăng ký công ty cổ phần, gồm thông tin thành lập, cổ phần và đăng ký thuế.'),
    8: spec('Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh', 'DN-008', 'Biểu mẫu đăng ký công ty hợp danh, gồm thông tin thành viên và đăng ký thuế.', notes=['Bản nguồn ghi Phụ lục I-5 và Thông tư 01/2021/TT-BKHĐT; trang chỉ hiển thị nguồn, không xác nhận đây là phiên bản đang được cơ quan tiếp nhận sử dụng.']),
    9: spec('Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên', 'DN-004', 'Biểu mẫu dành riêng cho công ty trách nhiệm hữu hạn một thành viên.', notes=['Không dùng tên chung cho mẫu công ty TNHH hai thành viên trở lên.']),
    10: spec('Giấy đề nghị đăng ký doanh nghiệp tư nhân', 'DN-005', 'Biểu mẫu đăng ký doanh nghiệp tư nhân, kèm các trang thông tin và hướng dẫn.'),
    11: spec('Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh', 'household-certificate-reissue', 'Đề nghị cấp lại giấy chứng nhận và kê khai lý do cấp lại.', notes=['Nội dung là hộ kinh doanh; không gắn vào DN-002 về cấp lại giấy chứng nhận đăng ký doanh nghiệp.']),
    12: spec('Giấy ủy quyền thực hiện thủ tục bảo hiểm xã hội', 'social-insurance-authorization', 'Giấy ủy quyền có mã số BHXH, loại chế độ được hưởng và phạm vi ủy quyền.', notes=['Tên file Drive ghi “Giay_Uy_Quyen_Kinh_Doanh”; nội dung là BHXH, không phải ủy quyền đăng ký doanh nghiệp.']),
    13: spec('Phương án sử dụng tầng đất mặt', 'DD-008', 'Phương án sử dụng tầng đất mặt; bản nguồn có thông tin người được giao đất, cho thuê đất và phương án sử dụng.'),
    14: spec('Đơn đề nghị chuyển mục đích sử dụng đất (Mẫu số 02)', 'DD-008', 'Đề nghị chuyển mục đích sử dụng đất, gồm thông tin khu đất và hướng dẫn.'),
    15: spec('Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản DOCX)', 'land-allocation-rental', 'Đơn đề nghị dùng cho giao đất, thuê đất, chuyển mục đích sử dụng đất và các trường hợp đất gắn với rừng theo chú thích của nguồn.', related=['DD-008'], notes=['Bản này khác nguồn số 20; giữ hai biến thể riêng, không tự kết luận bản nào thay thế bản nào.']),
    16: spec('Đơn yêu cầu đo đạc đất', 'land-survey', 'Đơn yêu cầu đo đạc mảnh đất, có phần thông tin người yêu cầu và nội dung đề nghị.', notes=['Tên file nói đo đạc lại đất; tiêu đề trong mẫu là “Đơn yêu cầu đo đạc đất”. Không tự gắn vào thủ tục cấp đổi giấy chứng nhận.']),
    17: spec('Đơn xin gia hạn sử dụng đất (Mẫu số 08)', 'DD-009', 'Kê khai thông tin đất đang sử dụng, thời hạn gia hạn và giấy tờ liên quan.'),
    18: spec('Đơn xin xác nhận lại thời hạn sử dụng đất nông nghiệp (Mẫu số 09)', 'agricultural-land-term-confirmation', 'Đề nghị xác nhận lại thời hạn sử dụng đất nông nghiệp, có phần xác nhận của cơ quan tiếp nhận.'),
    19: spec('Đơn đề nghị chuyển hình thức giao đất, cho thuê đất (Mẫu số 02)', 'land-allocation-method-change', 'Bản ảnh đơn đề nghị chuyển hình thức giao đất hoặc cho thuê đất.', notes=['Tên file Drive là “Đơn xin giao đất02.png”; tiêu đề thật là đơn đề nghị chuyển hình thức giao đất/cho thuê đất. Nguồn là ảnh nhỏ, có thể kém rõ khi phóng lớn.']),
    20: spec('Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản có ghi chú cập nhật)', 'land-allocation-rental', 'Biểu mẫu số 01 có các chú thích cập nhật được in màu trong nguồn; giữ nguyên cả phần hướng dẫn.', related=['DD-008'], notes=['Giữ nguyên ghi chú màu đỏ và phần giải thích trong nguồn, không tự chỉnh sửa hoặc xác nhận hiệu lực văn bản.']),
    21: spec('Đơn đề nghị hòa giải tranh chấp đất đai', 'land-dispute-mediation', 'Đơn trình bày nội dung tranh chấp, yêu cầu hòa giải và tài liệu gửi kèm.'),
    22: spec('Đơn đề nghị tách thửa đất, hợp thửa đất (Mẫu số 21)', 'DD-003', 'Biểu mẫu tách, hợp thửa, phần ý kiến của cơ quan đăng ký và hướng dẫn viết đơn.'),
    23: spec('Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (03a)', 'DD-011', 'Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm, gồm phần kê khai, xác nhận và hướng dẫn.'),
    24: spec('Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất', 'DD-006', 'Bản nguồn hợp đồng tặng cho, có các nhánh cá nhân, đồng sở hữu và phần lời chứng.', status='needs_review', notes=['Cần rà soát trước khi sử dụng: số điều bị lặp/nhảy; phần giấy tờ sở hữu ghi Bên B trong khi đối tượng thuộc Bên A; trách nhiệm thuế/lệ phí chưa thống nhất. Trang giữ nguyên bản nguồn, không coi đây là mẫu đã duyệt.']),
    25: spec('Văn bản nhận tài sản thừa kế (người thừa kế duy nhất)', 'DD-006', 'Bản nguồn dành cho tình huống người kê khai là người thừa kế duy nhất theo pháp luật.', status='specific_case', notes=['Không dùng như mẫu chung cho mọi trường hợp thừa kế. Giữ nguyên dòng xác nhận người thừa kế duy nhất và thông tin nguồn ở chân trang.']),
    26: spec('Văn bản thỏa thuận phân chia di sản thừa kế (tình huống hai người, phần di sản 1/2)', 'DD-006', 'Biến thể có hai người thừa kế và giả định một phần hai quyền sử dụng đất, tài sản gắn liền với đất.', status='specific_case', notes=['Bản nguồn cố định tình huống và tỷ lệ di sản; không tự thay tỷ lệ hoặc coi áp dụng cho mọi gia đình.']),
    27: spec('Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 18)', 'DD-001', 'Biểu mẫu đăng ký biến động, có trang hướng dẫn và bảng danh sách những người sử dụng đất.', related=['DD-006'], notes=['Mẫu nguồn là đơn đăng ký biến động chung; trong dự án được liên kết cả nhóm chuyển nhượng và nhóm tặng cho, thừa kế.']),
    28: spec('Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền với đất', 'DD-010', 'Bản nguồn hợp đồng thế chấp, gồm phần thông tin các bên, tài sản và lời chứng.', status='needs_review', notes=['Cần rà soát trước khi sử dụng: nhiều đoạn nói căn hộ chung cư trong khi tiêu đề nói quyền sử dụng đất; số Điều 9 bị lặp. Trang giữ nguyên bản nguồn, không coi đây là mẫu đã duyệt.']),
    29: spec('Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (01a)', 'DD-010', 'Phiếu yêu cầu đăng ký biện pháp bảo đảm, gồm các phần mô tả tài sản, chữ ký và hướng dẫn.', notes=['Giữ đủ chín trang theo nguồn, gồm cả trang cuối trắng; không tự xóa trang hoặc phần dành cho cơ quan đăng ký.']),
    30: spec('Đơn đăng ký đất đai, tài sản gắn liền với đất (Mẫu số 15)', 'DD-004', 'Đơn đăng ký đất đai, tài sản gắn liền với đất, kèm phần hướng dẫn kê khai.'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def immutable_copy(source, destination):
    data = source.read_bytes()
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError(f'Existing immutable asset differs: {destination.name}')
    if not destination.exists():
        destination.write_bytes(data)
    return digest(data)


def build(sync_frontend=False):
    manifest = json.loads((LOCAL / 'procedure-prepared/manifest.json').read_text(encoding='utf-8-sig'))
    inventory = json.loads((BACKEND / 'docs/api/DRIVE_TEMPLATE_INVENTORY.json').read_text(encoding='utf-8'))
    sources = {r['local_manifest_number']: r for r in inventory['items']}
    texts = {r['number']: r for r in json.loads((LOCAL / 'procedure-qa/text-audit.json').read_text(encoding='utf-8'))}
    registry_path = BACKEND / 'services/procedure-service/app/reference_classifications.json'
    registry_bytes = registry_path.read_bytes()
    registry = json.loads(registry_bytes)
    classifications = {r['key']: r for r in registry['items']}
    if len(manifest) != 30 or set(sources) != set(SPECS):
        raise ValueError('Expected exactly the 30 reviewed sources')
    output = SERVICE / 'templates/reference-catalog'
    output.mkdir(parents=True, exist_ok=True)
    assets = {}
    items = []
    for number, source in enumerate(manifest, 1):
        entry = SPECS[number]
        drive = sources[number]
        primary = classifications[entry['classification']]
        groups = [primary] + [classifications[key] for key in entry['related']]
        if any(group['domain'] != primary['domain'] for group in groups):
            raise ValueError('Cross-domain classification')
        original = BACKEND / 'services/procedure-service' / source['source']
        if digest(original.read_bytes()) != source['source_sha256'] or original.stat().st_size != drive['size']:
            raise ValueError(f'Source changed: {number}')
        prefix = source['source_sha256'][:16]
        pdf_source = LOCAL / 'procedure-prepared' / source['prepared_file']
        if digest(pdf_source.read_bytes()) != source['prepared_sha256']:
            raise ValueError(f'Prepared PDF changed: {number}')
        pdf_name = prefix + '.pdf'
        assets[pdf_name] = immutable_copy(pdf_source, output / pdf_name)
        pages, dimensions = [], []
        with pymupdf.open(pdf_source) as document:
            if len(document) != source['pages']:
                raise ValueError(f'Page count changed: {number}')
            for page, pdf_page in enumerate(document, 1):
                # Audit thumbnails are too small for the full-width reader.
                # Render the unchanged PDF, and give new pixels a new URL so
                # browsers cannot reuse the old blurry cached image.
                pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
                image_bytes = pix.tobytes('png')
                image_hash = digest(image_bytes)
                image_name = f'{prefix}-page-{page}-{image_hash[:12]}.png'
                destination = output / image_name
                if destination.exists() and destination.read_bytes() != image_bytes:
                    raise ValueError(f'Existing immutable asset differs: {image_name}')
                if not destination.exists():
                    destination.write_bytes(image_bytes)
                assets[image_name] = image_hash
                pages.append('/reference-templates/' + image_name)
                dimensions.append({'width': pix.width, 'height': pix.height})
        item_id = 'ff94ef6b-c507-5ca0-8fac-2bc1876e8d45' if number == 1 else str(uuid5(NAMESPACE, drive['id']))
        items.append({
            'id': item_id, 'name': entry['name'], 'description': entry['description'],
            'domain': primary['domain'], 'procedure': primary['key'], 'procedure_code': primary['code'],
            'procedure_id': primary['procedure_id'], 'procedure_name': primary['name'], 'procedures': groups,
            'page_count': source['pages'], 'format': 'PDF', 'pdf': '/reference-templates/' + pdf_name,
            'pages': pages, 'page_dimensions': dimensions, 'text': texts[number]['text'],
            'source_id': drive['id'], 'source_filename': drive['name'], 'source_url': drive['url'],
            'source_sha256': source['source_sha256'], 'pdf_sha256': source['prepared_sha256'],
            'content_status': entry['status'], 'review_notes': entry['notes'], 'view_only': True, 'can_fill': False,
        })
    snapshot = {
        'schema_version': 1, 'read_only': True, 'source_folder_url': inventory['folder_url'],
        'domains': registry['domains'], 'classifications': registry['items'],
        'classifications_sha256': digest(registry_bytes),
        'counts': {'templates': len(items), 'pages': sum(i['page_count'] for i in items)},
        'items': items, 'asset_hashes': assets,
    }
    snapshot['revision'] = digest(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode())
    (output / 'catalog.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    if sync_frontend:
        frontend = WORKSPACE / 'frontend/AI_Legal/public/reference-templates'
        frontend.mkdir(parents=True, exist_ok=True)
        for name in [*assets, 'catalog.json']:
            shutil.copyfile(output / name, frontend / name)
    print(json.dumps({'templates': len(items), 'pages': snapshot['counts']['pages'],
                      'assets': len(assets), 'frontend_synced': sync_frontend, 'revision': snapshot['revision']}))
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sync-frontend', action='store_true')
    args = parser.parse_args()
    build(args.sync_frontend)
