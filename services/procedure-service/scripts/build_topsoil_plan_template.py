"""Build a fillable topsoil-use plan with an expandable use-destination table."""
from __future__ import annotations
import json
from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"app"/"builtin_forms";SLUG="phuong-an-su-dung-tang-dat-mat"
HELP="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa thông tin gốc."
def field(key,label,word_key,kind="text",required=False,*,autofill=None,help_text="",options=None):
 d={"key":key,"label":label,"word_key":word_key,"type":kind,"required":required}
 if autofill:d["autofill"]=autofill
 if help_text:d["help_text"]=help_text
 if options:d["options"]=options
 return d

def content():
 uses=[field("location_type","Vị trí sử dụng","use_location_type","select",True,options=["Trong khuôn viên dự án","Ngoài khuôn viên dự án"]),field("location","Địa điểm / vị trí cụ thể","use_location","textarea",True),field("area","Diện tích sử dụng (m²)","use_area","number",True),field("volume","Khối lượng đất mặt sử dụng (m³)","use_volume","number",True),field("purpose","Mục đích sử dụng","use_purpose","textarea",True)]
 sections=[
  {"id":"holder","title":"Thông tin người được giao / thuê đất","order":1,"blocks":[{"id":"holder_info","instance_id":"holder_info","block_key":"free_field","title":"Người đề nghị","order":1,"fields":[
   field("holder_type","Người đề nghị là","holder_type","select",True,options=["Cá nhân","Tổ chức"]),field("organization","Tên tổ chức (nếu là tổ chức)","organization_name"),field("name","Họ tên người đại diện","holder_name",required=True,autofill="profile.full_name",help_text=HELP),field("address","Địa chỉ / trụ sở","holder_address","textarea",True,autofill="profile.permanent_address",help_text=HELP),field("phone","Điện thoại","holder_phone","phone",False,autofill="profile.phone"),field("identity","Số CCCD / định danh","holder_identity","cccd",False,autofill="profile.cccd_number",help_text=HELP),field("issue_date","Ngày cấp CCCD","holder_issue_date","issue_date",False,autofill="profile.cccd_issue_date",help_text=HELP),field("issue_place","Nơi cấp","holder_issue_place",autofill="profile.cccd_issue_place",help_text=HELP),field("business_registration","Giấy đăng ký kinh doanh (nếu có)","business_registration"),field("business_issue_date","Ngày cấp đăng ký kinh doanh","business_issue_date","date"),field("business_issue_place","Nơi cấp đăng ký kinh doanh","business_issue_place")]}]},
  {"id":"project","title":"Thông tin chuyển mục đích đất chuyên trồng lúa","order":2,"blocks":[{"id":"project_info","instance_id":"project_info","block_key":"free_field","title":"Khu đất và khối lượng đất mặt","order":1,"fields":[
   field("project_name","Công trình / dự án","project_name",required=True),field("rice_area","Diện tích đất chuyên trồng lúa phải bóc tách (m²)","rice_area","number",True),field("soil_depth","Độ sâu tầng đất mặt bóc tách (m)","soil_depth","number",True),field("soil_volume","Khối lượng đất mặt phải bóc tách (m³)","soil_volume","number",True,help_text="Có thể đối chiếu theo công thức: diện tích phải bóc tách × độ sâu tầng đất mặt."),field("use_purpose","Mục đích sử dụng tầng đất mặt","soil_use_purpose","textarea",True),field("commitment","Cam kết khác (nếu có)","other_commitment","textarea")]}]},
  {"id":"destinations","title":"Địa điểm sử dụng đất mặt","order":3,"blocks":[{"id":"uses","instance_id":"uses","block_key":"repeat_group","title":"Thêm từng vị trí sử dụng trong / ngoài dự án","order":1,"fields":uses}]},
  {"id":"closing","title":"Ngày lập và người ký","order":4,"blocks":[{"id":"closing_info","instance_id":"closing_info","block_key":"free_field","title":"Thông tin cuối phương án","order":1,"fields":[field("serial","Số phương án (nếu có)","plan_number"),field("place","Địa điểm lập","signing_place",required=True),field("day","Ngày","signing_day","number",True),field("month","Tháng","signing_month","number",True),field("year","Năm","signing_year","number",True),field("signer","Họ tên người ký","signer_name",required=True,autofill="profile.full_name",help_text=HELP),field("position","Chức vụ người ký (nếu có)","signer_position")]}]}
 ]
 return {"schema_version":1,"name":"Phương án sử dụng tầng đất mặt","code":"DD-008","procedure_code":"DD-008","domain":"dat_dai","procedure_id":None,"reference_catalog_names":["Phương án bóc tách, sử dụng tầng đất mặt khi chuyển mục đích sử dụng"],"sections":sections,"repeat_groups":[{"key":"uses","title":"Vị trí sử dụng đất mặt","fields":uses}]}

def setfont(r,size=9,bold=False,italic=False):r.font.name="Times New Roman";r._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"Times New Roman");r.font.size=Pt(size);r.bold=bold;r.italic=italic
def para(d,text="",*,align=None,size=9,bold=False,italic=False,after=3):
 p=d.add_paragraph();p.paragraph_format.space_after=Pt(after);p.paragraph_format.line_spacing=1.0
 if align is not None:p.alignment=align
 if text:setfont(p.add_run(text),size,bold,italic)
 return p
def create_docx():
 d=Document();s=d.sections[0];s.page_width=Cm(21);s.page_height=Cm(29.7);s.top_margin=s.bottom_margin=Cm(1.5);s.left_margin=s.right_margin=Cm(1.8)
 n=d.styles["Normal"];n.font.name="Times New Roman";n._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"Times New Roman");n.font.size=Pt(9);n.paragraph_format.space_after=Pt(3)
 para(d,"(Kèm theo Nghị định số 112/2024/NĐ-CP ngày 11 tháng 9 năm 2024)",align=WD_ALIGN_PARAGRAPH.CENTER,size=8,italic=True,after=3)
 para(d,"NGƯỜI ĐƯỢC NHÀ NƯỚC GIAO ĐẤT, CHO THUÊ ĐẤT",align=WD_ALIGN_PARAGRAPH.LEFT,bold=True,size=9,after=5)
 para(d,"CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",align=WD_ALIGN_PARAGRAPH.CENTER,size=11,bold=True,after=0);para(d,"Độc lập - Tự do - Hạnh phúc",align=WD_ALIGN_PARAGRAPH.CENTER,bold=True,after=4)
 para(d,"Số: {{plan_number}}",align=WD_ALIGN_PARAGRAPH.LEFT,after=0);para(d,"{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True,after=4)
 para(d,"PHƯƠNG ÁN SỬ DỤNG TẦNG ĐẤT MẶT",align=WD_ALIGN_PARAGRAPH.CENTER,bold=True,size=12,after=6)
 para(d,"1. Người được Nhà nước giao đất, cho thuê đất",bold=True);para(d,"Loại chủ thể: {{holder_type}} · Tên tổ chức: {{organization_name}}")
 para(d,"Họ tên người đại diện: {{holder_name}} · Địa chỉ / trụ sở: {{holder_address}}")
 para(d,"Điện thoại: {{holder_phone}} · CCCD / định danh: {{holder_identity}} · Ngày cấp: {{holder_issue_date}} · Nơi cấp: {{holder_issue_place}}")
 para(d,"Đăng ký kinh doanh (nếu có): {{business_registration}} · Ngày cấp: {{business_issue_date}} · Nơi cấp: {{business_issue_place}}",after=5)
 para(d,"2. Mục đích chuyển đổi từ đất chuyên trồng lúa",bold=True);para(d,"Chuyển đổi mục đích sử dụng đất chuyên trồng lúa để thực hiện công trình / dự án: {{project_name}}",after=4)
 para(d,"3. Diện tích đất chuyên trồng lúa đề nghị chuyển đổi: {{rice_area}} m²",after=4)
 para(d,"4. Khối lượng đất mặt phải bóc tách: {{soil_volume}} m³",after=1);para(d,"(Diện tích phải bóc tách {{rice_area}} m² × độ sâu tầng đất mặt {{soil_depth}} m)",italic=True,size=8.5,after=5)
 para(d,"5. Phương án sử dụng đất mặt (thêm từng điểm đến trong bảng)",bold=True)
 table=d.add_table(rows=1,cols=6);table.style="Table Grid"
 for c,h in zip(table.rows[0].cells,["STT","Trong / ngoài dự án","Vị trí / địa điểm","Diện tích (m²)","Khối lượng (m³)","Mục đích sử dụng"]):c.text=h
 row=table.add_row();row.cells[0].text="{{#uses}}{{uses.index}}";row.cells[1].text="{{uses.location_type}}";row.cells[2].text="{{uses.location}}";row.cells[3].text="{{uses.area}}";row.cells[4].text="{{uses.volume}}";row.cells[5].text="{{uses.purpose}}{{/uses}}"
 for tr in table.rows:
  for cell in tr.cells:
   for p in cell.paragraphs:
    for r in p.runs:setfont(r,7.5,bold=(tr is table.rows[0]))
 para(d,"Mục đích sử dụng tầng đất mặt: {{soil_use_purpose}}",after=2)
 para(d,"Đất mặt chỉ được sử dụng vào mục đích nông nghiệp theo quy định; có thể gồm tôn cao nền ruộng, tăng độ dày tầng canh tác, trồng lúa/cây hàng năm/cây lâu năm, cây xanh, hoa, cây cảnh hoặc cây dược liệu.",italic=True,size=8.5,after=5)
 para(d,"Cam kết thực hiện đúng phương án và chịu trách nhiệm trước pháp luật về thông tin kê khai.",after=3);para(d,"Cam kết khác: {{other_commitment}}",after=6)
 para(d,"NGƯỜI ĐƯỢC NHÀ NƯỚC GIAO ĐẤT, CHO THUÊ ĐẤT",align=WD_ALIGN_PARAGRAPH.RIGHT,bold=True,after=0);para(d,"(Ký, ghi rõ họ tên, chức vụ và đóng dấu nếu có)",align=WD_ALIGN_PARAGRAPH.RIGHT,italic=True,size=8.5,after=9);para(d,"{{signer_name}} · {{signer_position}}",align=WD_ALIGN_PARAGRAPH.RIGHT,bold=True)
 d.save(OUT/f"{SLUG}.docx")
def main():
 OUT.mkdir(parents=True,exist_ok=True);(OUT/f"{SLUG}.json").write_text(json.dumps(content(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8");create_docx();print(OUT/f"{SLUG}.docx")
if __name__=="__main__":main()
