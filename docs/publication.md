# Public Repository Setup

This checklist prepares Chappe for a public GitHub repository and future PyPI
release.

## GitHub

1. Create the public repository:

   ```bash
   gh repo create crimeacs/chappe --public --source=. --remote=origin --push
   ```

2. Confirm the default branch is `main`.

3. Enable branch protection for `main`:

   - require pull request before merge
   - require status checks
   - require the `CI` workflow
   - disallow force pushes

4. Configure repository metadata:

   - description: `Open-source CLI tool surface for Telegram channel agents.`
   - website: PyPI page after release
   - topics: `telegram`, `cli`, `growth`, `analytics`, `agents`, `tdlib`

5. Review the repository after push:

   ```bash
   git status --short
   git ls-files | rg '(\.env|session|tdlib|chappe\.db|audit\.jsonl|fetched_)'
   ```

   The second command should return nothing.

## PyPI Trusted Publishing

The release workflow uses PyPI Trusted Publishing through GitHub Actions OIDC,
so no long-lived PyPI API token is stored in GitHub secrets.

Create a GitHub environment named `pypi`, then configure a PyPI trusted
publisher for:

- project name: `chappe`
- owner: `crimeacs`
- repository: `chappe`
- workflow: `release.yml`
- environment: `pypi`

Relevant docs:

- PyPI Trusted Publishers: https://docs.pypi.org/trusted-publishers/
- PyPI GitHub publisher setup: https://docs.pypi.org/trusted-publishers/using-a-publisher/
- PyPA publish action: https://github.com/pypa/gh-action-pypi-publish

## Release

1. Update `CHANGELOG.md`.
2. Update `version` in `pyproject.toml` and `src/chappe/__init__.py`.
3. Run local checks:

   ```bash
   ruff check .
   pytest -q
   python -m build
   ```

4. Commit changes.
5. Tag the release:

   ```bash
   git tag v0.1.0
   git push origin main --tags
   ```

6. Create a GitHub release from the tag. Publishing the GitHub release triggers
   `.github/workflows/release.yml`.

## Public Safety Scan

Before first push and before every release, run:

```bash
git status --short
find . -maxdepth 4 -type f \( -name '.env' -o -name '*.session' -o -name '*.session-journal' -o -name '*.db' -o -name '*.sqlite' -o -name 'audit.jsonl' \) -print
git grep -nE 'api_hash|TELEGRAM_API_HASH|phone_number|authorizationStateWaitCode|BEGIN (RSA|OPENSSH|PRIVATE) KEY' -- ':!README.md' ':!docs/publication.md' ':!.env.example'
```

The `find` command should return nothing except ignored local files outside the
tracked set. The `git grep` command may find documentation or code references,
but it must not find real secrets.
