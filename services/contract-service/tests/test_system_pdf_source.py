"""System PDF snapshots use the reviewed catalog path, never text rendering."""
from uuid import uuid4
from unittest import TestCase
from unittest.mock import patch

import pymupdf as fitz

from app.core.errors import ContractError
from app.schemas.generations import validate_generation
from app.services import catalog_source


def pdf_version():
    template_id, version_id, object_id = (str(uuid4()) for _ in range(3))
    return {
        "id": version_id,
        "template_id": template_id,
        "kind": "system",
        "source_format": "pdf",
        "snapshot_storage_key": f"snapshots/{template_id}/{version_id}/{object_id}.pdf",
        "extraction_snapshot": {"catalog_import": {"schema_version": 1, "visual_qa_passed": True}},
    }


class SystemPdfSourceTests(TestCase):
    def test_catalog_accepts_reviewed_pdf_snapshot_only(self):
        version = pdf_version()
        self.assertTrue(catalog_source.catalog_manifest(version))
        self.assertTrue(catalog_source.source_key_valid(version))
        self.assertIsNone(catalog_source.validate_catalog(version, {}, final=False))

        version["snapshot_storage_key"] = version["snapshot_storage_key"].replace(".pdf", ".docx")
        self.assertFalse(catalog_source.source_key_valid(version))
        version["kind"] = "personal"
        self.assertIsNone(catalog_source.catalog_manifest(version))

    def test_system_pdf_cannot_be_requested_as_docx(self):
        context = {"version": pdf_version(), "draft": {"field_values": {}}, "fields": []}
        with self.assertRaises(ContractError) as error:
            validate_generation(context, "final", "docx")
        self.assertEqual(error.exception.code, "SOURCE_FORMAT_NOT_SUPPORTED")

    def test_system_pdf_uses_blank_box_validation_and_pdf_renderer(self):
        calls = []

        class Renderer:
            def validate_mapping(self, fields, manifest, source_format, data):
                calls.append(("validate", fields, source_format, data))

            def render(self, data, source_format, fields, values, output_format, preview):
                calls.append(("render", source_format, output_format, preview))
                return b"%PDF-filled"

        with patch.object(catalog_source, "PersonalRenderer", Renderer):
            with fitz.open() as source:
                source.new_page()
                original = source.tobytes()
            fields = [{"name": "applicant", "placements": []}]
            result = catalog_source.CatalogSourceRenderer().render(
                original, fields, {"applicant": "Test"}, "pdf", preview=True
            )
        self.assertEqual(result, b"%PDF-filled")
        self.assertEqual(calls[0][0:3], ("validate", fields, "pdf"))
        self.assertTrue(calls[0][3].startswith(b"%PDF-"))
        self.assertEqual(calls[1], ("render", "pdf", "pdf", True))

    def test_system_pdf_fills_a_blank_area(self):
        with fitz.open() as source:
            page = source.new_page(width=612, height=792)
            page.insert_text((72, 72), "FORM")
            original = source.tobytes()
        fields = [{
            "name": "applicant",
            "type": "text",
            "label": "Applicant",
            "placements": [{
                "kind": "pdf_box", "page": 1, "x": 0.2, "y": 0.2,
                "width": 0.5, "height": 0.05, "font_size": 12, "multiline": False,
            }],
        }]
        result = catalog_source.CatalogSourceRenderer().render(
            original, fields, {"applicant": "Sample Applicant"}, "pdf"
        )
        with fitz.open(stream=result, filetype="pdf") as filled:
            self.assertIn("Sample Applicant", filled[0].get_text())

    def test_system_pdf_replaces_reviewed_dots_without_erasing_label(self):
        with fitz.open() as source:
            page = source.new_page(width=612, height=792)
            page.insert_text((72, 180), "Applicant: .................")
            dotted = page.search_for(".................")[0]
            original = source.tobytes()
        fields = [{
            "name": "applicant", "type": "text", "label": "Applicant",
            "placements": [{
                "kind": "pdf_dots", "page": 1,
                "x": dotted.x0 / 612, "y": dotted.y0 / 792,
                "width": dotted.width / 612, "height": dotted.height / 792,
                "font_size": 10, "multiline": False,
            }],
        }]
        result = catalog_source.CatalogSourceRenderer().render(
            original, fields, {"applicant": "Test Name"}, "pdf"
        )
        with fitz.open(stream=result, filetype="pdf") as filled:
            text = filled[0].get_text()
            self.assertIn("Test Name", text)
            label = filled[0].get_pixmap(clip=fitz.Rect(72, 160, 120, 185), colorspace=fitz.csGRAY)
            self.assertTrue(any(value < 200 for value in label.samples))

        bad = [dict(fields[0], placements=[dict(fields[0]["placements"][0], x=72 / 612)])]
        with self.assertRaises(ContractError) as error:
            catalog_source.CatalogSourceRenderer().render(original, bad, {}, "pdf")
        self.assertEqual(error.exception.code, "INVALID_MAPPING")

    def test_system_pdf_refuses_a_dotted_area_with_graphics(self):
        with fitz.open() as source:
            page = source.new_page(width=612, height=792)
            page.insert_text((100, 180), ".................")
            dotted = page.search_for(".................")[0]
            page.draw_line((dotted.x0, 180), (dotted.x1, 180))
            original = source.tobytes()
        fields = [{
            "name": "name", "type": "text", "label": "Name",
            "placements": [{
                "kind": "pdf_dots", "page": 1,
                "x": dotted.x0 / 612, "y": dotted.y0 / 792,
                "width": dotted.width / 612, "height": dotted.height / 792,
                "font_size": 10, "multiline": False,
            }],
        }]
        with self.assertRaises(ContractError) as error:
            catalog_source.CatalogSourceRenderer().render(original, fields, {}, "pdf")
        self.assertEqual(error.exception.code, "INVALID_MAPPING")
