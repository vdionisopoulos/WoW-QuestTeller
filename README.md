# WoW QuestTeller

QuestTeller is a character-driven World of Warcraft narrative chronicle. This
repository is the source of truth for the Blogger theme, Alisander's Chronicle
chapters, and static pages.

## Repository layout

- `blogger/theme/questteller.xml` — deployable Blogger theme.
- `blogger/deploy.json` — stable mapping between local source files and live Blogger resource IDs.
- `content/chronicles/alisander/` — Chronicle chapter HTML.
- `content/pages/` — Blogger static-page HTML.
- `tools/validate_repo.py` — local/CI validation.
- `tools/blogger_deploy.py` — dependency-free Blogger API v3 REST content deployer.
- `tools/blogger_oauth_bootstrap.py` — one-time local OAuth setup.
- `.github/workflows/questteller-ci-deploy.yml` — validation + production content deployment.

## Deployment model

`main` is production.

1. Work locally on a feature branch.
2. Run `python tools/validate_repo.py`.
3. Commit and merge to `main`.
4. Push `main` to GitHub.
5. GitHub Actions validates the repository.
6. If validation succeeds, changed managed posts/pages are patched and published through Blogger API v3.
7. The validated theme XML is attached to the GitHub Actions run as an artifact.

Blogger API v3 supports posts/pages but does not provide an official theme
write endpoint. Theme changes therefore remain a deliberate manual upload in
Blogger. Content changes are fully automated.

## One-time Blogger API setup

### 1. Google Cloud

Create/select a Google Cloud project and enable **Blogger API v3**.

Configure the OAuth consent screen and request only:

`https://www.googleapis.com/auth/blogger`

For a persistent CI refresh token, move the OAuth app to **In production**.
External OAuth apps left in **Testing** receive refresh tokens that expire after
7 days.

Create an OAuth 2.0 Client ID of type **Desktop app**, then download its JSON
file to your workstation. Never commit that file.

### 2. Generate the refresh token locally

Create a local virtual environment, install the deployment dependencies, and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-oauth.txt
python tools\blogger_oauth_bootstrap.py --client-secret .\client_secret_XXXX.json
```

Authorize with the Google account that owns/manages the Blogger site.

The script prints three values. Add them to the GitHub repository under
**Settings → Secrets and variables → Actions**:

- `BLOGGER_CLIENT_ID`
- `BLOGGER_CLIENT_SECRET`
- `BLOGGER_REFRESH_TOKEN`

Do not paste these values into source files.

### 3. Verify live Blogger title mapping before enabling automatic deploys

With the same three values set in your local shell environment, inspect the
live Blogger inventory:

```powershell
$env:BLOGGER_CLIENT_ID="..."
$env:BLOGGER_CLIENT_SECRET="..."
$env:BLOGGER_REFRESH_TOKEN="..."
python tools\blogger_deploy.py inventory
```

`blogger/deploy.json` deliberately matches by **exact title** and refuses to
guess. Verify these eight titles match the live Blogger resources:

- Shadow Grave
- Those That Couldn't Be Saved
- The Wakening
- Recruitment
- Scourge on Our Perimeter
- The Truth of the Grave
- About Alisander
- Journey

Then run a no-write mapping check:

```powershell
python tools\blogger_deploy.py check --manifest blogger/deploy.json
```

Only after that command succeeds should the pipeline be pushed to `main`.

## Install the versioned local pre-push gate

Run once in the repository:

```powershell
git config core.hooksPath .githooks
```

After that, every `git push` first runs `python tools/validate_repo.py` locally.
GitHub Actions runs the same validation again before production deployment.

## Normal local workflow

```powershell
git switch -c feature/chapter-07
# edit files
python tools\validate_repo.py
git add .
git commit -m "Add chapter VII"
git switch main
git merge --ff-only feature/chapter-07
git push
```

A push to `main` triggers production deployment. On normal pushes, the deployer
uses the previous Git SHA and updates only managed content files that changed.
A manually dispatched workflow intentionally deploys all managed content.

## Safety behavior

The deployment intentionally fails closed:

- invalid Blogger XML fails validation;
- unbalanced theme CSS braces fail validation;
- malformed chapter filenames fail validation;
- missing/empty image alt text fails validation;
- inline styles in content fail validation;
- duplicate/missing deployment-manifest entries fail validation;
- a missing or non-unique live Blogger title fails deployment;
- no new Blogger post/page is created automatically;
- labels are not overwritten by the deployer;
- OAuth credentials and refresh tokens are ignored by Git.

## Theme changes

When `blogger/theme/questteller.xml` changes, CI validates it and attaches the
validated XML to the workflow run. Upload that artifact manually in Blogger's
Theme UI after reviewing the live preview.
