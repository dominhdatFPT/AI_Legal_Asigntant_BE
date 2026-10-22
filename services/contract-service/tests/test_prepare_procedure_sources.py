"""Local-only conversion inventory safety tests; no database or cloud required."""

from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import unittest

import pymupdf


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_procedure_sources.py"
SPEC = importlib.util.spec_from_file_location("prepare_procedure_sources", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PrepareProcedureSourcesTests(unittest.TestCase):
    @staticmethod
    def pdf_bytes():
        with pymupdf.open() as document:
            document.new_page(width=595.28, height=841.89)
            return document.tobytes()

    def test_duplicate_mirrors_do_not_create_more_records(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            business = root / "Template các thủ tục kinh doanh"
            mirror = business / "Template kinh doanh"
            business.mkdir()
            mirror.mkdir()
            sample = self.pdf_bytes()
            (business / "a.pdf").write_bytes(sample)
            (business / "b.pdf").write_bytes(sample)
            (mirror / "a.pdf").write_bytes(sample)
            records = list(module.unique_sources(root))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0][2], module.digest(sample))

    def test_pdf_verification_and_page_count(self):
        sample = self.pdf_bytes()
        self.assertEqual(module.verify_pdf(sample), 1)
        with self.assertRaises(Exception):
            module.verify_pdf(b"not a pdf")

    def test_prepare_preserves_source_and_marks_unmapped(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "sources"
            output = Path(temporary) / "output"
            root.mkdir()
            output.mkdir()
            original = self.pdf_bytes()
            path = root / "source.pdf"
            path.write_bytes(original)
            records = module.prepare(root, output)
            self.assertEqual(records[0]["status"], "prepared_not_mapped")
            self.assertEqual(records[0]["pages"], 1)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual((output / records[0]["prepared_file"]).read_bytes(), original)
            self.assertEqual(module.prepare(root, output), records)

    def test_png_conversion_is_a4(self):
        with pymupdf.open() as document:
            page = document.new_page(width=300, height=200)
            png = page.get_pixmap().tobytes("png")
        converted = module.png_to_pdf(png)
        with pymupdf.open(stream=converted, filetype="pdf") as pdf:
            self.assertEqual(len(pdf), 1)
            self.assertAlmostEqual(pdf[0].rect.width, 595.28, places=1)
            self.assertAlmostEqual(pdf[0].rect.height, 841.89, places=1)


if __name__ == "__main__":
    unittest.main()
