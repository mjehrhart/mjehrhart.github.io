#!/usr/bin/env python3
"""One-time, LOCAL: OAuth authorization-code flow -> refresh token + energy site id.

1. Prints a Tesla sign-in URL. Open it, sign in, approve the scopes.
2. Tesla redirects to TESLA_REDIRECT_URI (e.g. https://<user>.github.io/callback/),
   a static page that only tells you to copy the address bar. Paste that full
   URL here.
3. The script exchanges the code, lists your energy sites (locally only), and
   saves the refresh token + chosen site id to .tesla-local.json (chmod 600,
   gitignored), or pushes them straight to GitHub Actions repository secrets
   and the production environment with --gh-repo OWNER/REPO (or to AWS SSM
   with --ssm PREFIX). The production value overrides the repository value.

Env (.env): TESLA_CLIENT_ID, TESLA_CLIENT_SECRET, TESLA_REDIRECT_URI,
            TESLA_API_BASE (optional).
"""
import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from _env import load_env, need  # noqa: E402

load_env()
CLIENT_ID = need("TESLA_CLIENT_ID")
CLIENT_SECRET = need("TESLA_CLIENT_SECRET")
REDIRECT = need("TESLA_REDIRECT_URI")
API = os.environ.get("TESLA_API_BASE", "https://fleet-api.prd.na.vn.cloud.tesla.com").rstrip("/")
SCOPES = "openid offline_access energy_device_data"

ap = argparse.ArgumentParser()
ap.add_argument("--ssm", metavar="PREFIX", help="write refresh_token/energy_site_id to SSM under PREFIX (needs aws CLI)")
ap.add_argument("--gh-repo", metavar="OWNER/REPO", help="write TESLA_REFRESH_TOKEN/TESLA_SITE_ID as repository secrets and production environment secrets (needs gh CLI, logged in, with Environments and Secrets read-write)")
args = ap.parse_args()

state = secrets.token_urlsafe(24)
url = "https://auth.tesla.com/oauth2/v3/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
    "scope": SCOPES, "state": state, "prompt_missing_scopes": "true",
})
print("\n1) Open this URL, sign in to Tesla and approve access:\n\n" + url + "\n")
cb = input("2) Paste the FULL URL you were redirected to: ").strip()
q = urllib.parse.parse_qs(urllib.parse.urlparse(cb).query)
if q.get("state", [""])[0] != state:
    raise SystemExit("State mismatch; start over.")
code = q.get("code", [""])[0]
if not code:
    raise SystemExit("No ?code= in that URL.")

body = urllib.parse.urlencode({
    "grant_type": "authorization_code", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
    "code": code, "audience": API, "redirect_uri": REDIRECT, "scope": SCOPES,
}).encode()
req = urllib.request.Request("https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token", data=body,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    tok = json.loads(urllib.request.urlopen(req, timeout=30).read())
except urllib.error.HTTPError as e:
    raise SystemExit(f"code exchange failed: HTTP {e.code} {json.loads(e.read() or b'{}').get('error', '')}")
access, refresh = tok["access_token"], tok.get("refresh_token")
if not refresh:
    raise SystemExit("No refresh_token returned: was offline_access granted?")

req = urllib.request.Request(f"{API}/api/1/products", headers={"Authorization": f"Bearer {access}"})
try:
    products = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("response", [])
except urllib.error.HTTPError as e:
    raise SystemExit(f"/api/1/products failed: HTTP {e.code} (412 = run partner_register.py first; 421 = wrong region)")
sites = [p for p in products if isinstance(p, dict) and "energy_site_id" in p]
if not sites:
    raise SystemExit("No energy sites on this account.")
for i, s in enumerate(sites):
    print(f"  [{i}] energy site ({s.get('resource_type', 'unknown type')})")
idx = int(input("Choose site number [0]: ") or 0) if len(sites) > 1 else 0
site_id = str(sites[idx]["energy_site_id"])


def _gh_secret_set(repo, name, value, env_name=None):
    cmd = ["gh", "secret", "set", name, "--repo", repo]
    if env_name:
        cmd.extend(["--env", env_name])
    env = os.environ.copy()
    env["GH_PROMPT_DISABLED"] = "1"
    # Value goes on stdin, never on the command line. gh strips trailing newlines.
    subprocess.run(cmd, input=value.encode(), check=True, stdout=subprocess.DEVNULL, env=env)


if args.gh_repo:
    for name, val in (("TESLA_REFRESH_TOKEN", refresh), ("TESLA_SITE_ID", site_id)):
        _gh_secret_set(args.gh_repo, name, val)
        try:
            _gh_secret_set(args.gh_repo, name, val, env_name="production")
        except subprocess.CalledProcessError:
            raise SystemExit(
                f"Stored {name} as a repository secret, but could not write the production environment secret. "
                "The production value overrides the repository value, so a spent production token would hide this one. "
                "Grant this gh login Environments: Read and write, then write the production secret before the next workflow run."
            )
    print(f"Stored TESLA_REFRESH_TOKEN and TESLA_SITE_ID as repository and production secrets in {args.gh_repo}")
elif args.ssm:
    pfx = args.ssm.rstrip("/")
    for name, val in (("refresh_token", refresh), ("energy_site_id", site_id)):
        subprocess.run(["aws", "ssm", "put-parameter", "--name", f"{pfx}/{name}", "--type", "SecureString",
                        "--overwrite", "--value", val], check=True, stdout=subprocess.DEVNULL)
    print(f"Stored refresh_token and energy_site_id in SSM under {pfx}/")
else:
    out = Path(__file__).resolve().parent.parent / ".tesla-local.json"
    out.write_text(json.dumps({"refresh_token": refresh, "energy_site_id": site_id}))
    out.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Saved to {out} (gitignored, mode 600). Store the values as repo secrets (or SSM), then delete the file.")
print("Note: refresh tokens are single-use and rotate on every refresh; the scheduled job stores each new one in the production environment.")
