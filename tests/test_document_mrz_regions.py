"""Synthetic geometry only; no private card photos or personal fixtures."""
import copy
import io
import time

import pytest
from PIL import Image

from test_document_foundation import foundation
from test_document_identity_card_match import mrz_raw, front_fields


def detected():
    value = {"source":"mrz_band", "source_crop":[0,600,1000,1000],
             "ocr_size":{"width":1000,"height":400}, "lines":[]}
    for text, box in zip(("IDVNM"+"0"*25, "0"*15+"<"*10, "TEST<<PERSON"+"<"*8),
                         ((100,40,900,90), (90,90,880,140), (100,140,880,190))):
        x,y,r,b=box
        value["lines"].append({"text":text,"confidence":0,
                               "polygon":[[x,y],[r,y],[r,b],[x,b]]})
    return value


def test_coarse_short_rows_still_locate_regions_with_source_offset(foundation):
    from app.services.identity_mrz_regions import row_regions
    boxes = row_regions(detected(), (1000,1000))
    assert boxes["band"] == (0,600,1000,1000)
    assert 680 < boxes["second"][1] < 700
    assert 730 < boxes["name"][1] < 750
    scaled = detected(); scaled["ocr_size"] = {"width":2000,"height":800}
    for line in scaled["lines"]:
        line["polygon"] = [[x*2,y*2] for x,y in line["polygon"]]
    assert row_regions(scaled, (1000,1000)) == boxes


@pytest.mark.parametrize("kind", ["no_crop", "bad_crop", "no_anchor", "duplicate_anchor", "missing_row", "gap", "outside"])
def test_bad_or_ambiguous_geometry_is_not_guessed(foundation, kind):
    from app.services.identity_mrz_regions import row_regions
    value = detected()
    if kind == "no_crop": value.pop("source_crop")
    elif kind == "bad_crop": value["source_crop"][3] = 1001
    elif kind == "no_anchor": value["lines"][0]["text"] = "<"*30
    elif kind == "duplicate_anchor": value["lines"][1]["text"] = value["lines"][0]["text"]
    elif kind == "missing_row": value["lines"].pop()
    elif kind == "gap": value["lines"][2]["polygon"] = [[100,300],[880,300],[880,350],[100,350]]
    elif kind == "outside": value["lines"][0]["polygon"][0][0] = -1
    assert row_regions(value, (1000,1000)) is None


def test_separate_row_reads_never_validate_a_pair(foundation):
    from app.services.identity_mrz_regions import read_rows
    from app.services.identity_card_match import ensure_card_pair
    calls=[]; rows=mrz_raw()["lines"]
    def recognize(content, size, psm, **kwargs):
        index=len(calls)%3; calls.append(kwargs)
        with Image.open(io.BytesIO(content)) as image: assert image.size == (size['width'],size['height'])
        return {"ocr_size":size,"lines":[copy.deepcopy(rows[index])]}
    result=read_rows(Image.new("RGB", (1000,1000), "white"), detected(), recognize,
                     deadline=time.monotonic()+15, dedicated_model=True)
    assert len(calls) == 6
    assert result[0]['lines'] == rows and len(result[0]['row_crops']) == 3
    with pytest.raises(Exception) as error:
        ensure_card_pair(front_fields(), {}, result[0])
    assert error.value.code == 'IDENTITY_PAIR_UNVERIFIABLE'


@pytest.mark.parametrize("kind", ["checksum", "short_name", "missing"])
def test_retry_never_pads_or_repairs_missing_or_wrong_characters(foundation, kind):
    from app.services.identity_mrz_regions import read_rows
    from app.services.identity_card_match import mrz_candidates
    rows=mrz_raw()["lines"]; calls=[]
    if kind == 'checksum': rows[1]['text'] = rows[1]['text'][:-1] + str((int(rows[1]['text'][-1])+1)%10)
    elif kind == 'short_name': rows[2]['text'] = rows[2]['text'][:-1]
    def recognize(content,size,psm,**kwargs):
        index=len(calls)%3; calls.append(kwargs)
        return {'ocr_size':size,'lines':[] if kind=='missing' and index==2 else [copy.deepcopy(rows[index])]}
    results=read_rows(Image.new('RGB',(1000,1000),'white'),detected(),recognize,
                      deadline=time.monotonic()+15,dedicated_model=True)
    assert len(calls) == 6
    assert all(not mrz_candidates(result) for result in results)
    if kind == 'missing': assert results == []
    else: assert all(result['lines'] == rows for result in results)


@pytest.mark.parametrize("model,seconds", [(False,15),(True,0)])
def test_deadline_and_missing_dedicated_model_skip_extra_work(foundation,model,seconds):
    from app.services.identity_mrz_regions import read_rows
    def unexpected(*args,**kwargs): pytest.fail('Must not start a subprocess')
    assert read_rows(Image.new('RGB',(1000,1000)),detected(),unexpected,
                     deadline=time.monotonic()+seconds,dedicated_model=model) == []


def test_normalization_changes_pixels_not_recognized_strings(foundation):
    from app.services.identity_mrz_regions import normalize_ink
    image=Image.new('L',(300,80),180)
    for x in range(60,70):
        for y in range(20,60): image.putpixel((x,y),20)
    result=normalize_ink(image)
    assert result.size == image.size
    assert result.getpixel((65,40)) == 0 and result.getpixel((200,40)) == 255
    assert set(result.getdata()) == {0,255}


def test_whole_band_uses_mrz_model_and_keeps_checksum_validation(foundation):
    from app.services.identity_mrz_regions import read_bands
    from app.services.identity_card_match import mrz_candidates
    calls = []
    correct = mrz_raw()
    broken = copy.deepcopy(correct)
    broken["lines"][1]["text"] = broken["lines"][1]["text"][:-1] + "X"
    def recognize(content, size, psm, **kwargs):
        calls.append(kwargs)
        with Image.open(io.BytesIO(content)) as image:
            assert image.size == (size["width"], size["height"])
        return {"lines": copy.deepcopy((broken if len(calls) == 1 else correct)["lines"])}
    results = read_bands(Image.new("RGB", (1000,650), "white"), {"lines": []}, recognize,
                         deadline=time.monotonic()+15, dedicated_model=True)
    assert len(calls) == 2 and all(call["language"] == "mrz" for call in calls)
    assert not mrz_candidates(results[0]) and mrz_candidates(results[1])
    assert results[0]["lines"] == broken["lines"]  # No repaired characters.


@pytest.mark.parametrize("model,seconds", [(False,15),(True,0)])
def test_band_reader_respects_deadline_and_model_availability(foundation, model, seconds):
    from app.services.identity_mrz_regions import read_bands
    def unexpected(*args, **kwargs):
        pytest.fail("Must not start OCR")
    assert read_bands(Image.new("RGB", (1000,650)), {}, unexpected,
                      deadline=time.monotonic()+seconds, dedicated_model=model) == []


@pytest.mark.parametrize("size", [(1000,650), (1,1), (80,40)])
def test_ambiguous_or_blank_ink_does_not_invent_mrz_region(foundation, size):
    from app.services.identity_mrz_regions import rectified_band, detected_band
    assert rectified_band(Image.new("RGB", size, "white")) is None
    assert detected_band({"lines": []}, (1000,650)) is None


def test_pixel_region_deskews_three_rows_and_excludes_card_edge(foundation):
    from PIL import ImageDraw
    from app.services.identity_mrz_regions import rectified_band
    picture = Image.new("RGB", (1000,650), (220,225,220))
    draw = ImageDraw.Draw(picture)
    for y in (390,450,510):
        for x in range(100,850,25):
            draw.rectangle((x,y,x+12,y+35), fill="black")
    draw.rectangle((950,360,985,600), fill="black")
    picture = picture.rotate(-1, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    result = rectified_band(picture)
    assert result is not None
    region, geometry = result
    assert .25 <= geometry["angle"] <= 1.75
    assert 700 <= region.width < 900 and 130 <= region.height <= 220
