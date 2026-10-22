"""Offline subprocess boundary, crop selection and safe accent restoration."""
import json
import subprocess
import time
import pytest
from PIL import Image
from test_document_foundation import foundation


def image_raw():
    return Image.new("RGB",(600,300),"white"), {"side":"front","ocr_size":{"width":600,"height":300},"lines":[
        {"text":"Họ và tên / Full name:","confidence":.95,"polygon":[[100,50],[350,50],[350,70],[100,70]]},
        {"text":"NGUYEN VAN AN","confidence":.95,"polygon":[[100,75],[350,75],[350,95],[100,95]]},
        {"text":"Ngày sinh: 01/01/2000","confidence":.95,"polygon":[[100,115],[350,115],[350,135],[100,135]]},
    ]}


@pytest.fixture
def model_root(tmp_path):
    for name in ("config.yml","manifest.json","vgg_seq2seq.pth"):
        (tmp_path/name).touch()
    return str(tmp_path)


def test_crops_only_value_and_not_mrz_or_dates(foundation):
    from app.services.vietocr_local import crop_regions
    pixels,raw=image_raw()
    regions=crop_regions(pixels,raw)
    assert [r["field"] for r in regions] == ["full_name"]


def test_crop_pass_prefers_name_heading_over_background_fragments(foundation):
    from app.services.vietocr_local import crop_regions
    pixels, good = image_raw()
    raw = {"side":"front", "ocr_size":good['ocr_size'],
           "lines":[{"text":"noise", "confidence":.1} for _ in range(40)],
           "attempts":[{**good, "psm":11}]}
    assert [r['field'] for r in crop_regions(pixels,raw)] == ['full_name']


def test_clear_number_not_discarded_with_a_weak_label(foundation):
    from app.services.identity_parser import parse_fields
    raw={'lines':[{'text':'Số / No.: 099000000001','confidence':.2,'words':[
        {'text':'Số','confidence':.2},{'text':'099000000001','confidence':.93}]}]}
    assert parse_fields(raw)['identity_number']=='099000000001'
    raw['lines'][0]['words'][1]['confidence']=.6
    assert parse_fields(raw)['identity_number'] is None


def test_weak_bilingual_heading_locates_value_without_accepting_weak_text(foundation):
    from app.services.vietocr_local import supplemental_regions
    from app.services.identity_parser import parse_additional_fields
    raw = {'side':'front','ocr_size':{'width':600,'height':300},'lines':[
        {'text':'Giới tính / Sex: Nam Quốc tịch / Nationality: Việt Nam','confidence':.2,
         'words':[{'text':'Nam','bbox':[230,100,35,20]},
                  {'text':'Việt','bbox':[470,100,40,20]}, {'text':'Nam','bbox':[520,100,40,20]}]}]}
    regions = supplemental_regions(raw,600,300,set())
    assert regions['sex'] == [(226,96,269,124)]
    assert regions['nationality'] == [(466,96,564,124)]
    attempt = {'source':'vietocr_field','field':'sex','lines':[{'text':'Nam','confidence':.74}]}
    assert parse_additional_fields(attempt)['sex'] is None
    attempt['lines'][0]['confidence'] = .95
    assert parse_additional_fields(attempt)['sex'] == 'male'


@pytest.mark.parametrize('field,value,expected',[
    ('issue_date','13/05/2021','2021-05-13'),
    ('expiry_date','27/08/2031','2031-08-27'),
    ('expiry_date','31/02/2031',None),
    ('sex','Quốc tịch Việt Nam',None)])
def test_supplemental_values_are_typed_and_validated(foundation,field,value,expected):
    from app.services.identity_parser import parse_fields,parse_additional_fields
    raw={'source':'vietocr_field','field':field,'lines':[{'text':value,'confidence':.95}]}
    assert {**parse_fields(raw),**parse_additional_fields(raw)}[field] == expected


def test_legacy_back_date_crop_requires_printed_date_heading(foundation):
    from app.services.vietocr_local import supplemental_regions
    raw={'side':'back','ocr_size':{'width':600,'height':300},'lines':[
        {'text':'Date, month, year: 13/05/2021','confidence':.2,
         'words':[{'text':'13/05/2021','bbox':[210,30,100,20]}]}]}
    assert 'issue_date' in supplemental_regions(raw,600,300,set())
    raw['lines'][0]['text']='Unrelated 13/05/2021'
    assert 'issue_date' not in supplemental_regions(raw,600,300,set())


def test_authority_crop_does_not_include_officer_signature(foundation):
    from app.services.vietocr_local import supplemental_regions
    raw={'side':'back','ocr_size':{'width':600,'height':300},'lines':[
        {'text':'KT CỤC TRƯỞNG CỤC CẢNH SÁT','confidence':.2,
         'polygon':[[40,30],[300,30],[300,50],[40,50]],'words':[
             {'text':'KT','bbox':[40,30,20,20]}, {'text':'CỤC','bbox':[130,30,30,20]},
             {'text':'CẢNH','bbox':[170,30,50,20]}, {'text':'SÁT','bbox':[230,30,35,20]}]},
        {'text':'QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI','confidence':.2,
         'polygon':[[40,55],[400,55],[400,75],[40,75]]},
        {'text':'NGƯỜI KÝ KIỂM THỬ','confidence':.95,
         'polygon':[[40,80],[300,80],[300,100],[40,100]]}]}
    regions=supplemental_regions(raw,600,300,set())
    assert len(regions['issue_place'])==2
    assert regions['issue_place'][-1][3]==79


def test_back_noise_cannot_override_front_owner_identity(foundation):
    from app.services.identity_parser import parse_fields
    raw={'side':'back','lines':[{'text':'Họ và tên: OTHER PERSON','confidence':.99}]}
    assert parse_fields(raw)['full_name'] is None


def test_damaged_bilingual_prefix_is_not_a_person_name(foundation):
    from app.services.identity_parser import parse_fields
    raw={'side':'front','lines':[
        {'text':'Ho và tén ! Full name:','confidence':.9},
        {'text':'TEST PERSON','confidence':.96}]}
    assert parse_fields(raw)['full_name']=='TEST PERSON'


def test_portrait_card_roi_does_not_use_identity_values(foundation):
    from app.services.identity_card_crop import crop_card_background
    image=Image.new('RGB',(960,1280),(180,180,180))
    image.paste(Image.new('RGB',(740,465),(100,200,170)),(110,470))
    cropped,meta=crop_card_background(image)
    assert meta and 1.3 <= cropped.width/cropped.height <= 1.9
    blank,meta=crop_card_background(Image.new('RGB',(960,1280),'white'))
    assert meta is None and blank.size==(960,1280)
    original=Image.new('RGB',(960,624),'white')
    assert crop_card_background(original)[0] is original


def test_misread_english_heading_is_not_a_name_crop(foundation):
    from app.services.vietocr_local import crop_regions
    pixels,raw=image_raw()
    raw["lines"][0].update(text="Họ và tên / Ful name:",words=[
        {"text":"Ful","confidence":.95,"bbox":[220,50,30,20]},
        {"text":"name:","confidence":.95,"bbox":[255,50,50,20]}])
    assert len(crop_regions(pixels,raw))==1


def test_vietocr_restores_accents_when_letters_agree(foundation):
    from app.services.identity_parser import parse_fields
    raw={"lines":[{"text":"Họ và tên: NGUYEN VAN AN","confidence":.95}],"attempts":[
        {"source":"vietocr_field","field":"full_name","lines":[{"text":"NGUYỄN VĂN AN","confidence":.96}]}]}
    assert parse_fields(raw)["full_name"] == "NGUYỄN VĂN AN"
    raw["attempts"][0]["lines"][0]["text"] = "TRẦN VĂN AN"
    assert parse_fields(raw)["full_name"] == "NGUYEN VAN AN"


def test_missing_model_does_not_download(foundation,tmp_path):
    from app.services.vietocr_local import read_regions
    pixels,raw=image_raw()
    with pytest.raises(foundation[3]) as error:
        read_regions(pixels,raw,root=str(tmp_path),threads=2,deadline=time.monotonic()+10)
    assert error.value.code == "OCR_MODEL_UNAVAILABLE"


def test_address_tone_placement_preserves_places(foundation):
    from app.services.identity_parser import normalize_address_tones, parse_additional_fields
    assert normalize_address_tones("Hoà Bình, HOÀ BÌNH") == "Hòa Bình, HÒA BÌNH"
    assert normalize_address_tones("Hoàng Mai, Đà Nẵng") == "Hoàng Mai, Đà Nẵng"
    assert normalize_address_tones("Thị trần Vương, THỊ TRẦN Y") == "Thị trấn Vương, THỊ TRẤN Y"
    raw = {"lines": [], "attempts": [{"source": "vietocr_field", "field": "residence_address",
           "lines": [{"text": "Hoà Bình, Quận A, Tỉnh B", "confidence": .9}]}]}
    assert parse_additional_fields(raw)["residence_address"] == "Hòa Bình, Quận A, Tỉnh B"


def test_regional_accents_not_overwritten_by_lower_confidence_vietocr(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw = {"lines": [], "attempts": [
        {"source": "new_card_region", "field": "registered_birth_place",
         "lines": [{"text": "Đà Nẵng", "confidence": .94}]},
        {"source": "vietocr_field", "field": "registered_birth_place",
         "lines": [{"text": "Đã Năng", "confidence": .83}]}]}
    assert parse_additional_fields(raw)["registered_birth_place"] == "Đà Nẵng"


def test_residence_region_keeps_house_line_separate(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw = {"lines": [], "attempts": [{"source": "new_card_region", "field": "residence_address",
        "lines": [{"text": "25 Đường A", "confidence": .96},
                  {"text": "Phường B, Quận C, Tỉnh D", "confidence": .94}]}]}
    assert parse_additional_fields(raw)["residence_address"] == "25 Đường A Phường B, Quận C, Tỉnh D"


def test_inline_residence_crop_uses_heading_and_value_boxes(foundation):
    from app.services.vietocr_local import residence_detail_box
    raw = {"ocr_size": {"width": 1200, "height": 800}, "lines": [{"words": [
        {"text": "residence:", "bbox": [200, 40, 100, 20]},
        {"text": "25", "bbox": [310, 40, 20, 20]},
        {"text": "Street", "bbox": [340, 40, 90, 20]}]}]}
    assert residence_detail_box(raw, 1200, 800) == (307, 37, 433, 63)


def test_missing_tones_restored_without_overwriting_existing_tones(foundation):
    from app.services.identity_parser import restore_missing_tones
    assert restore_missing_tones("Hòa My", "Hoa Mỹ") == "Hòa Mỹ"
    assert restore_missing_tones("Hòa My", "Hoa Minh") == "Hòa My"


def test_same_address_suffix_uses_stronger_independent_reading(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw={"lines":[],"attempts":[
        {"source":"new_card_region","field":"residence_address","lines":[{"text":"25 Đường A, Phường B, Quận C, Đà Nẵng","confidence":.96}]},
        {"source":"new_card_region","field":"registered_birth_place","lines":[{"text":"Phường B, Quận C, Đà Năng","confidence":.92}]}]}
    assert parse_additional_fields(raw)["registered_birth_place"] == "Phường B, Quận C, Đà Nẵng"


def test_multiline_address_keeps_tones_and_model_line_boundaries(foundation):
    from app.services.identity_parser import restore_missing_tones
    assert restore_missing_tones("Khối A Hưng Dũng, Thanh phố B", "Khối A Hưng Dúng, Thành phố B", punctuation=True) == "Khối A Hưng Dũng, Thành phố B"


def test_shared_new_card_suffix_does_not_insert_a_comma_at_line_wrap(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw={"lines":[],"attempts":[
        {"source":"new_card_region","field":"residence_address","lines":[
            {"text":"25 Đường A","confidence":.91},
            {"text":"Phường B, Quận C, Đà Nẵng","confidence":.96}]},
        {"source":"new_card_region","field":"registered_birth_place","lines":[
            {"text":"Phường B, Quận C, Đà Năng","confidence":.92}]}]}
    values=parse_additional_fields(raw)
    assert values['residence_address']=='25 Đường A Phường B, Quận C, Đà Nẵng'
    assert values['registered_birth_place']=='Phường B, Quận C, Đà Nẵng'


def test_new_card_house_number_comma_stays_with_street(foundation):
    from app.services.identity_parser import format_residence_lines
    assert format_residence_lines(['25, Đường A 3', 'Phường B, Quận C, Tỉnh D']) == '25, Đường A 3 Phường B, Quận C, Tỉnh D'
    assert format_residence_lines(['Khối A', 'Phường B']) == 'Khối A Phường B'


def test_line_break_keeps_first_printed_comma_boundary(foundation):
    from app.services.identity_parser import format_residence_lines
    value=format_residence_lines(['Ấp Kiểm Thử', 'Xã A, Huyện B, Tỉnh C'])
    assert value=='Ấp Kiểm Thử Xã A, Huyện B, Tỉnh C'
    parts=value.split(',')
    assert parts[0]=='Ấp Kiểm Thử Xã A'
    assert parts[1].strip()=='Huyện B'
    assert parts[-1].strip()=='Tỉnh C'
    assert format_residence_lines(['Ấp Kiểm Thử,','Xã A, Tỉnh C'])=='Ấp Kiểm Thử, Xã A, Tỉnh C'


def test_high_confidence_name_can_correct_a_misread_tone(foundation):
    from app.services.identity_parser import parse_fields
    raw={"lines":[{"text":"Họ và tên: NGUYÊN VĂN AN","confidence":.95}],"attempts":[
        {"source":"vietocr_field","field":"full_name","lines":[{"text":"NGUYỄN VĂN AN","confidence":.89}]}]}
    assert parse_fields(raw)["full_name"] == "NGUYỄN VĂN AN"
    raw['attempts'][0]['lines'][0]['confidence']=.8
    assert parse_fields(raw)["full_name"] == "NGUYÊN VĂN AN"


class Process:
    returncode=0
    output=b'[{"id":0,"text":"TEST NAME","confidence":0.95}]'
    timeout=False
    killed=False
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def communicate(self,payload=None,timeout=None):
        if self.timeout and not self.killed:raise subprocess.TimeoutExpired("vietocr",timeout)
        return self.output,None
    def kill(self):self.killed=True


def test_child_receives_crops_not_secrets(foundation,model_root,monkeypatch):
    from app.services import vietocr_local
    monkeypatch.setenv("DOCUMENT_DATABASE_URL","private-dsn")
    monkeypatch.setenv("DOCUMENT_SUPABASE_SERVICE_ROLE_KEY","private-key")
    def spawn(command,**kwargs):
        assert command[1:] == ["-m","app.services.vietocr_worker"]
        assert "DOCUMENT_DATABASE_URL" not in kwargs["env"]
        assert "DOCUMENT_SUPABASE_SERVICE_ROLE_KEY" not in kwargs["env"]
        assert kwargs["env"]["DOCUMENT_VIETOCR_MODEL_DIR"] == model_root
        assert kwargs["stderr"] == subprocess.DEVNULL
        return Process()
    monkeypatch.setattr(vietocr_local.subprocess,"Popen",spawn)
    pixels,raw=image_raw()
    attempts=vietocr_local.read_regions(pixels,raw,root=model_root,threads=2,deadline=time.monotonic()+10)
    assert len(attempts)==1 and attempts[0]["model"]=="vgg_seq2seq"


@pytest.mark.parametrize("score,accepted",[(.8,True),(.7,False)])
def test_model_score_filter(foundation,model_root,monkeypatch,score,accepted):
    from app.services import vietocr_local
    process=Process();process.output=json.dumps([{"id":0,"text":"TEST NAME","confidence":score}]).encode()
    monkeypatch.setattr(vietocr_local.subprocess,"Popen",lambda *args,**kwargs:process)
    pixels,raw=image_raw()
    result=vietocr_local.read_regions(pixels,raw,root=model_root,threads=2,deadline=time.monotonic()+10)
    assert bool(result)==accepted


def test_enable_flag_changes_idempotency_namespace(foundation,monkeypatch):
    from app.services.local_ocr import LocalOcr
    monkeypatch.setenv("DOCUMENT_VIETOCR_ENABLED","0");first=LocalOcr().request_namespace
    monkeypatch.setenv("DOCUMENT_VIETOCR_ENABLED","1");second=LocalOcr().request_namespace
    assert first!=second


def test_timeout_kills_and_reaps_child(foundation,model_root,monkeypatch):
    from app.services import vietocr_local
    process=Process();process.timeout=True
    monkeypatch.setattr(vietocr_local.subprocess,"Popen",lambda *args,**kwargs:process)
    pixels,raw=image_raw()
    with pytest.raises(foundation[3]) as error:
        vietocr_local.read_regions(pixels,raw,root=model_root,threads=2,deadline=time.monotonic()+10)
    assert error.value.code=="OCR_TIMEOUT" and process.killed


@pytest.mark.parametrize("payload", [[],[{"id":99,"text":"TEST","confidence":.95}],
    [{"id":0,"text":"TEST","confidence":2}],[{"id":0,"text":3,"confidence":.95}]])
def test_invalid_output_rejected(foundation,model_root,monkeypatch,payload):
    from app.services import vietocr_local
    process=Process();process.output=json.dumps(payload).encode()
    monkeypatch.setattr(vietocr_local.subprocess,"Popen",lambda *args,**kwargs:process)
    pixels,raw=image_raw()
    with pytest.raises(foundation[3]) as error:
        vietocr_local.read_regions(pixels,raw,root=model_root,threads=2,deadline=time.monotonic()+10)
    assert error.value.code=="OCR_BAD_RESPONSE"


def test_incomplete_issuer_is_not_reported_as_complete(foundation):
    from app.services.identity_parser import parse_fields
    raw={'source':'vietocr_field','field':'issue_place','lines':[{'text':'CỤC CẢNH SÁT','confidence':.98}]}
    assert parse_fields(raw)['issue_place'] is None
    raw['lines'][0]['text']='CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI'
    assert parse_fields(raw)['issue_place']==raw['lines'][0]['text']


def test_address_heading_crop_does_not_conflict_with_value(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw={'lines':[],'attempts':[
        {'source':'vietocr_field','field':'place_of_origin','variant':'primary',
         'lines':[{'text':'Quê quán Place','confidence':.9}]},
        {'source':'vietocr_field','field':'place_of_origin','variant':'old_card_layout',
         'lines':[{'text':'Phường A, Quận B, Tỉnh C','confidence':.9}]}]}
    assert parse_additional_fields(raw)['place_of_origin']=='Phường A, Quận B, Tỉnh C'


def test_complete_residence_replaces_only_matching_administrative_suffix(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw={'lines':[],'attempts':[
        {'source':'vietocr_field','field':'residence_address',
         'lines':[{'text':'Phường A, Quận B, Tỉnh C','confidence':.95}]},
        {'source':'vietocr_field','field':'residence_address','includes_inline_detail':True,
         'lines':[{'text':'Ấp Kiểm Thử, Phường A, Quận B, Tỉnh C','confidence':.9}]}]}
    assert parse_additional_fields(raw)['residence_address']=='Ấp Kiểm Thử, Phường A, Quận B, Tỉnh C'
    raw['attempts'][0]['lines'][0]['text']='Phường D, Quận B, Tỉnh C'
    assert parse_additional_fields(raw)['residence_address'] is None


def test_residence_fallback_scans_long_ink_line_and_requires_new_back(foundation):
    from PIL import ImageDraw
    from app.services.vietocr_local import residence_detail_box
    image=Image.new('RGB',(1000,625),'white');draw=ImageDraw.Draw(image)
    for x in range(330,800,15):
        draw.rectangle((x,31,x+7,51),fill='black')
    raw={'side':'back','lines':[{'text':'Date of issue / Date of expiry','confidence':.2}]}
    box=residence_detail_box(raw,1000,625,pixels=image)
    assert box and box[2]>790  # Not limited to a short example street.
    raw['side']='front'
    assert residence_detail_box(raw,1000,625,pixels=image) is None
    raw['side']='back';raw['lines'][0]['text']='Unknown label'
    assert residence_detail_box(raw,1000,625,pixels=image) is None


def test_birthplace_never_supplies_residence_detail(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw={'lines':[],'attempts':[
        {'source':'vietocr_field','field':'residence_address',
         'lines':[{'text':'Phường A, Quận B, Tỉnh C','confidence':.95}]},
        {'source':'vietocr_field','field':'registered_birth_place','includes_inline_detail':True,
         'lines':[{'text':'Đường Khác, Phường A, Quận B, Tỉnh C','confidence':.95}]}]}
    assert parse_additional_fields(raw)['residence_address']=='Phường A, Quận B, Tỉnh C'


def residence_geometry():
    return {'side':'back','ocr_size':{'width':600,'height':300},'lines':[
        {'text':'Nơi cư trú / Place of residence: 25, Đường A 3','confidence':.2,
         'polygon':[[50,40],[450,40],[450,78],[50,78]],'words':[
             {'text':'residence:','bbox':[200,50,70,18]},
             {'text':'25,','bbox':[278,52,20,18]}, {'text':'Đường','bbox':[310,52,40,18]},
             {'text':'A','bbox':[360,52,15,18]}, {'text':'3','bbox':[385,52,15,18]}]},
        {'text':'Phường B, Quận C, Tỉnh D','confidence':.2,
         'polygon':[[60,80],[300,80],[300,100],[60,100]]},
        {'text':'Nơi đăng ký khai sinh / Place of birth:','confidence':.2,
         'polygon':[[50,140],[450,140],[450,160],[50,160]]}]}


def test_residence_anchor_keeps_left_context_and_stops_before_birthplace(foundation):
    from app.services.vietocr_local import residence_value_regions
    raw=residence_geometry()
    regions=residence_value_regions(raw,600,300)
    assert regions['suffix'] == (20,76,304,104)
    assert regions['detail'] == (272,46,406,76)
    raw['side']='front'
    assert residence_value_regions(raw,600,300) is None


def test_residence_anchor_maps_source_scale(foundation):
    from app.services.vietocr_local import residence_value_regions
    regions=residence_value_regions(residence_geometry(),1200,600)
    assert regions['suffix'] == (40,152,608,208)


def test_street_only_is_not_complete_residence(foundation):
    from app.services.identity_parser import parse_additional_fields,residence_components
    raw={'side':'back','lines':[],'attempts':[{'source':'new_card_region',
         'field':'residence_address','includes_inline_detail':True,
         'lines':[{'text':'25, Đường A 3','confidence':.96}]}]}
    assert parse_additional_fields(raw)['residence_address'] is None
    assert residence_components(raw,None) is None


def complete_residence_raw():
    rows=['25, Đường A 3','Phường B, Quận C, Tỉnh D']
    return {'side':'back','lines':[],'attempts':[{'source':'new_card_region',
            'field':'residence_address','includes_inline_detail':True,'address_lines':rows,
            'lines':[{'text':row,'confidence':.96} for row in rows]}]}


def test_components_use_printed_rows_not_comma_after_house_number(foundation):
    from app.services.identity_parser import parse_additional_fields,residence_components
    raw=complete_residence_raw();value=parse_additional_fields(raw)['residence_address']
    assert value=='25, Đường A 3 Phường B, Quận C, Tỉnh D'  # Literal source stays unchanged.
    assert residence_components(raw,value)=={'detail':'25 Đường A 3','ward':'Phường B, Quận C','province':'Tỉnh D'}


def test_components_apply_same_equivalent_tone_placement_as_address(foundation):
    from app.services.identity_parser import parse_additional_fields,residence_components
    raw=complete_residence_raw()
    raw['attempts'][0]['address_lines'][0]='25, Hoà A 3'
    raw['attempts'][0]['lines'][0]['text']='25, Hoà A 3'
    value=parse_additional_fields(raw)['residence_address']
    assert residence_components(raw,value)['detail']=='25 Hòa A 3'


def test_public_detail_exposes_components_without_private_raw(foundation):
    from contextlib import contextmanager
    import copy
    from app.repositories.identity_ocr import IdentityOcr
    raw=complete_residence_raw();raw['card_pair_check']={'status':'matched'}
    class Database:
        @contextmanager
        def connection(self,**kwargs):
            assert kwargs['read_only'] is True
            yield self
        def execute(self,sql,args):
            assert 'owner_user_id=%s' in sql and args==('session','owner')
            return self
        def fetchone(self):
            return {'status':'completed','raw_front':{},'raw_back':copy.deepcopy(raw)}
    result=IdentityOcr(Database(),None,None).detail('owner','session')
    assert 'raw_front' not in result and 'raw_back' not in result
    assert result['additional_fields']['residence_address_components']=={
        'detail':'25 Đường A 3','ward':'Phường B, Quận C','province':'Tỉnh D'}


@pytest.mark.parametrize('kind',['low_confidence','birthplace','wrong_value','missing_suffix','conflict'])
def test_components_never_guess_or_borrow_a_different_address(foundation,kind):
    import copy
    from app.services.identity_parser import residence_components
    raw=complete_residence_raw();attempt=raw['attempts'][0]
    value='25, Đường A 3 Phường B, Quận C, Tỉnh D'
    if kind=='low_confidence':attempt['lines'][1]['confidence']=.5
    elif kind=='birthplace':attempt['field']='registered_birth_place'
    elif kind=='wrong_value':value='OTHER ADDRESS'
    elif kind=='missing_suffix':attempt['address_lines'].pop()
    elif kind=='conflict':
        other=copy.deepcopy(attempt);other['address_lines']=['25, Đường A','3 Phường B, Quận C, Tỉnh D']
        raw['attempts'].append(other)
    assert residence_components(raw,value) is None


def test_optional_components_match_response_schema(foundation):
    from test_document_contract_design import DOC,schema_validator
    schema=DOC['components']['schemas']['IdentityOcrResponse']['properties']['data']['properties']['additional_fields']
    value={'sex':None,'nationality':None,'place_of_origin':None,'expiry_date':None,
           'residence_address_components':{'detail':'25 Đường A 3','ward':'Phường B, Quận C','province':'Tỉnh D'}}
    schema_validator(schema,DOC).validate(value)


@pytest.mark.parametrize('side,size,headings,enabled',[
    ('front',(600,380),['Quê quán:','Nơi thường trú:'],True),
    ('back',(600,380),['Quê quán:','Nơi thường trú:'],False),
    ('front',(600,380),['Nơi cư trú:','Nơi đăng ký khai sinh:'],False),
    ('front',(380,600),['Quê quán:','Nơi thường trú:'],False)])
def test_old_layout_requires_old_front_headings(foundation,side,size,headings,enabled):
    from app.services.vietocr_local import crop_regions
    raw={'side':side,'ocr_size':{'width':size[0],'height':size[1]},
         'lines':[{'text':text,'confidence':.2} for text in headings]}
    regions=crop_regions(Image.new('RGB',size,'white'),raw)
    assert any(r.get('variant')=='old_card_layout' for r in regions)==enabled


def test_old_layout_rescues_origin_and_expiry_when_their_headings_are_lost(foundation):
    from app.services.vietocr_local import crop_regions
    raw={'side':'front','ocr_size':{'width':600,'height':380},'lines':[
        {'text':'Họ và tên / Full name:','confidence':.8},
        {'text':'Ngày sinh / Date of birth:','confidence':.8},
        {'text':'Quốc tịch / Nationality:','confidence':.8},
        {'text':'Nơi thường trú / Place of residence:','confidence':.8}]}
    regions=crop_regions(Image.new('RGB',(600,380),'white'),raw)
    rescued={r['field'] for r in regions if r.get('variant')=='old_card_layout'}
    assert {'place_of_origin','expiry_date'} <= rescued


def test_old_layout_survives_a_damaged_residence_heading(foundation):
    from app.services.vietocr_local import crop_regions
    raw={'side':'front','ocr_size':{'width':1280,'height':825},'lines':[
        {'text':'Họ và tên / Full name:','confidence':.8},
        {'text':'Ngày sinh / Date of birth:','confidence':.8},
        {'text':'Quốc tịch / Nationality:','confidence':.8},
        {'text':'_ Quê quán / Place of origin:','confidence':.8},
        {'text':'—.- residence:','confidence':.8}]}
    regions=crop_regions(Image.new('RGB',(1280,825),'white'),raw)
    expiry=[r for r in regions if r['field']=='expiry_date' and r.get('variant')=='old_card_layout']
    assert len(expiry)==1


@pytest.mark.parametrize('field,value',[('place_of_origin','_'),('permanent_address','-'),
    ('nationality','_ Quê quán / Place of origin:')])
def test_value_only_regions_reject_punctuation_and_other_labels(foundation,field,value):
    from app.services.identity_parser import region_value
    raw={'source':'vietocr_field','field':field,
         'lines':[{'text':value,'confidence':.95}]}
    assert region_value(raw) is None


@pytest.mark.parametrize('lines',[
    [{'text':'Quốc tịch / Nationality: _ Quê quán / Place of origin:','confidence':.95}],
    [
        {'text':'Quốc tịch / Nationality:','confidence':.95,
         'polygon':[(10,10),(200,10),(200,30),(10,30)]},
        {'text':'_ Quê quán / Place of origin:','confidence':.95,
         'polygon':[(10,32),(260,32),(260,52),(10,52)]},
    ],
])
def test_full_parser_never_treats_an_ocr_damaged_label_as_nationality(foundation,lines):
    from app.services.identity_parser import parse_additional_fields
    raw={'side':'front','lines':lines}
    assert parse_additional_fields(raw)['nationality'] is None


def test_punctuation_only_origin_does_not_outrank_region_value(foundation):
    from app.services.identity_parser import parse_additional_fields
    raw={'side':'front','lines':[{'text':'Quê quán / Place of origin: _','confidence':.95}],
         'attempts':[{'side':'front','source':'vietocr_field','field':'place_of_origin',
             'lines':[{'text':'Thị trấn Vương, Tiên Lữ, Hưng Yên','confidence':.86}]}]}
    assert parse_additional_fields(raw)['place_of_origin']=='Thị trấn Vương, Tiên Lữ, Hưng Yên'


def test_alternative_regions_are_not_concatenated(foundation,model_root,monkeypatch):
    from app.services import vietocr_local
    monkeypatch.setattr(vietocr_local,'crop_regions',lambda *_:[
        {'field':'place_of_origin','variant':'primary','order':0,'image':'unused'},
        {'field':'place_of_origin','variant':'complete_line','order':0,'image':'unused'}])
    process=Process();process.output=json.dumps([
        {'id':0,'text':'Địa danh A','confidence':.95},
        {'id':1,'text':'Địa danh B','confidence':.95}]).encode()
    monkeypatch.setattr(vietocr_local.subprocess,'Popen',lambda *args,**kwargs:process)
    pixels,raw=image_raw()
    result=vietocr_local.read_regions(pixels,raw,root=model_root,threads=2,deadline=time.monotonic()+10)
    assert len(result)==2
    assert [a['lines'][0]['text'] for a in result]==['Địa danh A','Địa danh B']


def test_name_crop_skips_interleaved_portrait_fragment(foundation):
    from app.services.vietocr_local import crop_regions
    raw={"side":"front","ocr_size":{"width":1200,"height":800},"lines":[
        {"text":"Họ và tên / Full name:","confidence":.9,
         "polygon":[[360,380],[650,380],[650,420],[360,420]],"words":[
            {"text":"Họ","confidence":.9,"bbox":[360,380,25,40]},
            {"text":"Full","confidence":.9,"bbox":[500,380,45,40]},
            {"text":"name:","confidence":.9,"bbox":[550,380,60,40]}]},
        {"text":"x","confidence":.9,"polygon":[[1050,405],[1070,405],[1070,425],[1050,425]],
         "words":[{"text":"x","confidence":.9,"bbox":[1050,405,20,20]}]},
        {"text":"SAMPLE PERSON NAME","confidence":.9,
         "polygon":[[360,430],[780,430],[780,480],[360,480]],"words":[
            {"text":"SAMPLE","confidence":.9,"bbox":[360,430,110,50]},
            {"text":"PERSON","confidence":.9,"bbox":[490,430,110,50]},
            {"text":"NAME","confidence":.9,"bbox":[620,430,90,50]}]},
        {"text":"Ngày sinh / Date of birth:","confidence":.9,
         "polygon":[[360,490],[800,490],[800,530],[360,530]],"words":[]}]}
    regions=crop_regions(Image.new("RGB",(1200,800),"white"),raw)
    assert [region["field"] for region in regions].count("full_name") == 1
