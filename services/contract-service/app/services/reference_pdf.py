"""Inspect a public PDF and create bounded page images without modifying its bytes."""
import hashlib
from pathlib import PurePosixPath
from uuid import uuid4

import pymupdf

from app.core.errors import ContractError

MAX_PDF_BYTES = 10 * 1024 * 1024


def prepare_reference_pdf(data, filename, name, domain, classification):
    filename = PurePosixPath((filename or '').replace('\\', '/')).name
    if not filename.lower().endswith('.pdf') or len(filename) > 255 or '\x00' in filename or not data.startswith(b'%PDF-'):
        raise ContractError('INVALID_PDF', 'Chọn một tệp PDF hợp lệ.', 422)
    if not data or len(data) > MAX_PDF_BYTES:
        raise ContractError('PDF_TOO_LARGE', 'Tệp PDF phải có dung lượng từ 1 byte đến 10 MB.', 422)
    template_id = str(uuid4())
    images, dimensions, texts = [], [], []
    total_bytes = 0
    try:
        with pymupdf.open(stream=data, filetype='pdf') as document:
            if document.needs_pass or not 1 <= len(document) <= 100:
                raise ContractError('INVALID_PDF', 'PDF phải có 1–100 trang và không được đặt mật khẩu.', 422)
            for page in document:
                if page.rect.is_empty or page.rect.is_infinite:
                    raise ValueError('Invalid page size')
                scale = min(2, 1600 / max(page.rect.width, page.rect.height))
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                image = pixmap.tobytes('png')
                total_bytes += len(image)
                if total_bytes > 50 * 1024 * 1024:
                    raise ContractError('PDF_PREVIEW_TOO_LARGE', 'PDF có quá nhiều trang ảnh. Hãy chia thành các tệp nhỏ hơn.', 422)
                images.append(image)
                dimensions.append({'width': pixmap.width, 'height': pixmap.height})
                texts.append(page.get_text()[:20000])
    except ContractError:
        raise
    except Exception:
        raise ContractError('INVALID_PDF', 'Không đọc được nội dung PDF. Hãy kiểm tra lại tệp.', 422) from None
    digest = hashlib.sha256(data).hexdigest()
    procedure = {key: classification.get(key) for key in ('key', 'code', 'name')}
    item = {
        'id': template_id, 'number': 0, 'name': name.strip(),
        'description': f"Mẫu PDF thuộc thủ tục: {classification['name']}",
        'domain': domain, 'procedure': classification['key'],
        'procedure_code': classification.get('code'), 'procedure_name': classification['name'],
        'procedures': [procedure], 'page_count': len(images), 'format': 'PDF',
        'pdf': f'/api/reference-assets/{template_id}.pdf',
        'pages': [f'/api/reference-assets/{template_id}-page-{i + 1}.png' for i in range(len(images))],
        'page_dimensions': dimensions, 'text': '\n'.join(texts)[:200000],
        'content_status': 'source', 'review_notes': [], 'source_id': f'upload:{template_id}',
        'source_filename': filename, 'source_url': None,
        'source_sha256': digest, 'pdf_sha256': digest, 'view_only': True, 'can_fill': False,
        'uploaded': True,
    }
    return item, images
