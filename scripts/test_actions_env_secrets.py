#!/usr/bin/env python3
"""Saving the rotated refresh token to production. No network, no gh."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import actions_env_secrets as m  # noqa: E402


REFRESH = b"refresh-token-value"


class FakeGh:
    def __init__(self, fail_times=0, error="HTTP 403"):
        self.fail_times = fail_times
        self.error = error
        self.calls = []
        self.store = {}
        self._fails = 0

    def set_secret(self, name, value, *, environment):
        if environment != m.PRODUCTION:
            raise AssertionError(f"non-production write: {environment!r}")
        self.calls.append((name, environment, value))
        if self._fails < self.fail_times:
            self._fails += 1
            raise m.GhError(self.error)
        self.store[name] = value


class SaveRefreshTests(unittest.TestCase):
    def _save(self, gh, data=REFRESH, sleep=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(data)
            path = handle.name
        try:
            if env is None:
                code = m.save_refresh_token(gh, path, out, err, sleep=sleep)
            else:
                with patch.dict(os.environ, env, clear=True):
                    code = m.save_refresh_token(gh, path, out, err, sleep=sleep)
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.remove(path)
        return code, out.getvalue(), err.getvalue()

    def test_success_writes_environment_only(self):
        gh = FakeGh()
        code, out, err = self._save(gh)
        self.assertEqual(code, 0)
        self.assertEqual(gh.calls, [("TESLA_REFRESH_TOKEN", "production", REFRESH)])
        self.assertIn("saved rotated TESLA_REFRESH_TOKEN to the production environment", out)
        self.assertNotIn(REFRESH.decode(), out + err)
        self.assertEqual(gh.store, {"TESLA_REFRESH_TOKEN": REFRESH})

    def test_transient_failure_then_success(self):
        gh = FakeGh(fail_times=1)
        delays = []
        code, out, err = self._save(gh, sleep=delays.append)
        self.assertEqual(code, 0)
        self.assertEqual(len(gh.calls), 2)
        self.assertTrue(all(env == "production" for _name, env, _value in gh.calls))
        self.assertEqual(delays, [m.SAVE_BACKOFF_SECONDS[0]])
        self.assertEqual(gh.store["TESLA_REFRESH_TOKEN"], REFRESH)
        self.assertIn("saved rotated TESLA_REFRESH_TOKEN to the production environment", out)
        self.assertIn("retrying in 2s", err)
        self.assertNotIn(REFRESH.decode(), out + err)

    def test_production_failure_retries_then_fails_without_a_repo_write(self):
        gh = FakeGh(fail_times=10)
        delays = []
        code, out, err = self._save(gh, sleep=delays.append)
        self.assertEqual(code, 1)
        self.assertEqual(len(gh.calls), m.SAVE_ATTEMPTS)
        self.assertTrue(all(env == "production" for _name, env, _value in gh.calls))
        self.assertEqual(delays, list(m.SAVE_BACKOFF_SECONDS))
        self.assertEqual(len(m.SAVE_BACKOFF_SECONDS), m.SAVE_ATTEMPTS - 1)
        self.assertEqual(gh.store, {})
        self.assertIn("could not be saved", err)
        self.assertIn("scripts/get_refresh_token.py", err)
        self.assertIn("was not stored", err)
        self.assertIn("No repository secret was written.", err)
        self.assertIn("HTTP 403", err)
        self.assertNotIn("Fallback", err)
        self.assertNotIn(REFRESH.decode(), out + err)

    def test_unrelated_env_values_are_not_masked(self):
        gh = FakeGh(fail_times=10, error=f"HTTP 403 for {REFRESH.decode()} attempt 0")
        env = {"RUN_ATTEMPT": "0", "GH_TOKEN": "pat-value-xxxx", "TESLA_API_BASE": ""}
        code, _out, err = self._save(gh, sleep=lambda _s: None, env=env)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 403", err)
        self.assertIn("attempt 0", err)
        self.assertNotIn(REFRESH.decode(), err)
        self.assertNotIn("pat-value-xxxx", err)
        self.assertIn("***", err)

    def test_empty_file_writes_nothing(self):
        gh = FakeGh()
        code, _out, err = self._save(gh, b"\n")
        self.assertEqual(code, 1)
        self.assertEqual(gh.calls, [])
        self.assertIn("empty", err)
        self.assertIn("scripts/get_refresh_token.py", err)

    def test_missing_file(self):
        gh = FakeGh()
        out, err = io.StringIO(), io.StringIO()
        code = m.save_refresh_token(gh, os.path.join(tempfile.gettempdir(), "no-such-token-file"), out, err)
        self.assertEqual(code, 1)
        self.assertEqual(gh.calls, [])
        self.assertIn("missing", err.getvalue())


class MaskTests(unittest.TestCase):
    def test_secret_strings_skip_unrelated_and_blank_values(self):
        env = {
            "GH_TOKEN": "pat-value-xxxx",
            "GITHUB_TOKEN": "ghs-actions-token-value",
            "GH_SECRETS_PAT": "pat-value-xxxx",
            "TESLA_CLIENT_ID": "client-id-value",
            "TESLA_REFRESH_TOKEN": "refresh-token-value",
            "TESLA_SITE_ID": "site-id-value",
            "TESLA_SITE_TZ": "site-tz-value",
            "TESLA_API_BASE": "",
            "RUN_ATTEMPT": "0",
        }
        masks = m._secret_strings(env, b"file-token-value\n")
        self.assertIn("pat-value-xxxx", masks)
        self.assertIn("ghs-actions-token-value", masks)
        self.assertIn("client-id-value", masks)
        self.assertIn("refresh-token-value", masks)
        self.assertIn("site-id-value", masks)
        self.assertIn("site-tz-value", masks)
        self.assertIn("file-token-value", masks)
        self.assertIn("file-token-value\n", masks)
        self.assertNotIn("0", masks)
        self.assertNotIn("", masks)
        text = m.scrub("HTTP 403 attempt 0 token file-token-value and pat-value-xxxx", masks)
        self.assertIn("HTTP 403", text)
        self.assertIn("attempt 0", text)
        self.assertNotIn("file-token-value", text)
        self.assertNotIn("pat-value-xxxx", text)

    def test_api_base_is_masked_only_when_set(self):
        masks = m._secret_strings({"TESLA_API_BASE": "https://fleet.example.invalid", "RUN_ATTEMPT": "0"})
        self.assertIn("https://fleet.example.invalid", masks)
        self.assertNotIn("0", masks)
        self.assertEqual(m._secret_strings({"TESLA_API_BASE": "  ", "GH_TOKEN": ""}), [])
        scrubbed = m.scrub("HTTP 403 at https://fleet.example.invalid", masks)
        self.assertEqual(scrubbed, "HTTP 403 at ***")

    def test_scrub_replaces_the_longer_secret_first(self):
        self.assertEqual(m.scrub("pat-value-xxxx", ["pat", "pat-value-xxxx"]), "***")


class GhCliParsingTests(unittest.TestCase):
    def test_set_failure_scrubs_stdin_and_keeps_the_status_code(self):
        def runner(args, stdin):
            return 1, b"", b"HTTP 403 attempt 0 token=" + stdin

        gh = m.Gh("owner/repo", runner=runner)
        env = {"RUN_ATTEMPT": "0", "GH_TOKEN": "pat-value-xxxx", "TESLA_API_BASE": ""}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(m.GhError) as caught:
                gh.set_secret("TESLA_REFRESH_TOKEN", REFRESH, environment="production")
        message = str(caught.exception)
        self.assertIn("HTTP 403", message)
        self.assertIn("attempt 0", message)
        self.assertNotIn(REFRESH.decode(), message)
        self.assertIn("***", message)

    def test_set_uses_env_flag_and_stdin(self):
        seen = {}

        def runner(args, stdin):
            seen["args"] = args
            seen["stdin"] = stdin
            return 1, b"", b"HTTP 403"

        gh = m.Gh("owner/repo", runner=runner)
        with self.assertRaises(m.GhError):
            gh.set_secret("TESLA_CLIENT_ID", b"client-id-value", environment="production")
        self.assertEqual(seen["stdin"], b"client-id-value")
        self.assertEqual(
            seen["args"],
            ["gh", "secret", "set", "TESLA_CLIENT_ID", "--repo", "owner/repo", "--env", "production"],
        )
        self.assertNotIn("--body", seen["args"])

    def test_set_secret_never_calls_gh_without_production(self):
        called = []

        def runner(args, stdin):
            called.append(args)
            return 0, b"", b""

        gh = m.Gh("owner/repo", runner=runner)
        for environment in ("", "repo", "development"):
            with self.assertRaises(m.GhError):
                gh.set_secret("TESLA_REFRESH_TOKEN", REFRESH, environment=environment)
        self.assertEqual(called, [])
        gh.set_secret("TESLA_REFRESH_TOKEN", REFRESH, environment="production")
        self.assertEqual(called[0][called[0].index("--env") + 1], "production")


class CliTests(unittest.TestCase):
    def test_migrate_subcommand_is_removed(self):
        err = io.StringIO()
        with patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as caught:
                m.main(["migrate"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())

    def test_help_has_no_migrate_command(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            with self.assertRaises(SystemExit) as caught:
                m.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        text = out.getvalue()
        self.assertIn("save-refresh-token", text)
        self.assertNotIn("migrate", text)

    def test_main_save_passes_file_bytes_on_stdin(self):
        seen = {}

        def runner(args, stdin):
            seen["args"] = args
            seen["stdin"] = stdin
            return 0, b"", b""

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(REFRESH + b"\n")
            path = handle.name
        env = {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "pat-value-xxxx"}
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.stdout", out), patch("sys.stderr", io.StringIO()):
                code = m.main(["save-refresh-token", "--file", path], runner=runner)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(path))
        self.assertIn("production environment", out.getvalue())
        self.assertNotIn(REFRESH.decode(), out.getvalue())
        self.assertEqual(
            seen["args"],
            ["gh", "secret", "set", "TESLA_REFRESH_TOKEN", "--repo", "owner/repo", "--env", "production"],
        )
        # gh trims trailing newlines itself; the file bytes are passed through.
        self.assertEqual(seen["stdin"], REFRESH + b"\n")
        self.assertNotIn("--body", seen["args"])

    def test_main_save_retries_production_only_and_keeps_http_status(self):
        calls = []

        def runner(args, stdin):
            calls.append((args, stdin))
            return 1, b"", b"HTTP 403 attempt 0\n"

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(REFRESH + b"\n")
            path = handle.name
        env = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GH_TOKEN": "pat-value-xxxx",
            "RUN_ATTEMPT": "0",
        }
        err = io.StringIO()
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.stderr", err), patch("sys.stdout", out), patch.object(m, "_sleep") as sleep:
                code = m.main(["save-refresh-token", "--file", path], runner=runner)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(len(calls), m.SAVE_ATTEMPTS)
        self.assertEqual(sleep.call_count, m.SAVE_ATTEMPTS - 1)
        self.assertEqual(
            [c.args[0] for c in sleep.call_args_list],
            list(m.SAVE_BACKOFF_SECONDS),
        )
        for args, stdin in calls:
            self.assertEqual(args[args.index("--env") + 1], "production")
            self.assertEqual(stdin, REFRESH + b"\n")
            self.assertNotIn(REFRESH, args)
            joined = " ".join(args)
            self.assertNotIn(REFRESH.decode(), joined)
        text = err.getvalue()
        self.assertIn("HTTP 403", text)
        self.assertIn("attempt 0", text)
        self.assertIn("scripts/get_refresh_token.py", text)
        self.assertIn("could not be saved", text)
        self.assertNotIn(REFRESH.decode(), text)
        self.assertNotIn("pat-value-xxxx", text)
        self.assertNotIn("Fallback", text)

    def test_default_runner_sets_no_prompt_and_does_not_use_shell(self):
        with patch("actions_env_secrets.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
            with patch.dict(os.environ, {"GH_TOKEN": "pat-value-xxxx"}, clear=True):
                code, _out, _err = m.default_runner(
                    ["gh", "secret", "set", "TESLA_CLIENT_ID", "--env", "production"],
                    b"client-id-value",
                )
        self.assertEqual(code, 0)
        _args, kwargs = run.call_args
        self.assertEqual(kwargs["input"], b"client-id-value")
        self.assertEqual(kwargs["env"]["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(kwargs["env"]["GH_TOKEN"], "pat-value-xxxx")
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
