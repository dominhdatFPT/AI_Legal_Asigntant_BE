"""Pair matching fails closed; it is not authenticity/KYC."""
import copy
import pytest
from test_document_foundation import foundation


def mrz_raw(number="099000000001", name="TEST<<PERSON", birth="000101", expiry="300101"):
    def digit(text):
        values = {"<": 0, **{str(i): i for i in range(10)}, **{chr(65+i): 10+i for i in range(26)}}
        return str(sum(values[ch] * (7,3,1)[i % 3] for i,ch in enumerate(text)) % 10)
    doc = number[-9:]
    first = "IDVNM" + doc + digit(doc) + number + "<<0"
    second = birth + digit(birth) + "M" + expiry + digit(expiry) + "VNM" + "<" * 11
    second += digit(first[5:30] + second[:7] + second[8:15] + second[18:29])
    return {"side": "back", "lines": [{"text": text, "confidence": .95}
        for text in (first, second, name.ljust(30, "<"))]}


def front_fields(number="099000000001"):
    return {"identity_number": number, "full_name": "TEST PERSON", "date_of_birth": "2000-01-01"}


def test_same_card_with_all_td1_checks(foundation):
    from app.services.identity_card_match import ensure_card_pair, check_digit
    assert check_digit("D23145890") == "7"
    assert ensure_card_pair(front_fields(), {}, mrz_raw())["status"] == "matched"


def test_filler_noise_is_not_a_checksum_valid_name(foundation):
    from app.services.identity_card_match import mrz_candidates
    raw=mrz_raw(name='TEST<<PERSON<<S<<K')
    assert mrz_candidates(raw)==[]


def test_birth_corrected_only_with_independent_numeric_crop(foundation):
    from app.services.identity_card_match import ensure_card_pair
    fields={**front_fields(), 'date_of_birth':'2008-01-01'}
    raw={'lines':[], 'attempts':[{'source':'numeric_field','field':'date_of_birth',
         'lines':[{'text':'01/01/2000','confidence':.96}]}]}
    result=ensure_card_pair(fields,raw,mrz_raw())
    assert result['corrections']['date_of_birth']=='2000-01-01'
    assert ensure_card_pair({**fields,'date_of_birth':None},raw,mrz_raw())['corrections']['date_of_birth']=='2000-01-01'
    raw['attempts'][0]['lines'][0]['confidence']=.4
    with pytest.raises(foundation[3]):ensure_card_pair(fields,raw,mrz_raw())
    raw['attempts'][0]['lines'][0]['confidence']=.96
    with pytest.raises(foundation[3]):ensure_card_pair({**fields,'identity_number':'088000000002'},raw,mrz_raw())


def test_ambiguous_name_requires_independent_front_crop(foundation):
    from app.services.identity_card_match import ensure_card_pair
    fields={**front_fields(),'full_name':None}
    with pytest.raises(foundation[3]) as error:
        ensure_card_pair(fields,{},mrz_raw())
    assert error.value.code == "IDENTITY_PAIR_UNVERIFIABLE"
    raw={'lines':[],'attempts':[{'source':'vietocr_field','field':'full_name',
         'lines':[{'text':'TEST PERSON','confidence':.92}]}]}
    assert ensure_card_pair(fields,raw,mrz_raw())['corrections']['full_name']=='TEST PERSON'
    raw['attempts'][0]['lines'][0]['text']='OTHER PERSON'
    with pytest.raises(foundation[3]) as error:
        ensure_card_pair(fields,raw,mrz_raw())
    assert error.value.code == "IDENTITY_PAIR_UNVERIFIABLE"


@pytest.mark.parametrize("number,name,birth", [
    ("088000000002", "TEST<<PERSON", "000101"),
    ("099000000001", "OTHER<<PERSON", "000101"),
    ("099000000001", "TEST<<PERSON", "010101"),
])
def test_different_identity_name_or_birth_rejected(foundation,number,name,birth):
    from app.services.identity_card_match import ensure_card_pair
    with pytest.raises(foundation[3]) as error:
        ensure_card_pair(front_fields(), {}, mrz_raw(number,name,birth))
    assert error.value.code == "IDENTITY_PAIR_MISMATCH" and error.value.status == 422
    assert number not in error.value.message


@pytest.mark.parametrize("kind", ["missing", "wrong_digit", "wrong_checksum", "wrong_length", "wrong_country", "split_attempts"])
def test_unreadable_or_invalid_mrz_cannot_allow_pair(foundation,kind):
    from app.services.identity_card_match import ensure_card_pair
    raw = mrz_raw()
    if kind == "missing": raw["lines"] = []
    elif kind == "wrong_digit": raw["lines"][0]["text"] = raw["lines"][0]["text"].replace("1", "O", 1)
    elif kind == "wrong_checksum": raw["lines"][1]["text"] = raw["lines"][1]["text"][:-1] + str((int(raw["lines"][1]["text"][-1])+1)%10)
    elif kind == "wrong_length": raw["lines"][2]["text"] += "<"
    elif kind == "wrong_country": raw["lines"][0]["text"] = raw["lines"][0]["text"].replace("VNM", "UTO")
    elif kind == "split_attempts":
        other = copy.deepcopy(raw); raw["lines"] = raw["lines"][:2]; other["lines"] = other["lines"][2:]; raw["attempts"] = [other]
    with pytest.raises(foundation[3]) as error: ensure_card_pair(front_fields(), {}, raw)
    assert error.value.code == "IDENTITY_PAIR_UNVERIFIABLE"


def test_conflicting_valid_attempt_cannot_allow_pair(foundation):
    from app.services.identity_card_match import ensure_card_pair
    raw = mrz_raw(); raw["attempts"] = [mrz_raw("088000000002")]
    with pytest.raises(foundation[3]) as error: ensure_card_pair(front_fields(), {}, raw)
    assert error.value.code == "IDENTITY_PAIR_MISMATCH"


def expiry_raw(value):
    return {"lines":[{"text":"Có giá trị đến: " + value,"confidence":.95}]}


def test_wrong_visual_expiry_reconciles_only_with_independent_agreement(foundation):
    from app.services.identity_card_match import ensure_card_pair
    front = expiry_raw("01/01/2020")
    front["attempts"] = [expiry_raw("01/01/2030")]
    result = ensure_card_pair(front_fields(), front, mrz_raw())
    assert result["status"] == "matched" and result["corrections"] == {"expiry_date":"2030-01-01"}


def test_expiry_reconciliation_never_bypasses_number_mismatch(foundation):
    from app.services.identity_card_match import ensure_card_pair
    front = expiry_raw("01/01/2020");front["attempts"] = [expiry_raw("01/01/2030")]
    with pytest.raises(foundation[3]) as error:
        ensure_card_pair(front_fields("088000000002"), front, mrz_raw())
    assert error.value.code == "IDENTITY_PAIR_MISMATCH"


def test_wrong_expiry_without_second_visual_agreement_is_rejected(foundation):
    from app.services.identity_card_match import ensure_card_pair
    with pytest.raises(foundation[3]) as error:
        ensure_card_pair(front_fields(), expiry_raw("01/01/2020"), mrz_raw())
    assert error.value.code == "IDENTITY_PAIR_MISMATCH"


@pytest.mark.parametrize("kind", ["same", "different", "unreadable", "expiry"])
def test_session_cannot_complete_before_pair_gate(foundation, kind):
    from contextlib import contextmanager
    from uuid import uuid4
    from app.repositories.identity_ocr import IdentityOcr
    from app.services.identity_images import IdentityImage
    row = {"status": "processing", "raw_front": None, "raw_back": None,
           "warnings": [], "verification_status": "not_performed"}
    class Database:
        @contextmanager
        def connection(self, **kwargs):
            yield self
        def execute(self, sql, args):
            if "INSERT INTO" in sql:
                row["session_id"] = args[0]
            elif "SET raw_" in sql:
                row["raw_front" if "raw_front" in sql else "raw_back"] = copy.deepcopy(args[0].obj)
            elif "SET status='failed'" in sql:
                row.update(status="failed", error_code=args[0])
            elif "SET status='completed'" in sql:
                row.update(status="completed", fields=args[0].obj, warnings=args[1].obj)
            self.selection = None if "idempotency_key=%s" in sql else copy.deepcopy(row)
            return self
        def fetchone(self):
            return self.selection
    class Storage:
        def put_quarantine(self, *args): pass
    class Engine:
        request_namespace = "test-pair-gate"
        def read_image(self, image, *, deadline, on_response):
            if image.side == "front":
                front = {"side":"front", "lines":[]}
                if kind == "expiry":
                    front = expiry_raw("01/01/2020")
                    front["attempts"] = [expiry_raw("01/01/2030")]
                on_response(front)
                return {"full_name":"TEST PERSON", "identity_number":"099000000001",
                        "date_of_birth":"2000-01-01", "issue_date":None, "issue_place":None, "permanent_address":None}
            raw = mrz_raw("088000000002" if kind == "different" else "099000000001")
            if kind == "unreadable": raw["lines"] = []
            on_response(raw)
            return {key:None for key in ("full_name","identity_number","date_of_birth","issue_date","issue_place","permanent_address")}
    service = IdentityOcr(Database(), Storage(), Engine())
    pair = (IdentityImage("front","image/jpeg","a"*64,b"front"),
            IdentityImage("back","image/jpeg","b"*64,b"back"), "c"*64)
    if kind in ("same", "expiry"):
        result = service.create(str(uuid4()), uuid4(), pair)
        assert result["status"] == "completed" and result["card_match_status"] == "matched"
        assert result["verification_status"] == "not_performed"
        assert "raw_back" not in result and "raw_front" not in result
        if kind == "expiry":
            assert result["additional_fields"]["expiry_date"] == "2030-01-01"
            assert "OCR_EXPIRY_RECONCILED_WITH_MRZ" in result["warnings"]
    else:
        with pytest.raises(foundation[3]) as error: service.create(str(uuid4()), uuid4(), pair)
        assert error.value.code == ("IDENTITY_PAIR_MISMATCH" if kind == "different" else "IDENTITY_PAIR_UNVERIFIABLE")
        assert row["status"] == "failed" and "fields" not in row
