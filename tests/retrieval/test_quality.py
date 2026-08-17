import tempfile
import unittest
from pathlib import Path

import recall
from benchmarks.retrieval.corpus import load_spec, materialize_scale_embeddings, populate_corpus
from benchmarks.retrieval.evaluate import evaluate_quality
from recall_core.indexing import connect


class RetrievalQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / "quality.db"
        cls.conn = connect(cls.db_path)
        populate_corpus(cls.conn, 100)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_required_lexical_and_regex_goldens(self):
        quality = evaluate_quality(self.conn, semantic=False)
        self.assertEqual(quality["total"], 7)
        self.assertEqual(quality["pass_rate"], 1.0)
        self.assertGreaterEqual(quality["mrr"], 0.75)
        self.assertTrue(all(result["passed"] for result in quality["queries"].values()))


@unittest.skipIf(recall._semantic_dependency_error() is not None,
                 "semantic evaluation dependencies are not installed")
class SemanticRetrievalQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / "semantic-quality.db"
        cls.conn = connect(cls.db_path)
        populate_corpus(cls.conn, 100)
        recall._EMB_CACHE = None
        recall.build_embeddings(cls.conn, quiet=True, rechunk=True)
        materialize_scale_embeddings(cls.conn)
        cls.conn.execute("DELETE FROM embed_meta")
        cls.conn.execute("INSERT INTO embed_meta(model,dim) VALUES(?,?)", (recall.EMBED_MODEL, 384))
        cls.conn.commit()
        recall._EMB_CACHE = None

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        recall._EMB_CACHE = None
        cls.tmp.cleanup()

    def test_semantic_why_golden(self):
        quality = evaluate_quality(self.conn, semantic=True)
        result = quality["queries"]["semantic-why"]
        self.assertTrue(result["passed"], result)
        self.assertLessEqual(result["best_rank"], 3)
