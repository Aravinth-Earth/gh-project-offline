from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gh_project_offline.runtime import RuntimeLogger


class RuntimeLoggerTests(unittest.TestCase):
    def test_write_exception_writes_traceback_without_duplicate_summary_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "session.log"
            logger = RuntimeLogger(log_path=log_path)

            try:
                raise OSError("timed out")
            except OSError as exc:
                logger.emit("Sync command failed: OSError: timed out")
                logger.write_exception("Sync command failed", exc)

            content = log_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("Sync command failed: OSError: timed out"), 1)
            self.assertIn("Traceback (most recent call last):", content)
            self.assertIn("OSError: timed out", content)


if __name__ == "__main__":
    unittest.main()
