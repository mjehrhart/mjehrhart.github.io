#!/usr/bin/env python3
"""Behavior of the production-environment secret migration. No network, no gh."""
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


STATIC = {
    "TESLA_CLIENT_ID": "client-id-value",
    "TESLA_SITE_ID": "site-id-value",
    "TESLA_SITE_TZ": "site-tz-value",
    "TESLA_API_BASE": "",
    "GH_SECRETS_PAT": "pat-value-xxxx",
}
REFRESH = b"refresh-token-value"


class FakeGh:
    def __init__(self, env_names=None, fail_set=None, delete_result="deleted"):
        self.env_names = set(env_names or [])
        self.fail_set = set(fail_set or [])
        self.delete_result = delete_result
        self.calls = []
        self.store = {"production": {}, "repo": {}}

    def list_names(self):
        self.calls.append(("list",))
        if "list" in self.fail_set:
            raise m.GhError("HTTP 404: Not Found")
        return sorted(self.env_names)

    def set_secret(self, name, value, *, environment):
        where = environment or "repo"
        self.calls.append(("set", name, where, value))
        key = f"set:{where}:{name}"
        if key in self.fail_set or "set" in self.fail_set:
            raise m.GhError(f"HTTP 403 for {name} value {value.decode()}")
        self.store[where][name] = value
        if environment:
            self.env_names.add(name)

    def delete_env_secret(self, name):
        self.calls.append(("delete", name))
        if self.delete_result == "deleted":
            self.env_names.discard(name)
            self.store["production"].pop(name, None)
        return self.delete_result


class MigrateTests(unittest.TestCase):
    def test_first_run_copies_only_missing_nonempty_and_never_the_refresh_token(self):
        gh = FakeGh()
        out, err = io.StringIO(), io.StringIO()
        env = dict(STATIC, TESLA_REFRESH_TOKEN="spent-refresh-token")
        self.assertEqual(m.migrate(gh, env, out, err), 0)
        copied = [c for c in gh.calls if c[0] == "set"]
        self.assertEqual(
            [c[1] for c in copied],
            ["TESLA_CLIENT_ID", "TESLA_SITE_ID", "TESLA_SITE_TZ", "GH_SECRETS_PAT"],
        )
        self.assertTrue(all(c[2] == "production" for c in copied))
        self.assertNotIn("TESLA_REFRESH_TOKEN", gh.store["production"])
        self.assertNotIn("TESLA_API_BASE", gh.store["production"])
        text = out.getvalue() + err.getvalue()
        for secret in (*STATIC.values(), "spent-refresh-token"):
            if secret:
                self.assertNotIn(secret, text)
        self.assertIn("TESLA_API_BASE: not set", text)
        self.assertIn("not copied", text)

    def test_second_run_does_not_rewrite_existing_environment_secrets(self):
        gh = FakeGh(env_names=set(m.COPY_IF_MISSING) | {"TESLA_REFRESH_TOKEN"})
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(m.migrate(gh, dict(STATIC, TESLA_API_BASE="https://example.invalid"), out, err), 0)
        self.assertEqual([c for c in gh.calls if c[0] == "set"], [])
        self.assertIn("TESLA_CLIENT_ID: already in production", out.getvalue())

    def test_list_failure_stops_before_any_write(self):
        gh = FakeGh(fail_set={"list"})
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(m.migrate(gh, STATIC, out, err), 1)
        self.assertNotIn("set", [c[0] for c in gh.calls])
        self.assertIn("not exchanged", err.getvalue())
        self.assertIn("Environments: Read and write", err.getvalue())

    def test_copy_failure_stops_and_scrubs_the_value(self):
        gh = FakeGh(fail_set={"set:production:TESLA_CLIENT_ID"})
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(m.migrate(gh, STATIC, out, err), 1)
        self.assertEqual([c[1] for c in gh.calls if c[0] == "set"], ["TESLA_CLIENT_ID"])
        text = err.getvalue()
        self.assertNotIn("client-id-value", text)
        self.assertIn("***", text)
        self.assertIn("not exchanged", text)


class SaveRefreshTests(unittest.TestCase):
    def _save(self, gh, data=REFRESH):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(data)
            path = handle.name
        try:
            code = m.save_refresh_token(gh, path, out, err)
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.remove(path)
        return code, out.getvalue(), err.getvalue()

    def test_success_writes_environment_only(self):
        gh = FakeGh()
        code, out, err = self._save(gh)
        self.assertEqual(code, 0)
        self.assertEqual(gh.calls, [("set", "TESLA_REFRESH_TOKEN", "production", REFRESH)])
        self.assertIn("saved rotated TESLA_REFRESH_TOKEN to the production environment", out)
        self.assertNotIn(REFRESH.decode(), out + err)
        self.assertEqual(gh.store["repo"], {})

    def test_environment_failure_falls_back_to_repo_and_clears_env_shadow(self):
        gh = FakeGh(env_names={"TESLA_REFRESH_TOKEN"}, fail_set={"set:production:TESLA_REFRESH_TOKEN"})
        gh.store["production"]["TESLA_REFRESH_TOKEN"] = b"spent-refresh-token"
        code, out, err = self._save(gh)
        self.assertEqual(code, 1)
        self.assertEqual(
            [c[:3] for c in gh.calls],
            [
                ("set", "TESLA_REFRESH_TOKEN", "production"),
                ("set", "TESLA_REFRESH_TOKEN", "repo"),
                ("delete", "TESLA_REFRESH_TOKEN"),
            ],
        )
        self.assertEqual(gh.store["repo"]["TESLA_REFRESH_TOKEN"], REFRESH)
        self.assertNotIn("TESLA_REFRESH_TOKEN", gh.env_names)
        self.assertNotIn(REFRESH.decode(), out + err)
        self.assertIn("repository secret", err)
        self.assertIn("Failing this run", err)

    def test_repo_fallback_failure_does_not_delete_environment_secret(self):
        gh = FakeGh(fail_set={"set"})
        code, _out, err = self._save(gh)
        self.assertEqual(code, 1)
        self.assertNotIn(("delete", "TESLA_REFRESH_TOKEN"), [c[:2] for c in gh.calls])
        self.assertIn("was not stored", err)
        self.assertNotIn(REFRESH.decode(), err)

    def test_missing_environment_secret_on_fallback_is_fine(self):
        gh = FakeGh(fail_set={"set:production:TESLA_REFRESH_TOKEN"}, delete_result="absent")
        code, _out, err = self._save(gh)
        self.assertEqual(code, 1)
        self.assertEqual(gh.store["repo"]["TESLA_REFRESH_TOKEN"], REFRESH)
        self.assertIn("No production TESLA_REFRESH_TOKEN", err)

    def test_delete_failure_tells_the_owner_what_to_remove(self):
        gh = FakeGh(
            env_names={"TESLA_REFRESH_TOKEN"},
            fail_set={"set:production:TESLA_REFRESH_TOKEN"},
            delete_result="failed",
        )
        code, _out, err = self._save(gh)
        self.assertEqual(code, 1)
        self.assertIn("Delete the production environment secret named TESLA_REFRESH_TOKEN", err)
        self.assertIn("repository secret has the new token", err)

    def test_empty_file_writes_nothing(self):
        gh = FakeGh()
        code, _out, err = self._save(gh, b"\n")
        self.assertEqual(code, 1)
        self.assertEqual(gh.calls, [])
        self.assertIn("empty", err)

    def test_missing_file(self):
        gh = FakeGh()
        out, err = io.StringIO(), io.StringIO()
        code = m.save_refresh_token(gh, os.path.join(tempfile.gettempdir(), "no-such-token-file"), out, err)
        self.assertEqual(code, 1)
        self.assertEqual(gh.calls, [])
        self.assertIn("missing", err.getvalue())


class GhCliParsingTests(unittest.TestCase):
    def test_set_failure_scrubs_stdin_from_gh_stderr(self):
        def runner(args, stdin):
            return 1, b"", b"failed to set secret: " + stdin

        gh = m.Gh("owner/repo", runner=runner)
        with self.assertRaises(m.GhError) as caught:
            gh.set_secret("TESLA_REFRESH_TOKEN", REFRESH, environment="production")
        self.assertNotIn(REFRESH.decode(), str(caught.exception))
        self.assertIn("***", str(caught.exception))

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
        self.assertEqual(seen["args"][seen["args"].index("--env") + 1], "production")
        self.assertNotIn("--body", seen["args"])

    def test_delete_404_is_absent_and_403_is_failed(self):
        def runner_404(_args, _stdin):
            return 1, b"", b"failed to delete secret TESLA_REFRESH_TOKEN: HTTP 404: Not Found\n"

        def runner_403(_args, _stdin):
            return 1, b"", b"HTTP 403: Resource not accessible by personal access token\n"

        self.assertEqual(m.Gh("owner/repo", runner=runner_404).delete_env_secret("TESLA_REFRESH_TOKEN"), "absent")
        failed = m.Gh("owner/repo", runner=runner_403)
        self.assertEqual(failed.delete_env_secret("TESLA_REFRESH_TOKEN"), "failed")
        self.assertIn("403", failed.last_error)

    def test_empty_list_and_no_secrets_found_mean_copy(self):
        def empty(_args, _stdin):
            return 0, b"", b""

        def no_secrets(_args, _stdin):
            return 1, b"", b"no secrets found\n"

        self.assertEqual(m.Gh("owner/repo", runner=empty).list_names(), [])
        self.assertEqual(m.Gh("owner/repo", runner=no_secrets).list_names(), [])

    def test_unexpected_list_output_is_not_printed(self):
        def runner(_args, _stdin):
            return 0, b"refresh-token-value leaked\n", b""

        gh = m.Gh("owner/repo", runner=runner)
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(m.migrate(gh, STATIC, out, err), 1)
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("refresh-token-value", text)
        self.assertIn("unexpected secret list output", text)
        self.assertIn("not exchanged", text)


class CliTests(unittest.TestCase):
    def test_main_migrate_uses_production_env_flag(self):
        calls = []

        def runner(args, stdin):
            calls.append((args, stdin))
            if args[1:3] == ["secret", "list"]:
                return 0, b"TESLA_CLIENT_ID\n", b""
            return 0, b"", b""

        env = dict(STATIC, GITHUB_REPOSITORY="owner/repo", GH_TOKEN="pat-value-xxxx")
        with patch.dict(os.environ, env, clear=True):
            code = m.main(["migrate"], runner=runner)
        self.assertEqual(code, 0)
        list_args = calls[0][0]
        self.assertIn("--env", list_args)
        self.assertIn("production", list_args)
        set_calls = [c for c in calls if c[0][1:3] == ["secret", "set"]]
        self.assertTrue(set_calls)
        for args, stdin in set_calls:
            self.assertIn("--env", args)
            self.assertNotIn("--body", args)
            self.assertTrue(stdin)
            self.assertNotIn(b"TESLA_REFRESH_TOKEN", args)

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
        with patch.dict(os.environ, env, clear=True):
            code = m.main(["save-refresh-token", "--file", path], runner=runner)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(seen["args"][1:4], ["secret", "set", "TESLA_REFRESH_TOKEN"])
        self.assertEqual(seen["args"][seen["args"].index("--env") + 1], "production")
        # gh trims trailing newlines itself; the file bytes are passed through.
        self.assertEqual(seen["stdin"], REFRESH + b"\n")
        self.assertNotIn("--body", seen["args"])

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
