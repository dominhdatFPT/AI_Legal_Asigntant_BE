"""Task 4 image preparation only; does not mock a completed VNPT integration."""
import hashlib
import io

from PIL import Image, PngImagePlugin
import pytest

from test_document_foundation import foundation


def picture(color="red", fmt="PNG", **save_options):
    with Image.new("RGB", (24, 16), color) as img, io.BytesIO() as out:
        img.save(out, format=fmt, **save_options)
        return out.getvalue()


def module(foundation):
    from app.services import identity_images
    return identity_images


@pytest.mark.parametrize("fmt,mime,filename", [("PNG", "image/png", "front.png"),
                                               ("JPEG", "image/jpeg", "front.jpg")])
def test_fully_decodes_and_reencodes(foundation, fmt, mime, filename):
    m = module(foundation)
    result = m.prepare_identity_image("front", filename, mime, picture(fmt=fmt))
    assert result.side == "front" and result.media_type == "image/jpeg"
    assert result.sha256 == hashlib.sha256(result.content).hexdigest()
    with Image.open(io.BytesIO(result.content)) as img:
        img.load()
        assert img.format == "JPEG" and img.size == (24, 16) and img.mode == "RGB"
        assert not img.getexif() and not img.info.get("icc_profile")
    assert "content=" not in repr(result)


def test_exif_orientation_applied_and_metadata_removed(foundation):
    m = module(foundation)
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private metadata"
    data = picture(fmt="JPEG", exif=exif)
    result = m.prepare_identity_image("front", "front.jpg", "image/jpeg", data)
    with Image.open(io.BytesIO(result.content)) as img:
        assert img.size == (16, 24) and not img.getexif()
    assert b"private metadata" not in result.content


def test_png_text_and_trailing_payload_removed(foundation):
    m = module(foundation)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("comment", "private metadata")
    result = m.prepare_identity_image("front", "front.png", "image/png",
                                      picture(pnginfo=metadata) + b"private trailing payload")
    assert b"private" not in result.content


def test_transparency_flattened_on_white(foundation):
    m = module(foundation)
    with Image.new("RGBA", (10, 10), (255, 0, 0, 0)) as img, io.BytesIO() as out:
        img.save(out, format="PNG")
        result = m.prepare_identity_image("front", "front.png", "image/png", out.getvalue())
    with Image.open(io.BytesIO(result.content)) as img:
        assert min(img.getpixel((0, 0))) >= 250


@pytest.mark.parametrize("side,name,mime,data,code", [
    ("unknown", "front.png", "image/png", picture(), "VALIDATION_ERROR"),
    ("front", "../front.png", "image/png", picture(), "VALIDATION_ERROR"),
    ("front", "front.png", "image/png", b"", "UNSUPPORTED_FILE_TYPE"),
    ("front", "front.pdf", "application/pdf", b"%PDF-1.7", "UNSUPPORTED_FILE_TYPE"),
    ("front", "front.png", "image/png", b"PK\x03\x04", "UNSUPPORTED_FILE_TYPE"),
    ("front", "front.jpg", "image/jpeg", picture(), "UNSUPPORTED_FILE_TYPE"),
    ("front", "front.png", "image/png", b"\x89PNG\r\n\x1a\nmalformed", "INVALID_FILE"),
])
def test_invalid_images_are_sanitized(foundation, side, name, mime, data, code):
    m = module(foundation)
    with pytest.raises(foundation[3]) as caught:
        m.prepare_identity_image(side, name, mime, data)
    assert caught.value.code == code
    assert "malformed" not in caught.value.message and "../" not in caught.value.message


def test_input_size_limit(foundation):
    m = module(foundation)
    with pytest.raises(foundation[3]) as caught:
        m.prepare_identity_image("front", "front.png", "image/png", b"x" * (m.MAX_IDENTITY_IMAGE + 1))
    assert caught.value.status == 413


def test_output_size_limit(foundation, monkeypatch):
    m = module(foundation)
    source = picture()
    monkeypatch.setattr(m, "MAX_IDENTITY_IMAGE", len(source) + 1)
    with pytest.raises(foundation[3]) as caught:
        m.prepare_identity_image("front", "front.png", "image/png", source)
    assert caught.value.status == 413


def test_pixel_limit(foundation, monkeypatch):
    m = module(foundation)
    monkeypatch.setattr(m, "MAX_IDENTITY_PIXELS", 10)
    with pytest.raises(foundation[3]) as caught:
        m.prepare_identity_image("front", "front.png", "image/png", picture())
    assert caught.value.code == "UNSAFE_FILE"


def test_animated_png_rejected(foundation):
    m = module(foundation)
    with Image.new("RGB", (12, 12), "red") as img, Image.new("RGB", (12, 12), "blue") as next_img, io.BytesIO() as out:
        img.save(out, format="PNG", save_all=True, append_images=[next_img], duration=100)
        with pytest.raises(foundation[3]) as caught:
            m.prepare_identity_image("front", "front.png", "image/png", out.getvalue())
    assert caught.value.code == "UNSAFE_FILE"


def test_pair_order_duplicate_and_original_fingerprint(foundation):
    m = module(foundation)
    front = ("front.png", "image/png", picture("red"))
    back = ("back.png", "image/png", picture("blue"))
    first, second, fingerprint = m.prepare_identity_pair(front, back)
    assert first.side == "front" and second.side == "back"
    assert m.prepare_identity_pair(front, back)[2] == fingerprint
    assert m.prepare_identity_pair(back, front)[2] != fingerprint
    changed = (front[0], front[1], front[2] + b"trailing")
    assert m.prepare_identity_pair(changed, back)[0].sha256 == first.sha256
    assert m.prepare_identity_pair(changed, back)[2] != fingerprint
    with pytest.raises(foundation[3]) as caught:
        m.prepare_identity_pair(front, front)
    assert caught.value.code == "VALIDATION_ERROR"
