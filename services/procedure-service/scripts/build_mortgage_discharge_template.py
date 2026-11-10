"""Build a fillable mortgage-registration cancellation request."""
from __future__ import annotations
import json
from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "builtin_forms"
SLUG = "phieu-yeu-cau-xoa-dang-ky-the-chap"
HELP = "Tự lấy từ hồ sơ CCCD; không thể sửa thông tin gốc trong đơn."

def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text="", options=None):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill: item["autofill"] = autofill
    if help_text: item["help_text"] = help_text
    if options: item["options"] = options
    return item

def content():
    others = [field("name", "Họ tên người yêu cầu khác", "other_requester_name", required=True),
              field("role", "Tư cách / chức danh", "other_requester_role", required=True),
              field("identity", "Số định danh / giấy tờ", "other_requester_identity"),
              field("address", "Địa chỉ", "other_requester_address", "textarea")]
    sections = [
      {"id":"requester","title":"Người yêu cầu xóa đăng ký","order":1,"blocks":[
        {"id":"requester_info","instance_id":"requester_info","block_key":"free_field","title":"Người làm hồ sơ","order":1,"fields":[
          field("role_secured_party","Bên nhận bảo đảm","requester_secured_party","checkbox"),
          field("role_debtor","Bên bảo đảm","requester_debtor","checkbox"),
          field("role_transferee","Người nhận chuyển giao tài sản bảo đảm","requester_transferee","checkbox"),
          field("role_trustee","Quản tài viên / doanh nghiệp quản lý tài sản","requester_trustee","checkbox"),
          field("role_enforcement","Cơ quan thi hành án / Chấp hành viên","requester_enforcement","checkbox"),
          field("role_other","Cơ quan / người có thẩm quyền khác","requester_other_role","checkbox"),
          field("role_branch","Chi nhánh của pháp nhân / người đại diện","requester_branch","checkbox"),
          field("name","Họ và tên / tên tổ chức","requester_name",required=True,autofill="profile.full_name",help_text=HELP),
          field("address","Địa chỉ","requester_address","textarea",True,autofill="profile.permanent_address",help_text=HELP),
          field("contact_name","Người liên hệ","contact_name",autofill="profile.full_name",help_text=HELP),
          field("phone","Số điện thoại","phone","phone",False,autofill="profile.phone"),
          field("email","Email","email","email",False,autofill="profile.email"),
        ]},
        {"id":"other_requesters","instance_id":"other_requesters","block_key":"repeat_group","title":"Người cùng yêu cầu khác (nếu có)","order":2,"fields":others}
      ]},
      {"id":"cancellation","title":"Căn cứ xóa đăng ký và kết quả","order":2,"blocks":[
        {"id":"cancellation_info","instance_id":"cancellation_info","block_key":"free_field","title":"Thông tin xóa đăng ký","order":1,"fields":[
          field("receiving_agency","Cơ quan đăng ký có thẩm quyền","receiving_agency","textarea",True),
          field("grounds","Căn cứ xóa đăng ký (nêu rõ nghĩa vụ đã chấm dứt / căn cứ pháp lý)","cancellation_grounds","textarea",True),
          field("fee_exempt","Thuộc đối tượng không phải nộp phí đăng ký","fee_exempt","checkbox"),
          field("attachments","Giấy tờ kèm theo","attachments","textarea",True),
          field("result_method","Cách thức nhận kết quả","result_method","select",True,options=["Nhận trực tiếp tại cơ quan đăng ký","Qua dịch vụ bưu chính","Cách thức điện tử (nếu pháp luật quy định)","Cách thức khác nếu được đồng ý"]),
          field("result_address","Tên và địa chỉ người nhận qua bưu chính (nếu chọn)","result_address","textarea"),
          field("result_electronic","Địa chỉ / cách thức nhận điện tử (nếu chọn)","result_electronic","textarea"),
          field("recipient","Người đại diện ký thay (nếu có)","requester_signer"),
          field("place","Địa điểm lập phiếu","signing_place",required=True),
          field("day","Ngày","signing_day","number",True), field("month","Tháng","signing_month","number",True), field("year","Năm","signing_year","number",True),
        ]}
      ]}
    ]
    return {"schema_version":1,"name":"Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất","code":"DD-011","procedure_code":"DD-011","domain":"dat_dai","procedure_id":None,
      "reference_catalog_names":["Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất (Giải chấp Sổ đỏ)","Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất, tài sản gắn liền với đất (03a)"],"sections":sections,
      "repeat_groups":[{"key":"other_requesters","title":"Người cùng yêu cầu xóa đăng ký","fields":others}]}

def setfont(run,size=9.5,bold=False,italic=False):
    run.font.name="Times New Roman"; run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"Times New Roman"); run.font.size=Pt(size); run.bold=bold; run.italic=italic
def para(doc,text="",*,align=None,size=9.5,bold=False,italic=False,after=3):
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(after); p.paragraph_format.line_spacing=1.0
    if align is not None:p.alignment=align
    if text:setfont(p.add_run(text),size,bold,italic)
    return p

def create_docx():
    doc=Document(); sec=doc.sections[0]; sec.page_width=Cm(21); sec.page_height=Cm(29.7); sec.top_margin=sec.bottom_margin=Cm(1.5); sec.left_margin=sec.right_margin=Cm(1.8)
    normal=doc.styles["Normal"]; normal.font.name="Times New Roman"; normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"Times New Roman"); normal.font.size=Pt(9.5); normal.paragraph_format.space_after=Pt(3)
    para(doc,"(Ban hành kèm theo Nghị định số 99/2022/NĐ-CP)",align=WD_ALIGN_PARAGRAPH.CENTER,size=8,italic=True,after=2)
    para(doc,"CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",align=WD_ALIGN_PARAGRAPH.CENTER,size=11,bold=True,after=0)
    para(doc,"Độc lập - Tự do - Hạnh phúc",align=WD_ALIGN_PARAGRAPH.CENTER,bold=True,after=4)
    para(doc,"PHIẾU YÊU CẦU XÓA ĐĂNG KÝ BIỆN PHÁP BẢO ĐẢM",align=WD_ALIGN_PARAGRAPH.CENTER,size=12,bold=True,after=0)
    para(doc,"BẰNG QUYỀN SỬ DỤNG ĐẤT, TÀI SẢN GẮN LIỀN VỚI ĐẤT",align=WD_ALIGN_PARAGRAPH.CENTER,size=11,bold=True,after=5)
    para(doc,"{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True)
    para(doc,"Kính gửi: {{receiving_agency}}",after=5)
    para(doc,"PHẦN GHI CỦA CƠ QUAN ĐĂNG KÝ",bold=True)
    para(doc,"Sổ tiếp nhận hồ sơ: Quyển số: .....................   Số thứ tự: .....................   Người tiếp nhận: ........................................",after=5)
    para(doc,"PHẦN KÊ KHAI CỦA NGƯỜI YÊU CẦU XÓA ĐĂNG KÝ",bold=True)
    para(doc,"1. Người yêu cầu xóa đăng ký (đánh dấu tư cách phù hợp)",bold=True)
    para(doc,"Bên nhận bảo đảm: {{requester_secured_party}} · Bên bảo đảm: {{requester_debtor}} · Người nhận chuyển giao: {{requester_transferee}}")
    para(doc,"Quản tài viên: {{requester_trustee}} · Cơ quan thi hành án: {{requester_enforcement}} · Khác: {{requester_other_role}} · Chi nhánh / đại diện: {{requester_branch}}")
    para(doc,"Họ tên / tổ chức: {{requester_name}} · Địa chỉ: {{requester_address}}")
    para(doc,"Người liên hệ: {{contact_name}} · Điện thoại: {{phone}} · Email: {{email}}")
    para(doc,"Người cùng yêu cầu khác (nếu có), kê khai trong danh sách đính kèm:",italic=True)
    para(doc,"2. Căn cứ xóa đăng ký",bold=True); para(doc,"{{cancellation_grounds}}")
    para(doc,"3. Đối tượng không phải nộp phí đăng ký: {{fee_exempt}}")
    para(doc,"4. Giấy tờ kèm theo",bold=True); para(doc,"{{attachments}}")
    para(doc,"5. Cách thức nhận kết quả: {{result_method}}")
    para(doc,"Người nhận qua bưu chính: {{result_address}} · Nhận điện tử / cách thức khác: {{result_electronic}}")
    para(doc,"Người yêu cầu đăng ký chịu trách nhiệm trước pháp luật về tính trung thực, chính xác của thông tin kê khai.",after=5)
    para(doc,"NGƯỜI YÊU CẦU XÓA ĐĂNG KÝ",align=WD_ALIGN_PARAGRAPH.RIGHT,bold=True,after=0)
    para(doc,"(Ký, ghi rõ họ tên, chức danh và đóng dấu nếu có)",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True,size=8.5,after=2)
    para(doc,"{{requester_signer}}",align=WD_ALIGN_PARAGRAPH.RIGHT,bold=True)
    group_fields=content()["repeat_groups"][0]["fields"]
    para(doc,"DANH SÁCH NGƯỜI CÙNG YÊU CẦU XÓA ĐĂNG KÝ",align=WD_ALIGN_PARAGRAPH.CENTER,bold=True,size=10,after=3)
    table=doc.add_table(rows=1,cols=5); table.style="Table Grid"
    for cell,value in zip(table.rows[0].cells,["STT","Họ tên","Tư cách / chức danh","Số định danh","Địa chỉ"]):cell.text=value
    row=table.add_row(); row.cells[0].text="{{#other_requesters}}{{other_requesters.index}}"
    for cell,key in zip(row.cells[1:], ["name","role","identity","address"]):cell.text=f"{{{{other_requesters.{key}}}}}"
    row.cells[-1].text+="{{/other_requesters}}"
    for tr in table.rows:
      for cell in tr.cells:
       for p in cell.paragraphs:
        for run in p.runs:setfont(run,7.5,bold=(tr is table.rows[0]))
    doc.add_page_break(); para(doc,"PHẦN CHỨNG NHẬN CỦA CƠ QUAN ĐĂNG KÝ",bold=True)
    para(doc,"Cơ quan đăng ký có thẩm quyền: ........................................................................................................")
    para(doc,"Chứng nhận đã xóa đăng ký biện pháp bảo đảm theo nội dung được kê khai tại Phiếu yêu cầu này vào thời điểm ........ giờ ........ phút, ngày ........ tháng ........ năm ........")
    para(doc,"NGƯỜI CÓ THẨM QUYỀN CỦA CƠ QUAN ĐĂNG KÝ",align=WD_ALIGN_PARAGRAPH.RIGHT,bold=True,after=0)
    para(doc,"(Ký, ghi rõ họ tên, chức danh và đóng dấu)",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True)
    para(doc,"HƯỚNG DẪN CHUNG",bold=True,after=4)
    for i,note in enumerate(["Kê khai trung thực, đầy đủ và chính xác.","Đánh dấu X vào ô vuông tương ứng đối với mục có lựa chọn.","Gạch chéo mục không có thông tin kê khai.","Nếu nội dung kê khai có nhiều trang, đánh số trang và ký nháy từng trang."],1):para(doc,f"({i}) {note}",size=8.5,after=3)
    doc.save(OUT/f"{SLUG}.docx")

def main():
    OUT.mkdir(parents=True,exist_ok=True); (OUT/f"{SLUG}.json").write_text(json.dumps(content(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); create_docx(); print(OUT/f"{SLUG}.docx")
if __name__=="__main__":main()
