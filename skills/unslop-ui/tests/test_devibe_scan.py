import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "devibe_scan.py"


class ScannerTests(unittest.TestCase):
    def run_scan(self, path, json_mode=True):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(path)] + (["--json"] if json_mode else []),
            capture_output=True, text=True,
        )

    def test_high_count_does_not_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.css"
            path.write_text('.item { color: #6366f1; }\n' * 256)
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
            path = Path(directory) / "sample.css"
            path.write_text('.item { color: black; }\n')
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

    def test_directory_scans_components_json_only(self):
        with tempfile.TemporaryDirectory() as directory:
            components = Path(directory) / "components.json"
            components.write_text('{"baseColor": "slate"}')
            (Path(directory) / "other.json").write_text('{"baseColor": "slate"}')
            result = self.run_scan(directory)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["files_scanned"], 1)
            self.assertEqual(len(report["findings"]), 1)
            self.assertEqual(report["findings"][0]["file"], str(components))
            self.assertEqual(report["findings"][0]["rule"], "shadcn-default-card")
