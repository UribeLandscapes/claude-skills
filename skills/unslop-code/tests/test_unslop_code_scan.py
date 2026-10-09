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


class PrecisionTests(unittest.TestCase):
    def scan_text(self, text, name="sample.py", *extra):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(text)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path), "--json", *extra],
                capture_output=True, text=True,
            )
            return result, json.loads(result.stdout)

    def rules(self, text, name="sample.py", *extra):
        _, data = self.scan_text(text, name, *extra)
        return [(f["rule"], f["sev"]) for f in data["findings"]]

    # --- info severity ---
    def test_info_hidden_by_default_and_counted(self):
        result, data = self.scan_text("# Note: this is fine\nvalue = 1\n")
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["info_hidden"], 1)
        self.assertEqual(result.returncode, 0)

    def test_info_shown_with_flag_and_not_scored(self):
        result, data = self.scan_text("# Note: this is fine\n", "sample.py", "--severity", "info")
        self.assertIn(("note-comment", "info"), [(f["rule"], f["sev"]) for f in data["findings"]])
        self.assertEqual(data["slop_score"], 0)
        self.assertEqual(result.returncode, 0)

    def test_note_comment_not_chat_artifact(self):
        found = self.rules("# Note: cache is warm\n", "sample.py", "--severity", "info")
        self.assertNotIn(("chat-artifact", "high"), found)

    def test_weak_narrating_and_verbose_names_are_info(self):
        found = self.rules("# Create the client\nthis_is_a_very_long_name_here = 1\n",
                           "sample.py", "--severity", "info")
        self.assertIn(("narrating-comment-weak", "info"), found)
        self.assertIn(("verbose-naming", "info"), found)
        self.assertEqual(self.rules("# Create the client\n"), [])

    def test_step_comment_stays_medium(self):
        self.assertIn(("narrating-comment", "medium"), self.rules("# Step 1: load\n"))

    # --- string spans ---
    def test_docstring_fence_not_flagged(self):
        text = 'def f():\n    """\n    ```python\n    x = 1\n    ```\n    """\n'
        self.assertEqual(self.rules(text), [])

    def test_top_level_fence_still_high(self):
        self.assertIn(("chat-artifact", "high"), self.rules("```python\nx = 1\n```\n"))

    def test_code_after_closing_quotes_still_flagged(self):
        text = 'x = """a\nb"""  # rest of the code\n'
        self.assertIn(("placeholder-comment", "high"), self.rules(text))

    def test_text_after_opening_quotes_suppressed(self):
        self.assertEqual(self.rules('x = """# rest of the code\nfoo"""\n'), [])

    def test_emoji_in_docstring_still_flagged(self):
        text = 'x = """\n\U0001F680 launch\n"""\n'
        self.assertIn(("emoji-in-code", "medium"), self.rules(text))

    # --- generated files ---
    def test_min_named_and_minified_files_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.min.js").write_text("try{x()}catch(e){}\n")
            (root / "bundle.js").write_text("var a=1;" * 400 + "\ncatch(e){}\n")
            (root / "real.js").write_text("var a = 1;\n" * 50 + "x" * 1200 + "\n")
            result = subprocess.run([sys.executable, str(SCRIPT), str(root), "--json"],
                                    capture_output=True, text=True)
            data = json.loads(result.stdout)
        self.assertEqual(data["files_skipped_generated"], 2)
        self.assertEqual(data["files_scanned"], 1)
        self.assertFalse([f for f in data["findings"] if f["rule"] == "swallowed-errors"])

    # --- placeholder TODO ---
    def test_specific_todo_not_flagged_bare_stub_flagged(self):
        self.assertEqual(self.rules("# TODO: Add retry for the upload endpoint\n"), [])
        self.assertIn(("placeholder-comment", "high"), self.rules("# TODO: implement\n"))
        self.assertIn(("placeholder-comment", "high"), self.rules("# TODO: implement this\n"))

    # --- swallowed errors via AST ---
    def swallow(self, handler, *extra, body="operation()"):
        found = self.rules("try:\n    " + body + "\n" + handler, "sample.py", *extra)
        return [sev for rule, sev in found if rule == "swallowed-errors"]

    def test_unexplained_pass_medium(self):
        self.assertEqual(self.swallow("except Exception:\n    pass\n"), ["medium"])
        self.assertEqual(self.swallow("except Exception:  # ignore errors\n    pass\n"), ["medium"])

    def test_explicit_suppression_demotes(self):
        h = "except Exception:  # pylint: disable=broad-except\n    pass\n"
        self.assertEqual(self.swallow(h), [])
        self.assertEqual(self.swallow(h, "--severity", "info"), ["info"])

    def test_noqa_e501_does_not_demote(self):
        self.assertEqual(self.swallow("except Exception:  # noqa: E501\n    pass\n"), ["medium"])
        self.assertEqual(self.swallow("except Exception:  # noqa: BLE001\n    pass\n"), [])

    def test_long_comment_demotes_but_lint_directive_words_do_not(self):
        self.assertEqual(self.swallow(
            "except Exception:\n    # best effort, the cache is optional here\n    pass\n"), [])
        self.assertEqual(self.swallow(
            "except Exception:  # pylint: disable=line-too-long\n    pass\n"), ["medium"])

    def test_cleanup_call_demotes(self):
        self.assertEqual(self.swallow("except Exception:\n    pass\n", body="conn.close()"), [])
        self.assertEqual(self.swallow("except Exception:\n    pass\n", body="conn.fetch()"), ["medium"])

    def test_bare_except_only_explicit_codes_demote(self):
        self.assertEqual(self.swallow("except:\n    pass  # we do not care about this at all\n"), ["medium"])
        self.assertEqual(self.swallow("except:\n    pass\n", body="conn.close()"), ["medium"])
        self.assertEqual(self.swallow("except:  # noqa: E722\n    pass\n"), [])

    def test_except_text_in_docstring_not_flagged(self):
        text = 'def f():\n    """\n    except Exception:\n        pass\n    """\n'
        self.assertEqual(self.rules(text), [])

    # --- verdict ---
    def test_single_sloppy_file_is_strong(self):
        text = ("```python\n# ... rest of your code\ndef process_data(x):\n    try:\n"
                "        run()\n    except Exception:\n        pass\n")
        result, data = self.scan_text(text)
        self.assertIn("STRONG", data["verdict"])
        self.assertEqual(result.returncode, 1)
        found = [(f["rule"], f["sev"]) for f in data["findings"]]
        for want in [("chat-artifact", "high"), ("placeholder-comment", "high"),
                     ("swallowed-errors", "medium"), ("generic-naming", "medium")]:
            self.assertIn(want, found)

    def test_few_medium_in_large_tree_not_strong(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n in range(900):
                (root / f"m{n}.py").write_text("value = 1\n")
            for n in range(9):
                (root / f"e{n}.py").write_text("x = '\U0001F680'\n")
            result = subprocess.run([sys.executable, str(SCRIPT), str(root), "--json"],
                                    capture_output=True, text=True)
            data = json.loads(result.stdout)
        self.assertNotIn("STRONG", data["verdict"])
        self.assertIn("density", data)

    def test_runs_in_isolated_mode_from_other_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text("value = 1\n")
            result = subprocess.run([sys.executable, "-I", str(SCRIPT), str(path)],
                                    capture_output=True, text=True, cwd=directory)
        self.assertIn(result.returncode, (0, 1))
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)

    def test_wrapped_cleanup_call_demotes(self):
        h = "except Exception:\n    pass\n"
        for body in ("await asyncio.wait_for(loop.run_in_executor(None, client.close), timeout=t)",
                     "await loop.run_in_executor(None, lambda: executor.shutdown(wait=True))",
                     "loop.call_soon_threadsafe(loop.stop)",
                     "if client:\n        client.close()"):
            with self.subTest(body=body):
                self.assertEqual(self.swallow(h, body=body), [])

    def test_non_cleanup_shapes_stay_medium(self):
        h = "except Exception:\n    pass\n"
        for body in ("data['k'] = serialize(obj)", "if ok:\n        data['k'] = load()",
                     "await asyncio.wait_for(fetch(), timeout=t)",
                     "client.close()\n    data['k'] = load()"):
            with self.subTest(body=body):
                self.assertEqual(self.swallow(h, body=body), ["medium"])

    def test_cleanup_as_argument_of_other_call_stays_medium(self):
        h = "except Exception:\n    pass\n"
        for body in ("save_data(conn.close())", "fetch(on_error=conn.close)",
                     "asyncio.wait_for(fetch(), conn.close)"):
            with self.subTest(body=body):
                self.assertEqual(self.swallow(h, body=body), ["medium"])

    def test_string_content_is_not_a_comment_or_directive(self):
        for body in ('"# ignore errors because everything is fine here"', '"# noqa: BLE001"'):
            with self.subTest(body=body):
                self.assertEqual(self.swallow("except Exception:\n    " + body + "\n"), ["medium"])

    def test_text_report_wording(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text("value = 1\n")
            out = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                 capture_output=True, text=True).stdout
        self.assertNotIn("regardless of severity", out)
        self.assertNotIn("regardless of severity", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
