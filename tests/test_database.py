import tempfile
import unittest
from pathlib import Path

from tool1_dashboard.database import Tool1Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Tool1Database(Path(self._tmpdir.name) / "test.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_upsert_template(self) -> None:
        # First insertion
        self.db.upsert_template(
            stage="translation",
            provider="openai",
            path="path/to/template.txt",
            body="original template body",
            template_hash="hash1",
        )

        templates = self.db.list_templates()
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["stage"], "translation")
        self.assertEqual(templates[0]["provider"], "openai")
        self.assertEqual(templates[0]["path"], "path/to/template.txt")
        self.assertEqual(templates[0]["body"], "original template body")
        self.assertEqual(templates[0]["hash"], "hash1")

        # Upsert (update)
        self.db.upsert_template(
            stage="translation",
            provider="openai",
            path="path/to/new_template.txt",
            body="updated template body",
            template_hash="hash2",
        )

        templates = self.db.list_templates()
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["stage"], "translation")
        self.assertEqual(templates[0]["provider"], "openai")
        self.assertEqual(templates[0]["path"], "path/to/new_template.txt")
        self.assertEqual(templates[0]["body"], "updated template body")
        self.assertEqual(templates[0]["hash"], "hash2")
