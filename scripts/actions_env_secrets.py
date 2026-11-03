#!/usr/bin/env python3
"""Move the build job's Actions secrets into the production environment.

The build job runs with `environment: production`. GitHub resolves
`${{ secrets.NAME }}` to the environment secret when that name exists, and
otherwise to the repository secret. This script is the one-time bridge:

  migrate
      Copy TESLA_CLIENT_ID, TESLA_SITE_ID, TESLA_SITE_TZ, TESLA_API_BASE, and
      GH_SECRETS_PAT into production when production does not already have
      that name. Values come from the environment (already resolved by
      Actions). Empty values are skipped, so an optional secret is not created
      as a blank override. Never copies TESLA_REFRESH_TOKEN: that value is the
      one about to be spent, and an environment secret would hide a repository
      fallback. Runs before the Tesla exchange, so a permissions failure does
      not rotate the token.

  save-refresh-token --file PATH
      Store the new refresh token in production (`gh secret set --env
      production`). On failure, store it as a repository secret, then delete
      production's TESLA_REFRESH_TOKEN if it exists so the spent value cannot
      take precedence. Exit non-zero whenever the production write failed.
      The token file is removed either way. Secret values are never printed.

Requires GH_SECRETS_PAT as GH_TOKEN, with Environments and Secrets read-write.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PRODUCTION = "production"
# Deliberately excludes TESLA_REFRESH_TOKEN. See module docstring.
COPY_IF_MISSING = (
    "TESLA_CLIENT_ID",
    "TESLA_SITE_ID",
    "TESLA_SITE_TZ",
    "TESLA_API_BASE",
    "GH_SECRETS_PAT",
)
_NAME = re.compile(r"[A-Za-z0-9_]+")
_PAT_HINT = (
    "The PAT (GH_SECRETS_PAT) needs Environments: Read and write and "
    "Secrets: Read and write on this repository."
)


class GhError(RuntimeError):
    pass


def scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _known_secrets(*extra: bytes | str | None) -> list[str]:
    found: list[str] = []
    for name in ("GH_TOKEN", "GITHUB_TOKEN", *COPY_IF_MISSING, "TESLA_REFRESH_TOKEN"):
        raw = os.environ.get(name, "")
        if raw:
            found.append(raw)
    for item in extra:
        if not item:
            continue
        if isinstance(item, bytes):
            found.append(item.decode("utf-8", "replace"))
        else:
            found.append(item)
    return found


def default_runner(args: list[str], stdin: bytes | None) -> tuple[int, bytes, bytes]:
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    try:
        proc = subprocess.run(args, input=stdin, capture_output=True, env=env, check=False)
    except FileNotFoundError:
        return 127, b"", b"gh CLI is not installed\n"
    return proc.returncode, proc.stdout, proc.stderr


class Gh:
    def __init__(self, repo: str, runner=None):
        self.repo = repo
        self._run = runner or default_runner
        self.last_error = ""

    def _raw(self, args: list[str], stdin: bytes | None, secrets: list[str]) -> tuple[int, str, str]:
        code, out, err = self._run(args, stdin)
        out_s = scrub(out.decode("utf-8", "replace"), secrets).strip()
        err_s = scrub(err.decode("utf-8", "replace"), secrets).strip()
        return code, out_s, err_s

    def list_names(self) -> list[str]:
        # Names only. stdout is parsed and never printed; a leaked value that
        # is not a secret name aborts the migration.
        # --json is required. Without an exporter, gh exits 1 with
        # "no secrets found" when production has no secrets yet, which is the
        # first run. That message is treated as an empty list below.
        code, out, err = self._run(
            [
                "gh", "secret", "list",
                "--env", PRODUCTION,
                "--repo", self.repo,
                "--json", "name",
                "--jq", ".[].name",
            ],
            None,
        )
        if code != 0:
            detail = scrub((err or out).decode("utf-8", "replace"), _known_secrets()).strip()
            if "no secrets found" in detail.lower():
                return []
            raise GhError(detail or f"gh secret list exited {code}")
        names: list[str] = []
        for line in out.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            if not _NAME.fullmatch(line):
                raise GhError("unexpected secret list output; refused to continue")
            names.append(line)
        return names

    def set_secret(self, name: str, value: bytes, *, environment: str | None) -> None:
        args = ["gh", "secret", "set", name, "--repo", self.repo]
        if environment:
            args.extend(["--env", environment])
        code, _out, err = self._raw(args, value, _known_secrets(value))
        if code != 0:
            raise GhError(err or f"gh secret set exited {code}")

    def delete_env_secret(self, name: str) -> str:
        """Return 'deleted', 'absent', or 'failed'. Does not print values."""
        code, out, err = self._raw(
            ["gh", "secret", "delete", name, "--repo", self.repo, "--env", PRODUCTION],
            None,
            _known_secrets(),
        )
        self.last_error = err or out
        if code == 0:
            return "deleted"
        lowered = self.last_error.lower()
        if "404" in lowered or "not found" in lowered:
            return "absent"
        return "failed"


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise SystemExit("GITHUB_REPOSITORY must be owner/repo")
    return repo


def _print_detail(exc: BaseException, err, secrets: list[str]) -> None:
    detail = scrub(str(exc), secrets).strip()
    if detail:
        print(detail, file=err)


def _secret_strings(env: dict[str, str], *extra: bytes | str | None) -> list[str]:
    values = [v for v in env.values() if v]
    values.extend(_known_secrets(*extra))
    return values


def migrate(gh: Gh, env: dict[str, str], out, err) -> int:
    """Copy static secrets that production does not have yet. Return 0 or 1."""
    secrets = _secret_strings(env)
    try:
        existing = set(gh.list_names())
    except GhError as exc:
        print("ERROR: could not list secrets in the production environment.", file=err)
        print(
            "Create the production environment first (deployment branches: main only; "
            "no required reviewers or wait timer).",
            file=err,
        )
        print(_PAT_HINT, file=err)
        print("The Tesla refresh token was not exchanged.", file=err)
        _print_detail(exc, err, secrets)
        return 1

    if env.get("TESLA_REFRESH_TOKEN", "").strip():
        print(
            "TESLA_REFRESH_TOKEN: not copied; the rotation step stores the new token in production",
            file=out,
        )

    for name in COPY_IF_MISSING:
        if name in existing:
            print(f"{name}: already in production", file=out)
            continue
        value = env.get(name, "")
        if not value.strip():
            print(f"{name}: not set; left unset in production", file=out)
            continue
        try:
            gh.set_secret(name, value.encode(), environment=PRODUCTION)
        except GhError as exc:
            print(f"ERROR: failed to copy {name} to the production environment.", file=err)
            print(_PAT_HINT, file=err)
            print("The Tesla refresh token was not exchanged.", file=err)
            _print_detail(exc, err, _secret_strings(env, value))
            return 1
        print(f"{name}: copied to production", file=out)
    return 0


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def save_refresh_token(gh: Gh, path: str, out, err) -> int:
    """Store the rotated token in production. Always tries to remove `path`."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        print("ERROR: rotated refresh token file is missing. Nothing was stored.", file=err)
        return 1
    try:
        if not data.strip():
            print("ERROR: rotated refresh token file is empty. Nothing was stored.", file=err)
            print(
                "If Tesla already rotated the token, re-authorize with scripts/get_refresh_token.py.",
                file=err,
            )
            return 1
        return _save_refresh_bytes(gh, data, out, err)
    finally:
        _unlink(path)


def _save_refresh_bytes(gh: Gh, data: bytes, out, err) -> int:
    try:
        gh.set_secret("TESLA_REFRESH_TOKEN", data, environment=PRODUCTION)
    except GhError as exc:
        print("ERROR: failed to save the rotated TESLA_REFRESH_TOKEN to the production environment.", file=err)
        print(_PAT_HINT, file=err)
        _print_detail(exc, err, _known_secrets(data))
        return _fallback_repo(gh, data, err)
    print("saved rotated TESLA_REFRESH_TOKEN to the production environment", file=out)
    return 0


def _fallback_repo(gh: Gh, data: bytes, err) -> int:
    try:
        gh.set_secret("TESLA_REFRESH_TOKEN", data, environment=None)
    except GhError as exc:
        print("ERROR: also failed to save the rotated TESLA_REFRESH_TOKEN as a repository secret.", file=err)
        print("The rotated token was not stored.", file=err)
        print(
            "Re-authorize with scripts/get_refresh_token.py and store the new token in the "
            "production environment (that value overrides the repository secret).",
            file=err,
        )
        _print_detail(exc, err, _known_secrets(data))
        return 1

    print("Fallback: saved the rotated TESLA_REFRESH_TOKEN as a repository secret.", file=err)
    # Only after the fallback is stored. Deleting earlier could drop the only copy.
    removed = gh.delete_env_secret("TESLA_REFRESH_TOKEN")
    if removed == "deleted":
        print(
            "Removed the production TESLA_REFRESH_TOKEN so the next run reads the repository secret.",
            file=err,
        )
    elif removed == "absent":
        print(
            "No production TESLA_REFRESH_TOKEN to remove; the next run reads the repository secret.",
            file=err,
        )
    else:
        print("ERROR: could not remove the production TESLA_REFRESH_TOKEN.", file=err)
        print(
            "If that secret exists, it still holds the refresh token this run just spent, "
            "and it overrides the repository secret.",
            file=err,
        )
        print(
            "Delete the production environment secret named TESLA_REFRESH_TOKEN, then rerun. "
            "The repository secret has the new token.",
            file=err,
        )
        detail = scrub(getattr(gh, "last_error", ""), _known_secrets(data)).strip()
        if detail:
            print(detail, file=err)
    print("Failing this run so the fallback is visible. The next run can still refresh.", file=err)
    return 1


def main(argv: list[str] | None = None, runner=None) -> int:
    parser = argparse.ArgumentParser(description="Publish Actions secrets to the production environment.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    save = sub.add_parser("save-refresh-token")
    save.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    gh = Gh(_repo(), runner=runner)
    if args.cmd == "migrate":
        return migrate(gh, os.environ, sys.stdout, sys.stderr)
    return save_refresh_token(gh, args.file, sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
