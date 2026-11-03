# Home Energy Dashboard

A single-page, **anonymous** dashboard for a solar + Powerwall home. It shows solar production, battery charge,
home consumption, and grid import/export, with charts for today and the last 7 days.
It is a static site that reads one sanitized snapshot, `data.json`, which a scheduled GitHub Actions workflow refreshes.

```
GitHub Actions (cron */30, best effort)
  -> refresh Tesla token -> IMMEDIATELY save rotated token (gh secret set, fine-grained PAT)
  -> live_status + calendar_history (kind=energy) -> ALLOWLIST sanitize + validate
  -> _site/ (public files only) -> upload-pages-artifact -> deploy-pages -> https://<user>.github.io/
```

**Privacy.** The published site contains no tokens, site, account, or vehicle IDs, names, location, or timezone name.
`data.json` may contain only the allowlisted keys in `scripts/public_schema.py`, and the workflow refuses to deploy anything else.
The real snapshot exists **only in the deployed Pages artifact**. It is never committed, so the repo's `data.json` stays
example data (`"example": true`, and the page shows an "Example data" banner) and git history holds no energy data.
Before you push, run:
```
python3 scripts/verify_public_data.py   # allowlist check of data.json
python3 scripts/scan_repo.py            # repo-wide PII / secret pattern scan
```
**This is a public repo.** Every file in it is public, and so are Actions logs. Never put tokens, IDs, a `.env` file,
or the private key in it. Only `_site/` (assembled by the workflow) is served, so `scripts/` and this README are not published.
The `<user>.github.io` URL itself contains your GitHub username. That is the only identifying thing; the site content has none.

## Files
| Path | Purpose |
|---|---|
| `index.html`, `app.js`, `styles.css`, `vendor/chart.umd.min.js` | the page (Chart.js 4.4.6 vendored, strict CSP) |
| `data.json` | **example** snapshot (the live one is generated at deploy time) |
| `callback/index.html` | static OAuth redirect landing page (no script; the code is copied from the address bar) |
| `.well-known/appspecific/com.tesla.3p.public-key.pem` | Tesla public key. **Already in the repo. Keep your existing file; this folder ships without one.** |
| `.nojekyll` | keep (harmless with Actions deploys; needed if you ever go back to branch deploys) |
| `.github/workflows/refresh.yml` | refresh + deploy every 30 min, on push to `main`, and on manual run |
| `scripts/refresh.py` | the Actions fetcher (`token` and `fetch` sub-commands) |
| `scripts/powerwall_core.py` | Fleet API calls + aggregation into the public shape |
| `scripts/public_schema.py` | allowlist validator (keys, formats, value ranges, list sizes) |
| `scripts/partner_register.py`, `scripts/get_refresh_token.py` | one-time Tesla setup, run locally |
| `scripts/verify_public_data.py`, `scripts/scan_repo.py`, `scripts/make_example_data.py` | checks / example data |

## Deploy on GitHub Pages: do these in order

> ⚠️ **First, if you enabled the AWS Lambda/EventBridge schedule, disable it.** Tesla refresh tokens are single-use
> and rotate. Two jobs sharing one token will invalidate each other within a day.
> ```
> aws scheduler update-schedule --name home-energy-refresh --state DISABLED \
>   --schedule-expression 'rate(30 minutes)' --flexible-time-window Mode=OFF \
>   --target '{"Arn":"arn:aws:lambda:REGION:ACCOUNT_ID:function:home-energy-refresh","RoleArn":"arn:aws:iam::ACCOUNT_ID:role/home-energy-scheduler"}'
> # or simply: aws scheduler delete-schedule --name home-energy-refresh
> ```
> (`update-schedule` replaces the whole schedule, so it needs the same expression and target you created it with.)

### 1. Copy the files into the repo
Copy everything in this folder into the root of your Pages repo, next to the existing `.nojekyll` and `.well-known/`.
**Do not overwrite or delete `.well-known/appspecific/com.tesla.3p.public-key.pem`.** The workflow fails safe (no deploy) if it is missing.
Don't push yet. Do steps 2–5 first, otherwise the first run on push fails because the secrets are missing.

### 2. Tesla partner domain (must match the domain the app uses)
Your Tesla app's allowed origin and the registered partner domain must be the domain that serves the public key.
- **If you registered `mjehrhart.github.io`** (the key is already served there): nothing to do.
- **If you registered `viewu.app`**, choose one:
  - (a) keep `viewu.app` online, serving the same `.pem`, and keep the redirect URI there; or
  - (b) switch: on developer.tesla.com set **Allowed origin** `https://mjehrhart.github.io` and **Redirect URI**
    `https://mjehrhart.github.io/callback/`, then rerun, once per region:
    ```
    TESLA_DOMAIN=mjehrhart.github.io python3 scripts/partner_register.py   # reads TESLA_CLIENT_ID/SECRET from .env
    ```
    A `412` error later means this step is missing. A `421` means the wrong region (`TESLA_API_BASE`).

### 3. Create the fine-grained PAT (`GH_SECRETS_PAT`)
`GITHUB_TOKEN` can't write repository secrets, so the workflow needs a narrow PAT to save the rotated refresh token.
GitHub → your avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**:
- Name: `pages-secret-rotation`. Expiration: up to 1 year (set a reminder to renew it).
- Resource owner: you. **Repository access: Only select repositories → `mjehrhart.github.io`**.
- **Repository permissions → Secrets: Read and write**. Leave everything else at No access (Metadata: read-only is added automatically).
- Generate the token, then store it without it touching your screen history: run `gh secret set GH_SECRETS_PAT --repo mjehrhart/mjehrhart.github.io`
  and paste the token at the hidden prompt. Don't keep the token anywhere else.

### 4. Add the repository secrets
UI: repo → **Settings → Secrets and variables → Actions → New repository secret**. Or use `gh` (log in with `gh auth login` first).
If you used the AWS setup, pipe the values straight from SSM so they never appear on screen or in shell history:
```
R=mjehrhart/mjehrhart.github.io; P=/home-energy-dashboard
get() { aws ssm get-parameter --name "$P/$1" --with-decryption --query Parameter.Value --output text; }
get refresh_token  | gh secret set TESLA_REFRESH_TOKEN --repo "$R"
get energy_site_id | gh secret set TESLA_SITE_ID       --repo "$R"
get client_id      | gh secret set TESLA_CLIENT_ID     --repo "$R"
get site_tz        | gh secret set TESLA_SITE_TZ       --repo "$R"
get api_base       | gh secret set TESLA_API_BASE      --repo "$R"   # optional; defaults to North America
```
(`aws ... --output text` adds a trailing newline. The scripts strip it.)
If you never used AWS, or the token has expired, get a fresh one locally; it goes straight into the repo secrets:
```
python3 scripts/get_refresh_token.py --gh-repo mjehrhart/mjehrhart.github.io   # sets TESLA_REFRESH_TOKEN and TESLA_SITE_ID
gh secret set TESLA_CLIENT_ID --repo mjehrhart/mjehrhart.github.io              # paste at the prompt
gh secret set TESLA_SITE_TZ  --repo mjehrhart/mjehrhart.github.io              # IANA zone of the home, e.g. from your OS
```

| Secret | Required | Value |
|---|---|---|
| `TESLA_CLIENT_ID` | yes | Tesla app client ID (the client **secret** is not needed for refresh, so don't store it here) |
| `TESLA_REFRESH_TOKEN` | yes | current refresh token; the workflow rewrites it each run |
| `TESLA_SITE_ID` | yes | energy_site_id |
| `TESLA_SITE_TZ` | yes | IANA timezone of the home, used only on the runner to compute "today", never published |
| `TESLA_API_BASE` | no | `https://fleet-api.prd.eu.vn.cloud.tesla.com` for EU; default NA |
| `GH_SECRETS_PAT` | yes | the fine-grained PAT from step 3 |

### 5. Switch Pages to Actions deploys
Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions**. (The old "Deploy from a branch" build stops.
The site keeps serving the last deployment until the first workflow run replaces it.)

### 6. Push and run
Push to `main`. That triggers a run, and you can also use **Actions → Refresh energy snapshot → Run workflow**.
Check https://mjehrhart.github.io/: the "Example data" banner should be gone, with a current "Last updated" time.
After that, runs are every ~30 min.

## How the workflow works / design notes
- **Deploy method: `actions/deploy-pages` (no commits).** A push made with `GITHUB_TOKEN` does *not* trigger the classic
  "pages build and deployment", so committing `data.json` would need a PAT with `contents: write`, and would add
  ~48 commits/day (~17k/year) of energy history to a public repo. Instead, the workflow assembles `_site/` and deploys it as
  a Pages artifact. That means no commit churn and no data in git history. Deploys are atomic, and only the PAT's
  *Secrets* permission is needed.
  `include-hidden-files: true` is set so `.well-known/` is published.
- **Token rotation:** the refresh happens first. The new token is written back with `gh secret set` immediately, before any
  data call. If that write fails, the job stops and nothing deploys. Fix the PAT soon: the previous token keeps working for
  only about 24 h. If it lapses, rerun `get_refresh_token.py --gh-repo …`.
- **Never in logs:** tokens are `::add-mask::`ed as soon as they exist and kept in mode-600 files under `$RUNNER_TEMP`, which are
  deleted at the end. The scripts print only counts and Tesla error codes. Secrets are passed only to the steps that need them.
- **Concurrency:** there is one group with `cancel-in-progress: false`, so runs queue instead of racing or being cancelled mid-rotation.
- **Battery trail:** each run reads the currently deployed `data.json`, using it only if it passes the allowlist, so today's battery-% line carries forward.
- **If Tesla is down,** the run fails and the site keeps its last snapshot. The page shows a **Stale** badge if the snapshot is more than 2 h old.
- **Cron caveats:** GitHub schedules are best effort, so runs can be 5–30+ min late, or occasionally skipped at busy times.
  In public repos, scheduled workflows are **disabled after 60 days without repo activity**. Since data is no longer committed,
  re-enable the workflow from the Actions tab if GitHub emails you about it, or push any small change now and then.
- **Cost:** public repos get free Actions minutes. Each run takes ~30 s. Fleet API usage at 48 runs/day × 3 calls fits Tesla's monthly credit.

## Tesla token and site walkthrough (one time)
1. Generate a keypair locally (`openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem` then
   `openssl ec -in private-key.pem -pubout -out public-key.pem`). Keep the private key offline and out of the repo; only the public key
   goes at `.well-known/appspecific/com.tesla.3p.public-key.pem` (already done in this repo).
2. developer.tesla.com → Create application. Use a generic name. Grant types: authorization code + client credentials. Set the allowed origin and
   redirect URI as in step 2. Scope: **Energy Product Information** only. Add billing with a low limit.
3. `partner_register.py`, then `get_refresh_token.py --gh-repo …`, as above. The callback page only displays; the code is copied by you from
   the address bar, is single-use, and expires within minutes.

## Revoking access
Delete the repo secrets and revoke the app at tesla.com/teslaaccount/account-settings (Third Party Apps), or rotate the client secret. Delete the PAT.

## Local checks
```
python3 scripts/verify_public_data.py && python3 scripts/scan_repo.py
python3 scripts/make_example_data.py     # regenerate example data.json
```
