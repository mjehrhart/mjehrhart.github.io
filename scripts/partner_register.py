#!/usr/bin/env python3
"""One-time: register your Tesla developer app for your domain in a region.

Prereqs: the public key is already live at
  https://<DOMAIN>/.well-known/appspecific/com.tesla.3p.public-key.pem
Env (.env): TESLA_CLIENT_ID, TESLA_CLIENT_SECRET, TESLA_DOMAIN (e.g. viewu.app),
            TESLA_API_BASE (optional; defaults to North America).
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, __import__("os").path.dirname(__file__))
from _env import load_env, need  # noqa: E402

load_env()
import os  # noqa: E402

CLIENT_ID = need("TESLA_CLIENT_ID")
CLIENT_SECRET = need("TESLA_CLIENT_SECRET")
DOMAIN = need("TESLA_DOMAIN")
API = os.environ.get("TESLA_API_BASE", "https://fleet-api.prd.na.vn.cloud.tesla.com").rstrip("/")


def call(method, url, headers, data=None):
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# 0. Sanity check: the key must be publicly reachable over HTTPS.
pem_url = f"https://{DOMAIN}/.well-known/appspecific/com.tesla.3p.public-key.pem"
try:
    with urllib.request.urlopen(pem_url, timeout=15) as r:
        body = r.read().decode()
    assert "BEGIN PUBLIC KEY" in body, "URL did not return a PEM public key (SPA rewrite?)"
    print(f"public key reachable at {pem_url}")
except Exception as e:  # noqa: BLE001
    raise SystemExit(f"Public key check failed: {e}")

# 1. Partner token (client_credentials)
status, tok = call("POST", "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token",
                   {"Content-Type": "application/x-www-form-urlencoded"},
                   urllib.parse.urlencode({
                       "grant_type": "client_credentials", "client_id": CLIENT_ID,
                       "client_secret": CLIENT_SECRET, "audience": API,
                       "scope": "openid energy_device_data offline_access"}).encode())
if status != 200 or "access_token" not in tok:
    raise SystemExit(f"partner token failed: HTTP {status} {tok.get('error', '')}")
hdr = {"Authorization": f"Bearer {tok['access_token']}", "Content-Type": "application/json"}

# 2. Register
status, resp = call("POST", f"{API}/api/1/partner_accounts", hdr, json.dumps({"domain": DOMAIN}).encode())
print(f"register: HTTP {status} {resp.get('error', 'ok')}")

# 3. Confirm Tesla has the key for this domain
status, resp = call("GET", f"{API}/api/1/partner_accounts/public_key?domain={urllib.parse.quote(DOMAIN)}", hdr)
print(f"public_key lookup: HTTP {status} {'found' if resp.get('response') else resp.get('error', 'not found')}")
