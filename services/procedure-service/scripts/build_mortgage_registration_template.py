"""Build a fillable request to register a land/property security measure."""
from __future__ import annotations
import json
from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/"app"/"builtin_forms"; SLUG="phieu-dang-ky-the-chap-quyen-su-dung-dat"
HELP="Tự lấy từ hồ sơ CCCD; không thể sửa thông tin gốc trong đơn."
def field(key,label,word_key,kind="text",required=False,*,autofill=None,help_text="",options=None):
 d={"key":key,"label":label,"word_key":word_key,"type":kind,"required":required}
 if autofill:d["autofill"]=autofill
 if help_text:d["help_text"]=help_text
 if options:d["options"]=options
 return d

def content():
 party_fields=[field("name","Họ tên / tên tổ chức","party_name",required=True),field("address","Địa chỉ","party_address","textarea",True),
   field("identity_type","Loại giấy tờ pháp lý","party_identity_type","select",True,options=["CCCD / số định danh","Hộ chiếu","Thẻ thường trú","Mã số thuế tổ chức","Giấy tờ khác"]),
   field("identity_number","Số giấy tờ","party_identity_number",required=True),field("issuer","Cơ quan cấp","party_issuer"),field("issue_date","Ngày cấp","party_issue_date","date"),
   field("phone","Điện thoại","party_phone","phone"),field("fax","Fax","party_fax"),field("email","Email","party_email","email"),field("fee_exempt","Không phải nộp phí đăng ký","party_fee_exempt","checkbox")]
 asset_fields=[field("asset_type","Loại tài sản bảo đảm","asset_type","select",True,options=["Quyền sử dụng đất","Tài sản đã được chứng nhận quyền sở hữu","Dự án đầu tư có sử dụng đất","Nhà / tài sản hình thành trong tương lai","Tài sản gắn liền với đất chưa đăng ký quyền sở hữu"]),
   field("parcel_number","Số thửa đất","asset_parcel_number"),field("map_sheet","Tờ bản đồ","asset_map_sheet"),field("land_purpose","Mục đích sử dụng đất","asset_land_purpose"),field("land_term","Thời hạn sử dụng đất","asset_land_term"),field("address","Địa chỉ tài sản / thửa đất","asset_address","textarea"),
   field("certificate_name","Tên Giấy chứng nhận","asset_certificate_name"),field("certificate_serial","Số phát hành","asset_certificate_serial"),field("certificate_register","Số vào sổ cấp giấy","asset_certificate_register"),field("issuer","Cơ quan cấp","asset_issuer"),field("issue_date","Ngày cấp","asset_issue_date","date"),
   field("project_name","Tên dự án (nếu có)","asset_project_name"),field("project_basis","Căn cứ pháp lý xác lập dự án","asset_project_basis","textarea"),field("description","Mô tả chi tiết tài sản / tài sản hình thành trong tương lai","asset_description","textarea")]
 groups=[{"key":"debtors","title":"Bên bảo đảm","fields":party_fields},{"key":"creditors","title":"Bên nhận bảo đảm","fields":party_fields},{"key":"secured_assets","title":"Tài sản bảo đảm","fields":asset_fields}]
 blocks=[{"id":"applicant","instance_id":"applicant","block_key":"free_field","title":"Người yêu cầu đăng ký","order":1,"fields":[
   field("role_lender","Bên nhận bảo đảm","requester_role_lender","checkbox"),field("role_borrower","Bên bảo đảm","requester_role_borrower","checkbox"),field("role_trustee","Quản tài viên / doanh nghiệp quản lý, thanh lý tài sản","requester_role_trustee","checkbox"),field("role_branch","Chi nhánh pháp nhân / người đại diện","requester_role_branch","checkbox"),
   field("name","Họ tên người yêu cầu","requester_name",required=True,autofill="profile.full_name",help_text=HELP),field("address","Địa chỉ","requester_address","textarea",True,autofill="profile.permanent_address",help_text=HELP),field("identity","Số CCCD / định danh","requester_identity","cccd",True,autofill="profile.cccd_number",help_text=HELP),
   field("contact_name","Người liên hệ","contact_name",autofill="profile.full_name",help_text=HELP),field("contact_phone","Số điện thoại liên hệ","contact_phone","phone"),field("contact_fax","Fax liên hệ","contact_fax"),field("contact_email","Email liên hệ","contact_email","email") ]},
   {"id":"contract","instance_id":"contract","block_key":"free_field","title":"Hợp đồng bảo đảm","order":2,"fields":[field("contract_type","Loại hợp đồng bảo đảm","contract_type",required=True),field("contract_number","Số hợp đồng (nếu có)","contract_number"),field("effective_date","Ngày có hiệu lực","contract_effective_date","date",True)]},
   {"id":"debtors","instance_id":"debtors","block_key":"repeat_group","title":"Bên bảo đảm (thêm từng bên)","order":3,"fields":party_fields},
   {"id":"creditors","instance_id":"creditors","block_key":"repeat_group","title":"Bên nhận bảo đảm (thêm từng bên)","order":4,"fields":party_fields},
   {"id":"assets","instance_id":"secured_assets","block_key":"repeat_group","title":"Tài sản bảo đảm (thêm từng tài sản / dự án)","order":5,"fields":asset_fields},
   {"id":"closing","instance_id":"closing","block_key":"free_field","title":"Giấy tờ, cách nhận kết quả và ngày lập","order":6,"fields":[
     field("receiving_agency","Cơ quan đăng ký có thẩm quyền","receiving_agency","textarea",True),field("attachments","Giấy tờ kèm theo","attachments","textarea",True),field("result_method","Cách nhận kết quả","result_method","select",True,options=["Nhận trực tiếp","Qua bưu chính","Điện tử nếu được phép","Cách thức khác được cơ quan đồng ý"]),field("result_address","Tên / địa chỉ nhận bưu chính hoặc điện tử","result_address","textarea"),field("place","Địa điểm lập phiếu","signing_place",required=True),field("day","Ngày","signing_day","number",True),field("month","Tháng","signing_month","number",True),field("year","Năm","signing_year","number",True),field("debtor_signer","Người đại diện bên bảo đảm ký","debtor_signer",required=True),field("creditor_signer","Người đại diện bên nhận bảo đảm ký","creditor_signer",required=True),field("trustee_signer","Quản tài viên / người đại diện khác ký (nếu có)","trustee_signer")]}]
 return {"schema_version":1,"name":"Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất","code":"DD-010","procedure_code":"DD-010","domain":"dat_dai","procedure_id":None,
  "reference_catalog_names":["Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất","Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (01a)"],"sections":[{"id":"registration","title":"Kê khai đăng ký thế chấp","order":1,"blocks":blocks}],"repeat_groups":groups}

def setfont(run,size=8.5,bold=False,italic=False):run.font.name="Times New Roman";run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"Times New Roman");run.font.size=Pt(size);run.bold=bold;run.italic=italic
def para(doc,text="",*,align=None,size=8.5,bold=False,italic=False,after=2):
 p=doc.add_paragraph();p.paragraph_format.space_after=Pt(after);p.paragraph_format.line_spacing=1.0
 if align is not None:p.alignment=align
 if text:setfont(p.add_run(text),size,bold,italic)
 return p
def table_group(doc,title,headers,key,expressions):
 para(doc,title,bold=True,after=2);t=doc.add_table(rows=1,cols=len(headers));t.style="Table Grid"
 for c,h in zip(t.rows[0].cells,headers):c.text=h
 r=t.add_row();r.cells[0].text="{{#"+key+"}}{{"+key+".index}}"
 for cell,expr in zip(r.cells[1:],expressions):cell.text=expr
 r.cells[-1].text+="{{/"+key+"}}"
 for row in t.rows:
  for cell in row.cells:
   for p in cell.paragraphs:
    for run in p.runs:setfont(run,7,bold=(row is t.rows[0]))
 doc.add_paragraph()

def create_docx():
 doc=Document();s=doc.sections[0];s.page_width=Cm(21);s.page_height=Cm(29.7);s.top_margin=s.bottom_margin=Cm(1.2);s.left_margin=s.right_margin=Cm(1.5)
 n=doc.styles["Normal"];n.font.name="Times New Roman";n._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"Times New Roman");n.font.size=Pt(8.5);n.paragraph_format.space_after=Pt(2)
 para(doc,"(Ban hành kèm theo Nghị định số 99/2022/NĐ-CP)",align=WD_ALIGN_PARAGRAPH.CENTER,size=7.5,italic=True)
 para(doc,"CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",align=WD_ALIGN_PARAGRAPH.CENTER,size=10,bold=True,after=0);para(doc,"Độc lập - Tự do - Hạnh phúc",align=WD_ALIGN_PARAGRAPH.CENTER,bold=True,after=4)
 para(doc,"PHIẾU YÊU CẦU ĐĂNG KÝ BIỆN PHÁP BẢO ĐẢM",align=WD_ALIGN_PARAGRAPH.CENTER,size=11,bold=True,after=0);para(doc,"BẰNG QUYỀN SỬ DỤNG ĐẤT, TÀI SẢN GẮN LIỀN VỚI ĐẤT",align=WD_ALIGN_PARAGRAPH.CENTER,size=10,bold=True,after=5)
 para(doc,"{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True);para(doc,"Kính gửi: {{receiving_agency}}",after=3)
 para(doc,"PHẦN GHI CỦA CƠ QUAN ĐĂNG KÝ",bold=True);para(doc,"Vào sổ tiếp nhận: Quyển số ..................... · Số thứ tự ..................... · Người tiếp nhận .............................",after=4)
 para(doc,"PHẦN KÊ KHAI CỦA NGƯỜI YÊU CẦU ĐĂNG KÝ",bold=True);para(doc,"1. Người yêu cầu (đánh dấu tư cách phù hợp)",bold=True)
 para(doc,"Bên nhận bảo đảm: {{requester_role_lender}} · Bên bảo đảm: {{requester_role_borrower}} · Quản tài viên: {{requester_role_trustee}} · Chi nhánh / đại diện: {{requester_role_branch}}")
 para(doc,"Họ tên: {{requester_name}} · CCCD / định danh: {{requester_identity}} · Địa chỉ: {{requester_address}}")
 para(doc,"Liên hệ: {{contact_name}} · Điện thoại: {{contact_phone}} · Fax: {{contact_fax}} · Email: {{contact_email}}")
 para(doc,"2. Hợp đồng bảo đảm",bold=True);para(doc,"{{contract_type}} · Số: {{contract_number}} · Ngày có hiệu lực: {{contract_effective_date}}",after=4)
 schema=content();groupmap={g["key"]:g["fields"] for g in schema["repeat_groups"]}
 table_group(doc,"3. BÊN BẢO ĐẢM (thêm từng bên)",["STT","Họ tên / tổ chức","Địa chỉ","Loại giấy tờ · Số","Cơ quan cấp · Ngày cấp","Điện thoại / fax / email","Miễn phí"],"debtors",["{{debtors.name}}","{{debtors.address}}","{{debtors.identity_type}} · {{debtors.identity_number}}","{{debtors.issuer}} · {{debtors.issue_date}}","{{debtors.phone}} · {{debtors.fax}} · {{debtors.email}}","{{debtors.fee_exempt}}"])
 table_group(doc,"4. BÊN NHẬN BẢO ĐẢM (thêm từng bên)",["STT","Họ tên / tổ chức","Địa chỉ","Loại giấy tờ · Số","Cơ quan cấp · Ngày cấp","Điện thoại / fax / email","Miễn phí"],"creditors",["{{creditors.name}}","{{creditors.address}}","{{creditors.identity_type}} · {{creditors.identity_number}}","{{creditors.issuer}} · {{creditors.issue_date}}","{{creditors.phone}} · {{creditors.fax}} · {{creditors.email}}","{{creditors.fee_exempt}}"])
 table_group(doc,"5. MÔ TẢ TÀI SẢN BẢO ĐẢM (thêm từng tài sản / dự án)",["STT","Loại tài sản","Thửa · tờ · địa chỉ · mục đích · thời hạn","Giấy chứng nhận · số phát hành · số vào sổ","Cơ quan cấp · ngày cấp","Dự án · căn cứ · mô tả"],"secured_assets",["{{secured_assets.asset_type}}","Thửa {{secured_assets.parcel_number}} · Tờ {{secured_assets.map_sheet}} · {{secured_assets.address}} · {{secured_assets.land_purpose}} · {{secured_assets.land_term}}","{{secured_assets.certificate_name}} · {{secured_assets.certificate_serial}} · {{secured_assets.certificate_register}}","{{secured_assets.issuer}} · {{secured_assets.issue_date}}","{{secured_assets.project_name}} · {{secured_assets.project_basis}} · {{secured_assets.description}}"])
 para(doc,"6. Cơ quan đăng ký: {{receiving_agency}}",after=3);para(doc,"7. Giấy tờ kèm theo",bold=True);para(doc,"{{attachments}}",after=3);para(doc,"8. Cách nhận kết quả: {{result_method}} · Thông tin nhận: {{result_address}}",after=4)
 para(doc,"Người yêu cầu chịu trách nhiệm trước pháp luật về tính trung thực, chính xác của nội dung kê khai.",after=3)
 doc.add_page_break();para(doc,"CHỮ KÝ CÁC BÊN",bold=True,after=5)
 para(doc,"BÊN BẢO ĐẢM (hoặc đại diện)",align=WD_ALIGN_PARAGRAPH.LEFT,bold=True,after=0);para(doc,"Ký, ghi rõ họ tên, chức danh và đóng dấu nếu có: {{debtor_signer}}",after=4)
 para(doc,"BÊN NHẬN BẢO ĐẢM (hoặc đại diện)",align=WD_ALIGN_PARAGRAPH.LEFT,bold=True,after=0);para(doc,"Ký, ghi rõ họ tên, chức danh và đóng dấu nếu có: {{creditor_signer}}",after=3)
 para(doc,"Quản tài viên / đại diện khác (nếu có): {{trustee_signer}}",after=0)
 para(doc,"PHẦN CHỨNG NHẬN CỦA CƠ QUAN ĐĂNG KÝ",bold=True,after=4)
 para(doc,"Cơ quan đăng ký có thẩm quyền: ........................................................................................................")
 para(doc,"Chứng nhận biện pháp bảo đảm bằng ................................................................................ đã đăng ký theo nội dung kê khai tại Phiếu này vào lúc ........ giờ ........ phút, ngày ........ tháng ........ năm ........")
 para(doc,"NGƯỜI CÓ THẨM QUYỀN CỦA CƠ QUAN ĐĂNG KÝ",align=WD_ALIGN_PARAGRAPH.RIGHT,bold=True);para(doc,"(Ký, ghi rõ họ tên, chức danh và đóng dấu)",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True)
 para(doc,"HƯỚNG DẪN CHUNG",bold=True,after=3)
 for i,text in enumerate(["Kê khai trung thực, đầy đủ và chính xác.","Đánh dấu X vào ô lựa chọn tương ứng; gạch chéo mục không có thông tin.","Nếu nội dung có nhiều trang, đánh số trang và ký nháy từng trang.","Kê khai riêng từng người bảo đảm, người nhận bảo đảm và từng tài sản; thông tin tài sản theo Giấy chứng nhận và hồ sơ tại cơ quan đăng ký.","Mô tả tài sản hình thành trong tương lai cần ghi đủ loại, địa chỉ, căn hộ/hạng mục hoặc loại cây/rừng nếu áp dụng."],1):para(doc,f"({i}) {text}",size=8,after=3)
 doc.save(OUT/f"{SLUG}.docx")

def main():
 OUT.mkdir(parents=True,exist_ok=True);(OUT/f"{SLUG}.json").write_text(json.dumps(content(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8");create_docx();print(OUT/f"{SLUG}.docx")
if __name__=="__main__":main()
