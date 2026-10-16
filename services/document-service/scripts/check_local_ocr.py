"""Synthetic Vietnamese OCR smoke test. No DB, Storage, secrets or real CCCD.

Run inside the built image with --network none. Prints PASS/timing only, not text.
"""
import io
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from app.services.identity_images import prepare_identity_pair
from app.services.identity_parser import merge_fields
from app.services.local_ocr import LocalOcr, OcrSettings


def picture(lines):
    image=Image.new("RGB",(1600,850),"white")
    draw=ImageDraw.Draw(image)
    font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",40)
    for index,text in enumerate(lines):
        draw.text((60,40+index*95),text,fill="black",font=font)
    data=io.BytesIO(); image.save(data,format="PNG"); return data.getvalue()


def synthetic_pair():
    front=picture(["MẪU KIỂM THỬ OCR - KHÔNG PHẢI GIẤY TỜ", "Số / No.: 000000000001",
                   "Họ và tên / Full name:","NGUYỄN VĂN AN", "Ngày sinh: 01/01/2000", "Nơi thường trú: Hà Nội"])
    back=picture(["MẶT SAU - MẪU KIỂM THỬ", "Ngày cấp: 01/01/2020", "Nơi cấp: TEST AUTHORITY"])
    return prepare_identity_pair(("front.png","image/png",front),("back.png","image/png",back))


def main():
    pair=synthetic_pair()
    engine=LocalOcr(OcrSettings.from_env()); received=[]; start=time.monotonic(); deadline=start+55
    f=engine.read_image(pair[0],deadline=deadline,on_response=received.append)
    b=engine.read_image(pair[1],deadline=deadline,on_response=received.append)
    fields,_=merge_fields(f,b)
    expected={"identity_number":"000000000001","full_name":"NGUYỄN VĂN AN","date_of_birth":"2000-01-01",
              "issue_date":"2020-01-01","issue_place":"TEST AUTHORITY","permanent_address":"Hà Nội"}
    mismatch=[key for key,value in expected.items() if fields[key]!=value]
    if mismatch:
        print("FAIL: synthetic field mismatch: " + ",".join(mismatch)); return 1
    blank=picture([])
    blank_pair=prepare_identity_pair(("front.png","image/png",blank),("back.jpg","image/jpeg",pair[1].content))
    values=engine.read_image(blank_pair[0],deadline=time.monotonic()+55,on_response=received.append)
    if any(values.values()):
        print("FAIL: blank image must not produce identity fields"); return 1
    print(f"PASS: real CPU OCR, six Vietnamese synthetic fields, blank image; {time.monotonic()-start:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
