"""pooled_connection() must really reuse connections (psycopg's `with conn:` used to close them)."""
import unittest
from unittest.mock import patch

from app.core import database


class _FakeConn:
    def __init__(self):
        self.closed = False
        self.broken = False
        self.autocommit = False
        self.read_only = None
        self.isolation_level = None
        self.queries, self.commits, self.rollbacks = [], 0, 0

    def execute(self, sql, params=None):
        self.queries.append((sql, self.autocommit))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class PooledConnectionTests(unittest.TestCase):
    URL = "postgresql://u:p@pool-test:6543/postgres"

    def setUp(self):
        database._POOLS.clear()
        self.opened = []

        def fake_open(url, connect_timeout, use_dict_row, *, autocommit=False):
            conn = _FakeConn()
            self.opened.append(conn)
            return conn

        patcher = patch.object(database, "_open_connection", side_effect=fake_open)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(database._POOLS.clear)

    def test_reuses_one_connection_across_calls_and_commits_each(self):
        for _ in range(3):
            with database.pooled_connection(self.URL) as conn:
                conn.read_only = True
                conn.execute("SELECT 1")
        self.assertEqual(len(self.opened), 1)
        conn = self.opened[0]
        self.assertFalse(conn.closed)
        self.assertEqual(conn.commits, 3)
        # Per-transaction attributes never leak to the next borrower.
        self.assertIsNone(conn.read_only)
        # Health check is a single empty query in autocommit (1 round trip), only on reuse.
        self.assertEqual([q for q in conn.queries if q[0] == ""], [("", True)] * 2)
        self.assertFalse(conn.autocommit)

    def test_error_rolls_back_and_discards_connection(self):
        with self.assertRaises(RuntimeError):
            with database.pooled_connection(self.URL):
                raise RuntimeError("boom")
        self.assertEqual(self.opened[0].rollbacks, 1)
        self.assertTrue(self.opened[0].closed)
        with database.pooled_connection(self.URL):
            pass
        self.assertEqual(len(self.opened), 2)

    def test_idle_connection_past_ttl_is_replaced(self):
        with database.pooled_connection(self.URL):
            pass
        with patch.object(database.time, "monotonic", return_value=database.time.monotonic() + database._POOL_IDLE_TTL + 1):
            with database.pooled_connection(self.URL):
                pass
        self.assertEqual(len(self.opened), 2)
        self.assertTrue(self.opened[0].closed)


if __name__ == "__main__":
    unittest.main()
