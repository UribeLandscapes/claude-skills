import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "unslop_code_scan.py"


class ScannerTests(unittest.TestCase):
    def run_scan(self, path, json_mode=True):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(path)] + (["--json"] if json_mode else []),
            capture_output=True, text=True,
        )

    def test_high_count_does_not_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text('# rest of the code\n' * 256)
            for json_mode in (True, False):
                with self.subTest(json_mode=json_mode):
                    result = self.run_scan(path, json_mode)
                    self.assertEqual(result.returncode, 1)
                    if json_mode:
                        self.assertEqual(json.loads(result.stdout)["counts"]["high"], 256)
                    else:
                        self.assertIn("high: 256", result.stdout)

    def test_clean_file_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text('value = 1\n')
            for json_mode in (True, False):
                with self.subTest(json_mode=json_mode):
                    self.assertEqual(self.run_scan(path, json_mode).returncode, 0)

    def test_missing_path_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            for json_mode in (True, False):
                with self.subTest(json_mode=json_mode):
                    self.assertEqual(
                        self.run_scan(Path(directory) / "missing", json_mode).returncode, 2
                    )

    def assert_swallowed(self, handler, expected):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text("try:\n    operation()\n" + handler)
            result = self.run_scan(path)
            self.assertEqual(result.returncode, 0)
            findings = json.loads(result.stdout)["findings"]
            self.assertEqual(any(f["rule"] == "swallowed-errors" for f in findings), expected)

    def test_multiline_pass_is_flagged(self):
        self.assert_swallowed("except Exception:\n    pass\n", True)

    def test_logging_and_reraise_are_not_flagged(self):
        self.assert_swallowed("except Exception as e:\n    log.error(e)\n    raise\n", False)

    def test_multiline_nonempty_handlers_are_not_flagged(self):
        for body in ("log.error('failed')", "raise", "return", "value = 1"):
            with self.subTest(body=body):
                self.assert_swallowed("except Exception:\n    " + body + "\n", False)

    def test_inline_pass_is_flagged(self):
        self.assert_swallowed("except Exception: pass\n", True)

    def test_bare_except_is_flagged(self):
        self.assert_swallowed("except:\n    recover()\n", True)

    def test_empty_body_skips_comments_and_blanks(self):
        self.assert_swallowed(
            "except BaseException as e:\n    # ignored\n\n    ...\nnext_step()\n", True
        )

    def test_inline_ellipsis_is_flagged(self):
        self.assert_swallowed("except BaseException: ...\n", True)
