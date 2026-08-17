import sqlite3
import tempfile
import unittest
from pathlib import Path

import recall
from benchmarks.retrieval.corpus import corpus_fingerprint, load_spec, percentile, populate_corpus
from benchmarks.retrieval.evaluate import query_metric
from recall_core.indexing import connect


class RetrievalHarnessTests(unittest.TestCase):
    def test_generator_is_exact_and_deterministic(self):
        spec = load_spec()
        with tempfile.TemporaryDirectory() as tmp:
            first = connect(Path(tmp) / "first.db")
            second = connect(Path(tmp) / "second.db")
            a = populate_corpus(first, 1_000, 350036)
            b = populate_corpus(second, 1_000, 350036)
            self.assertEqual(a["sessions"], 1_000)
            self.assertEqual(a["messages"], b["messages"])
            self.assertEqual(
                corpus_fingerprint(1_000, 350036, spec),
                corpus_fingerprint(1_000, 350036, spec),
            )
            self.assertNotEqual(
                corpus_fingerprint(1_000, 350036, spec),
                corpus_fingerprint(1_000, 42, spec),
            )
            self.assertEqual(first.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0],
                             a["messages"])
            first.close()
            second.close()

    def test_metrics_are_not_self_generated(self):
        metric = query_metric(["other", "target"], ["target"], 2)
        self.assertTrue(metric["passed"])
        self.assertEqual(metric["best_rank"], 2)
        self.assertEqual(metric["reciprocal_rank"], 0.5)
        self.assertFalse(query_metric(["other"], ["target"], 2)["passed"])
        self.assertEqual(percentile([1, 2, 3, 4, 100], 0.95), 100)

    def test_semantic_cache_does_not_cross_connections_with_same_count(self):
        class NumpyOnly:
            pass

        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "a.db", Path(tmp) / "b.db"]
            seen = []
            for index, path in enumerate(paths):
                conn = connect(path)
                recall.init_db(conn)
                conn.execute("INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (str(path), f"session-{index}", "pi", "/tmp", "user", "user", "2026-01-01", 1, 1, "text", "text"))
                mid = conn.execute("SELECT id FROM messages").fetchone()[0]
                conn.execute("INSERT INTO chunks(message_id,session_id,ord,text) VALUES(?,?,0,?)",
                             (mid, f"session-{index}", f"chunk-{index}"))
                cid = conn.execute("SELECT id FROM chunks").fetchone()[0]
                conn.execute("INSERT INTO embeddings(chunk_id,vec) VALUES(?,?)",
                             (cid, np.asarray([float(index + 1), 0], dtype=np.float32).tobytes()))
                conn.commit()
                recall._EMB_CACHE = None if index == 0 else recall._EMB_CACHE
                seen.append(recall._embedding_matrix(conn, np)[2][0])
                conn.close()
            self.assertEqual(seen, ["session-0", "session-1"])
            recall._EMB_CACHE = None
