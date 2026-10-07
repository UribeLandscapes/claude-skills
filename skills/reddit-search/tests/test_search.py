import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import search  # noqa: E402

SECRET = "fake" + "-token-for-tests"
TODAY = date(2026, 10, 3)


class FakeResp:
    def __init__(self, payload):
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Http:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.calls = payload, exc, []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.exc:
            raise self.exc
        return FakeResp(self.payload)


def ok_cmd(key=SECRET):
    return lambda cmd: SimpleNamespace(returncode=0, stdout=key + "\n")


def bad_cmd(cmd):
    return SimpleNamespace(returncode=44, stdout="")


SAMPLE = {"web": {"results": [
    {"title": "<strong>Best</strong> &amp; cheap\xa0one",
     "url": "https://www.reddit.com/r/Keyboards/comments/abc/x/",
     "description": "A <b>snippet</b> &quot;here&quot;", "age": "2 days ago"},
    {"title": "Not reddit", "url": "https://evilreddit.com/r/x/", "description": "no"},
    {"title": "Short", "url": "https://redd.it/abc", "description": "d"},
    "junk",
    {"title": "old", "url": "https://old.reddit.com/r/foo_bar", "description": "o"},
]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "cache"

    def run_main(self, argv, env=None, cmd=None, http=None, today=TODAY):
        http = http or Http(SAMPLE)
        out, err = io.StringIO(), io.StringIO()
        env = {search.KEY_ENV: SECRET} if env is None else env
        with redirect_stdout(out), redirect_stderr(err):
            code = search.main(argv, env=env, run_cmd=cmd or bad_cmd,
                               http_open=http, cache_dir=self.cache, today=today)
        return code, out.getvalue(), err.getvalue(), http

    def counter(self, month="2026-10"):
        return json.loads((self.cache / f"usage-{month}.json").read_text())


class ValidationTests(Base):
    def test_empty_query_exits_2(self):
        code, _, err, http = self.run_main(["  "])
        self.assertEqual(code, 2)
        self.assertIn("empty", err)
        self.assertEqual(http.calls, [])

    def test_long_query_exits_2(self):
        self.assertEqual(self.run_main(["x" * 351])[0], 2)

    def test_bad_count_values_exit_2(self):
        for bad in ("0", "21", "abc"):
            self.assertEqual(self.run_main(["q", bad])[0], 2, bad)

    def test_bad_lang_country_exit_2(self):
        self.assertEqual(self.run_main(["q", "5", "eng"])[0], 2)
        self.assertEqual(self.run_main(["q", "5", "en", "u1"])[0], 2)

    def test_bad_freshness_exit_2(self):
        self.assertEqual(self.run_main(["q", "--freshness", "pz"])[0], 2)


class KeyTests(Base):
    def test_env_key_wins_over_keychain(self):
        calls = []
        def cmd(c):
            calls.append(c)
            return SimpleNamespace(returncode=0, stdout="other")
        _, _, _, http = self.run_main(["q"], cmd=cmd)
        self.assertEqual(calls, [])
        self.assertEqual(http.calls[0][0].get_header("X-subscription-token"), SECRET)

    def test_keychain_fallback(self):
        _, _, _, http = self.run_main(["q"], env={}, cmd=ok_cmd("kc-key"))
        self.assertEqual(http.calls[0][0].get_header("X-subscription-token"), "kc-key")

    def test_missing_key_prints_setup_and_exits_1(self):
        code, _, err, http = self.run_main(["q"], env={}, cmd=bad_cmd)
        self.assertEqual(code, 1)
        self.assertIn("security add-generic-password -s brave-search -a api-key -w", err)
        self.assertEqual(http.calls, [])

    def test_keychain_oserror_is_missing_key(self):
        def boom(c):
            raise FileNotFoundError("security")
        code, _, err, _ = self.run_main(["q"], env={}, cmd=boom)
        self.assertEqual(code, 1)
        self.assertIn("api-key", err)


class CapTests(Base):
    def test_cap_refuses_before_http(self):
        self.cache.mkdir()
        (self.cache / "usage-2026-10.json").write_text('{"requests": 900}')
        code, _, err, http = self.run_main(["q"])
        self.assertEqual(code, 1)
        self.assertIn("cap", err.lower())
        self.assertIn("BRAVE_MONTHLY_CAP", err)
        self.assertEqual(http.calls, [])

    def test_custom_cap_env(self):
        self.cache.mkdir()
        (self.cache / "usage-2026-10.json").write_text('{"requests": 5}')
        code, *_ = self.run_main(["q"], env={search.KEY_ENV: SECRET, "BRAVE_MONTHLY_CAP": "5"})
        self.assertEqual(code, 1)

    def test_invalid_cap_env_refuses(self):
        code, *_ = self.run_main(["q"], env={search.KEY_ENV: SECRET, "BRAVE_MONTHLY_CAP": "x"})
        self.assertEqual(code, 1)

    def test_counter_increments_and_persists(self):
        self.run_main(["q"])
        self.run_main(["q"])
        self.assertEqual(self.counter(), {"requests": 2})

    def test_new_month_uses_new_file(self):
        self.run_main(["q"])
        self.run_main(["q"], today=date(2026, 11, 1))
        self.assertEqual(self.counter("2026-10"), {"requests": 1})
        self.assertEqual(self.counter("2026-11"), {"requests": 1})

    def test_counter_incremented_even_when_request_fails(self):
        http = Http(exc=urllib.error.URLError("down"))
        self.run_main(["q"], http=http)
        self.assertEqual(self.counter(), {"requests": 1})

    def test_corrupt_counter_refuses_and_is_untouched(self):
        self.cache.mkdir()
        p = self.cache / "usage-2026-10.json"
        p.write_text("{not json")
        code, _, err, http = self.run_main(["q"])
        self.assertEqual(code, 1)
        self.assertIn("unreadable", err)
        self.assertEqual(p.read_text(), "{not json")
        self.assertEqual(http.calls, [])

    def test_invalid_counter_value_refuses(self):
        self.cache.mkdir()
        (self.cache / "usage-2026-10.json").write_text('{"requests": -3}')
        self.assertEqual(self.run_main(["q"])[0], 1)

    def test_unwritable_cache_refuses(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        self.cache = blocker / "sub"
        code, _, err, http = self.run_main(["q"])
        self.assertEqual(code, 1)
        self.assertEqual(http.calls, [])


class ConcurrencyTests(Base):
    def test_concurrent_reservations_never_exceed_cap(self):
        cap, workers = 5, 16
        env = {search.CAP_ENV: str(cap)}
        results, barrier = [], threading.Barrier(workers)

        def worker():
            barrier.wait()
            try:
                search.reserve_request(env, self.cache, TODAY)
                results.append(True)
            except search.SearchError:
                results.append(False)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(True), cap)
        self.assertEqual(self.counter(), {"requests": cap})


class RedactionTests(Base):
    def test_key_in_error_body_is_redacted(self):
        body = ("echo " + SECRET).encode()
        exc = urllib.error.HTTPError("u", 500, "m", {}, io.BytesIO(body))
        code, out, err, _ = self.run_main(["q"], http=Http(exc=exc))
        self.assertEqual(code, 1)
        self.assertNotIn(SECRET, out + err)
        self.assertIn("[redacted]", err)

    def test_key_in_network_error_is_redacted(self):
        exc = urllib.error.URLError("fail " + SECRET)
        _, out, err, _ = self.run_main(["q"], http=Http(exc=exc))
        self.assertNotIn(SECRET, out + err)


class TruncatedResponseTests(Base):
    def test_incomplete_read_is_friendly_error(self):
        class BadResp(FakeResp):
            def read(self):
                raise http.client.IncompleteRead(b"par", 10)

        http_open = lambda request, timeout=None: BadResp({})
        code, out, err, _ = self.run_main(["q"], http=http_open)
        self.assertEqual(code, 1)
        self.assertIn("Network error", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")


class MalformedUrlTests(Base):
    def test_malformed_url_skipped_valid_kept(self):
        payload = {"web": {"results": [
            {"title": "bad", "url": "http://[::1", "description": "x"},
            {"title": "good", "url": "https://reddit.com/r/ok/", "description": "y"}]}}
        code, out, _, _ = self.run_main(["q"], http=Http(payload))
        self.assertEqual(code, 0)
        self.assertIn("Title: good", out)
        self.assertNotIn("Title: bad", out)


class HttpErrorTests(Base):
    def http_err(self, code, body=b"boom"):
        return urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(body))

    def test_401_and_403_key_rejected(self):
        for c in (401, 403):
            code, out, err, _ = self.run_main(["q"], http=Http(exc=self.http_err(c)))
            self.assertEqual(code, 1)
            self.assertIn("rejected", err)

    def test_429_rate_limited(self):
        code, _, err, _ = self.run_main(["q"], http=Http(exc=self.http_err(429)))
        self.assertEqual(code, 1)
        self.assertIn("rate limited", err)

    def test_other_status_includes_truncated_body(self):
        code, _, err, _ = self.run_main(["q"], http=Http(exc=self.http_err(500, b"E" * 500)))
        self.assertEqual(code, 1)
        self.assertIn("500", err)
        self.assertIn("E" * 300, err)
        self.assertNotIn("E" * 301, err)

    def test_url_error_network_message(self):
        code, _, err, _ = self.run_main(["q"], http=Http(exc=urllib.error.URLError("dns")))
        self.assertEqual(code, 1)
        self.assertIn("Network", err)

    def test_timeout_network_message(self):
        code, _, err, _ = self.run_main(["q"], http=Http(exc=TimeoutError("t")))
        self.assertEqual(code, 1)
        self.assertIn("Network", err)

    def test_non_json_response_errors(self):
        code, _, err, _ = self.run_main(["q"], http=Http(b"<html>"))
        self.assertEqual(code, 1)
        self.assertIn("non-JSON", err)

    def test_key_never_in_output_on_any_path(self):
        cases = [Http(exc=self.http_err(401, SECRET.encode())), Http(SAMPLE),
                 Http(exc=urllib.error.URLError("x"))]
        for http in cases:
            _, out, err, _ = self.run_main(["q"], http=http)
            self.assertNotIn(SECRET, out + err)


class OutputTests(Base):
    def test_non_reddit_and_junk_filtered(self):
        _, out, _, _ = self.run_main(["q"])
        self.assertNotIn("evilreddit", out)
        self.assertNotIn("junk", out)
        self.assertEqual(out.count("Title:"), 3)

    def test_html_stripped(self):
        _, out, _, _ = self.run_main(["q"])
        self.assertIn("Title: Best & cheap one", out)
        self.assertIn('Snippet: A snippet "here"', out)
        self.assertNotIn("<", out)

    def test_subreddit_age_and_missing_subreddit(self):
        _, out, _, _ = self.run_main(["q"])
        self.assertIn("Subreddit: r/Keyboards", out)
        self.assertIn("Subreddit: r/foo_bar", out)
        self.assertIn("Age: 2 days ago", out)
        short = out.split("Title: Short")[1].split("\n\n")[0]
        self.assertNotIn("Subreddit", short)

    def test_json_output(self):
        _, out, _, _ = self.run_main(["q", "--json"])
        data = json.loads(out)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["subreddit"], "Keyboards")

    def test_missing_results_prints_none(self):
        for payload in ({}, {"web": {}}, {"web": {"results": "x"}}, []):
            _, out, _, _ = self.run_main(["q"], http=Http(payload))
            self.assertEqual(out.strip(), "No Reddit results.")


class RequestTests(Base):
    def test_request_url_params_headers_timeout(self):
        _, _, _, http = self.run_main(["my query", "7", "es", "mx", "--freshness", "pw"])
        req, timeout = http.calls[0]
        u = urlparse(req.full_url)
        self.assertEqual(f"{u.scheme}://{u.netloc}{u.path}", search.API_URL)
        q = parse_qs(u.query)
        self.assertEqual(q["q"], ["site:reddit.com my query"])
        self.assertEqual(q["count"], ["7"])
        self.assertEqual(q["search_lang"], ["es"])
        self.assertEqual(q["country"], ["mx"])
        self.assertEqual(q["freshness"], ["pw"])
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertEqual(req.get_header("X-subscription-token"), SECRET)
        self.assertEqual(timeout, 30)

    def test_defaults_and_no_freshness(self):
        _, _, _, http = self.run_main(["q"])
        q = parse_qs(urlparse(http.calls[0][0].full_url).query)
        self.assertEqual((q["count"], q["search_lang"], q["country"]), (["10"], ["en"], ["us"]))
        self.assertNotIn("freshness", q)

    def test_default_run_cmd_invokes_subprocess(self):
        res = search._default_run_cmd([sys.executable, "-c", "print('hi')"])
        self.assertEqual(res.stdout.strip(), "hi")


if __name__ == "__main__":
    unittest.main()
