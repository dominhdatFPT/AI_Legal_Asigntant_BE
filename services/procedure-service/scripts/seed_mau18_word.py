"""Insert the pilot only if absent. Never modify existing templates."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
from dotenv import dotenv_values
import psycopg

ROOT = Path(__file__).resolve().parents[1]
settings = dotenv_values(ROOT.parents[1] / '.env')
url = settings['PROCEDURE_DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
parsed = urlsplit(url)
project = 'qqwuhiymixomwrdunfkz'
if not (parsed.hostname == f'db.{project}.supabase.co' or
        (parsed.hostname or '').endswith('.pooler.supabase.com') and unquote(parsed.username or '') == f'postgres.{project}'):
    raise SystemExit('Sai project Procedure; không thực hiện ghi.')
if 'sslmode=' not in url:
    url += ('&' if '?' in url else '?') + 'sslmode=require'
data = (ROOT / 'database/mau18-two-person.docx').read_bytes()
with psycopg.connect(url, connect_timeout=8, prepare_threshold=None) as conn:
    row = conn.execute('''INSERT INTO public.form_templates
        (domain, name, procedure_name, procedure_code, file_name, format, media_type, file_size, file_data, order_index, is_active)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
        ON CONFLICT (domain,file_name) DO NOTHING RETURNING id''',
        ('land', 'Đơn đăng ký biến động đất đai (Mẫu 18, hai người)',
         'Đăng ký biến động quyền sử dụng đất do chuyển nhượng', 'DD-001',
         'mau18-hai-nguoi.docx', 'DOCX',
         'application/vnd.openxmlformats-officedocument.wordprocessingml.document', len(data), data, 99)).fetchone()
    print('INSERTED' if row else 'ALREADY_EXISTS; NO CHANGE')
    preview = ROOT / 'database/mau18-reference-clean.pdf'
    if preview.exists():
        updated = conn.execute('''UPDATE public.form_templates SET preview_pdf = %s
            WHERE domain = %s AND file_name = %s AND file_data = %s AND preview_pdf IS NULL RETURNING id''',
            (preview.read_bytes(), 'land', 'mau18-hai-nguoi.docx', data)).fetchone()
        print('PREVIEW SAVED' if updated else 'PREVIEW UNCHANGED')
        saved = conn.execute('SELECT id FROM public.form_templates WHERE domain = %s AND file_name = %s AND file_data = %s AND preview_pdf = %s',
                             ('land', 'mau18-hai-nguoi.docx', data, preview.read_bytes())).fetchone()
        if saved:
            conn.execute((ROOT / 'database/007_template_preview_pages.sql').read_text(encoding='utf-8'))
            for page in range(1, 4):
                image = (ROOT / f'database/page-{page}.png').read_bytes()
                conn.execute('INSERT INTO public.form_template_preview_pages(template_id,page_number,image_data) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                             (saved[0], page, image))
            print('PREVIEW PAGES READY')
