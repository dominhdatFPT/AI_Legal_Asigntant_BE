"""Unit tests for chunked Supabase reads in reference_catalog and reference_uploads."""

from contextlib import contextmanager
import os
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api import reference_catalog
from app.core.errors import ContractError
from app.repositories import reference_uploads


class _FakeCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class ChunkedCatalogAndUploadTests(unittest.TestCase):
    def setUp(self):
        reference_catalog._CACHE.update(data=None, timestamp=0.0, search_index={})

    def tearDown(self):
        reference_catalog._CACHE.update(data=None, timestamp=0.0, search_index={})

    def test_load_from_database_chunks_catalog_and_uploads_on_fresh_autocommit_connections(self):
        connections_opened = []
        lock = threading.Lock()

        @contextmanager
        def fake_open_fresh(url, *, connect_timeout=5, use_dict_row=False, autocommit=True):
            assert autocommit is True
            assert use_dict_row is True
            conn_info = {"queries": []}
            with lock:
                connections_opened.append(conn_info)

            class _FakeConn:
                def execute(self, sql, params=None):
                    conn_info["queries"].append((sql, params))
                    if "FROM public.reference_classifications" in sql:
                        return _FakeCursorResult(
                            [{"key": "DD-01", "code": "DD-01", "name": "Đất đai", "domain": "land", "kind": "procedure", "procedure_id": None}]
                        )
                    if "FROM public.contract_reference_catalog" in sql:
                        assert "''::text as text" in sql
                        limit, offset = params
                        assert limit == 30
                        all_rows = [
                            {
                                "id": f"00000000-0000-4000-8000-{i:012d}",
                                "number": i + 1,
                                "name": f"Mẫu {i + 1}",
                                "description": "",
                                "domain": "land",
                                "procedure": "DD-01",
                                "procedure_code": "DD-01",
                                "procedure_name": "Đất đai",
                                "procedures": [{"key": "DD-01", "code": "DD-01", "name": "Đất đai"}],
                                "page_count": 1,
                                "format": "PDF",
                                "pdf": f"/api/reference-assets/doc-{i}.pdf",
                                "pages": [f"/api/reference-assets/doc-{i}-page-1.png"],
                                "page_dimensions": [{"width": 595, "height": 842}],
                                "text": "",
                                "content_status": "source",
                                "review_notes": [],
                                "source_id": f"doc-{i}",
                                "source_filename": f"doc-{i}.pdf",
                                "source_url": "",
                                "source_sha256": "abc",
                                "pdf_sha256": "abc",
                                "view_only": True,
                                "can_fill": False,
                            }
                            for i in range(6)
                        ]
                        return _FakeCursorResult(all_rows[offset : offset + limit])
                    if "FROM public.contract_reference_uploads" in sql:
                        limit, offset = params
                        assert limit == 3
                        uploaded = [
                            {
                                "metadata": {
                                    "id": f"20000000-0000-4000-8000-{i:012d}",
                                    "name": f"Upload {i}",
                                    "domain": "land",
                                    "procedure": "DD-01",
                                    "procedures": [],
                                    "page_count": 1,
                                    "pages": [],
                                    "text": "",
                                }
                            }
                            for i in range(4)
                        ]
                        return _FakeCursorResult(uploaded[offset : offset + limit])
                    raise AssertionError(f"Unexpected SQL: {sql}")

            yield _FakeConn()

        with patch.dict(os.environ, {"CONTRACT_DATABASE_URL": "postgresql://u:p@host:6543/postgres"}), patch.object(
            reference_catalog, "open_fresh_connection", side_effect=fake_open_fresh
        ), patch.object(reference_uploads, "open_fresh_connection", side_effect=fake_open_fresh), patch.object(
            reference_catalog, "get_asset_hashes", return_value={}
        ):
            data = reference_catalog.load_from_database()

        self.assertIsNotNone(data)
        self.assertEqual(data["counts"]["templates"], 10)
        # 1 classifications + 1 lightweight catalog (< 30 rows) + 2 upload chunks (3 + 1 rows) = 4 connections
        self.assertEqual(len(connections_opened), 4)
        self.assertTrue(all(len(c["queries"]) == 1 for c in connections_opened))

    def test_hydrate_catalog_item_text_loads_on_demand_for_detail_view(self):
        sample_id = str(uuid4())

        @contextmanager
        def fake_open_fresh(url, *, connect_timeout=5, use_dict_row=False, autocommit=True):
            class _FakeConn:
                def execute(self, sql, params):
                    assert params == (sample_id,)
                    return _FakeCursorResult([{"text": "Full extracted PDF text"}])

            yield _FakeConn()

        with patch.dict(os.environ, {"CONTRACT_DATABASE_URL": "postgresql://u:p@host:6543/postgres"}), patch.object(
            reference_catalog, "open_fresh_connection", side_effect=fake_open_fresh
        ):
            hydrated = reference_catalog._hydrate_catalog_item_text({"id": sample_id, "text": ""})
            self.assertEqual(hydrated["text"], "Full extracted PDF text")

    def test_get_uploaded_asset_reassembles_large_and_small_bytea(self):
        large_pdf = b"%PDF-1.7\n" + (b"ABCDEF1234567890" * 8000)  # ~128 KB > 49,152 chunk
        small_png = b"\x89PNG\r\n\x1a\n" + (b"X" * 1024)
        sample_id = str(uuid4())

        for payload, name_suffix, expected_mime in [
            (large_pdf, ".pdf", "application/pdf"),
            (small_png, "-page-1.png", "image/png"),
        ]:
            chunk_calls = []
            opened = []

            @contextmanager
            def fake_conn(url="", *, fresh=False, use_dict_row=False, autocommit=False):
                assert fresh is True
                assert autocommit is True
                opened.append(1)

                class _FakeAssetConn:
                    def execute(self, sql, params):
                        chunk_calls.append((sql, params))
                        if "octet_length(val)" in sql:
                            chunk_size = params[0]
                            return _FakeCursorResult([(len(payload), payload[:chunk_size])])
                        start, chunk_size = params[0], params[1]
                        return _FakeCursorResult([(payload[start - 1 : start - 1 + chunk_size],)])

                yield _FakeAssetConn()

            with self.subTest(mime=expected_mime, size=len(payload)), patch.object(
                reference_uploads, "connection", side_effect=fake_conn
            ):
                content, mime = reference_uploads.get_uploaded_asset(f"{sample_id}{name_suffix}")
                self.assertEqual(content, payload)
                self.assertEqual(mime, expected_mime)
                self.assertEqual(len(opened), 1)  # one connection per asset, all chunks reuse it
                if len(payload) > reference_uploads._BYTEA_CHUNK_SIZE:
                    self.assertGreater(len(chunk_calls), 1)
                else:
                    self.assertEqual(len(chunk_calls), 1)

    def test_get_uploaded_asset_raises_404_when_missing(self):
        @contextmanager
        def fake_missing(url="", *, fresh=False, use_dict_row=False, autocommit=False):
            class _EmptyConn:
                def execute(self, sql, params):
                    return _FakeCursorResult([(None, None)])

            yield _EmptyConn()

        with patch.object(reference_uploads, "connection", side_effect=fake_missing):
            with self.assertRaises(ContractError) as caught:
                reference_uploads.get_uploaded_asset(f"{uuid4()}-page-9.png")
            self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
