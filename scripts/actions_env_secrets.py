#!/usr/bin/env python3
"""Store the rotated Tesla refresh token in the production environment.

The build job runs with `environment: production` and reads secrets only from
that environment. This script does not copy or migrate anything, and it never
writes a repository-level secret (those are readable by workflows on any branch).

  save-refresh-token --file PATH
      Store the new refresh token with `gh secret set --env production`.
      The write is retried a few times with backoff. If every attempt fails,
      the run fails: the rotated token was not stored, and the owner must
      re-authorize with scripts/get_refresh_token.py. The token file is
      removed either way. Secret values are never printed.

Requires GH_SECRETS_PAT as GH_TOKEN, with Environments and Secrets read-write.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PRODUCTION = "production"
# One try plus three retries. The sleeps are the gaps between tries.
SAVE_ATTEMPTS = 4
SAVE_BACKOFF_SECONDS = (2, 4, 8)
# Masked when set. Anything else in the environment stays visible: masking
# every value turned errors such as "HTTP 403" into "HTTP 4***3".
# GH_TOKEN is the PAT as exported for gh. The five names are the production
# secrets the job reads. TESLA_API_BASE is optional. GITHUB_TOKEN is the
# automatic Actions credential.
_SECRET_ENV_NAMES = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_SECRETS_PAT",
    "TESLA_CLIENT_ID",
    "TESLA_REFRESH_TOKEN",
    "TESLA_SITE_ID",
    "TESLA_SITE_TZ",
    "TESLA_API_BASE",
)
_PAT_HINT = (
    "The PAT (GH_SECRETS_PAT) needs Environments: Read and write and "
    "Secrets: Read and write on this repository."
)


class GhError(RuntimeError):
    pass


def scrub(text: str, secrets: list[str]) -> str:
    # Longest first so a shorter secret cannot split a longer one.
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def _secret_strings(env: dict[str, str], *extra: bytes | str | None) -> list[str]:
    """Secret values to mask: named env secrets, plus token-file contents."""
    found: list[str] = []
    for name in _SECRET_ENV_NAMES:
        raw = env.get(name, "")
        stripped = raw.strip()
        if not stripped:
            continue
        found.append(stripped)
        if raw != stripped:
            found.append(raw)
    for item in extra:
        if not item:
            continue
        if isinstance(item, bytes):
            text = item.decode("utf-8", "replace")
        else:
            text = item
        stripped = text.strip()
        if not stripped:
            continue
        found.append(stripped)
        if text != stripped:
            found.append(text)
    return found


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


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

    def _raw(self, args: list[str], stdin: bytes | None, secrets: list[str]) -> tuple[int, str, str]:
        code, out, err = self._run(args, stdin)
        out_s = scrub(out.decode("utf-8", "replace"), secrets).strip()
        err_s = scrub(err.decode("utf-8", "replace"), secrets).strip()
        return code, out_s, err_s

    def set_secret(self, name: str, value: bytes, *, environment: str) -> None:
        """Write one secret. Refuses any target other than production."""
        if environment != PRODUCTION:
            raise GhError("refusing to write a secret without --env production")
        args = ["gh", "secret", "set", name, "--repo", self.repo, "--env", environment]
        code, _out, err = self._raw(args, value, _secret_strings(os.environ, value))
        if code != 0:
            raise GhError(err or f"gh secret set exited {code}")


def _repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise SystemExit("GITHUB_REPOSITORY must be owner/repo")
    return repo


def _print_detail(exc: BaseException, err, secrets: list[str]) -> None:
    detail = scrub(str(exc), secrets).strip()
    if detail:
        print(detail, file=err, flush=True)


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def save_refresh_token(gh: Gh, path: str, out, err, sleep=None) -> int:
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
        return _save_refresh_bytes(gh, data, out, err, sleep=sleep)
    finally:
        _unlink(path)


def _save_refresh_bytes(gh: Gh, data: bytes, out, err, sleep=None) -> int:
    pause = _sleep if sleep is None else sleep
    secrets = _secret_strings(os.environ, data)
    last = ""
    for attempt in range(1, SAVE_ATTEMPTS + 1):
        try:
            gh.set_secret("TESLA_REFRESH_TOKEN", data, environment=PRODUCTION)
        except GhError as exc:
            last = scrub(str(exc), secrets).strip()
            if attempt == SAVE_ATTEMPTS:
                break
            delay = SAVE_BACKOFF_SECONDS[attempt - 1]
            print(
                f"production write failed (attempt {attempt} of {SAVE_ATTEMPTS}); "
                f"retrying in {delay}s.",
                file=err,
                flush=True,
            )
            if last:
                print(last, file=err, flush=True)
            pause(delay)
        else:
            print("saved rotated TESLA_REFRESH_TOKEN to the production environment", file=out)
            return 0
    print(
        "ERROR: the rotated TESLA_REFRESH_TOKEN could not be saved to the production environment "
        f"after {SAVE_ATTEMPTS} attempts.",
        file=err,
        flush=True,
    )
    print(
        "The rotated token was not stored. The refresh token this run already used is spent.",
        file=err,
        flush=True,
    )
    print(
        "Re-authorize with scripts/get_refresh_token.py and store the new token "
        "in the production environment.",
        file=err,
        flush=True,
    )
    print("No repository secret was written.", file=err, flush=True)
    print(_PAT_HINT, file=err, flush=True)
    if last:
        _print_detail(GhError(last), err, secrets)
    return 1


def main(argv: list[str] | None = None, runner=None) -> int:
    parser = argparse.ArgumentParser(
        description="Store the rotated Tesla refresh token in the production environment."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    save = sub.add_parser(
        "save-refresh-token",
        help="write TESLA_REFRESH_TOKEN with --env production",
    )
    save.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    gh = Gh(_repo(), runner=runner)
    return save_refresh_token(gh, args.file, sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
