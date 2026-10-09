# Link checking fixture

A small static test site for checking link-reporting behavior. All pages and content are synthetic and self-authored. There is no customer data, account flow, form submission, tracking script, external dependency, or external test URL.

## Intentional link outcomes

`expected-results.json` declares two source pages and **12 ordered link occurrences**:

- 9 working resources or anchors
- 2 missing resources, expected to return HTTP 404
- 1 missing anchor on an otherwise reachable page, expected to return HTTP 200

The duplicate `ok.html` link is a separate occurrence. Japanese filenames and fragments are percent-encoded in the source links. Nested links must resolve under the GitHub Pages project prefix. Missing resources and fragments are intentional test cases: do not add or fix them to make every link succeed. Directly requesting `404.html` returns that existing file successfully; requesting a missing resource should still return HTTP 404.

Only `index.html` and `nested/page.html` are link-source pages. The other HTML files and text asset are targets. Stylesheet references are checked as local project-relative resources and are not counted as link occurrences.

## Local validation

Use Python 3.10 or newer. No package installation or external network access is needed:

```sh
python3 tests/validate_fixture.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The validator binds its own HTTP server to an ephemeral `127.0.0.1` port and serves the fixture only under `/link-checker-test-site/`. It disables proxies and redirects and never requests external link targets. Missing resources are checked using real HTTP 404 responses. The server is stopped after validation.

Checks cover the exact source pages, all 12 links in order (including duplicates), response status and anchor existence, percent-decoded Japanese paths/fragments, project-prefix resolution, the exact eight-file `site/` inventory, and the absence of scripts, forms, external URLs, symlinks, or extra published files. Approved fixture bytes and `expected-results.json` are also fixed by SHA-256 checks, so text, style, link, or manifest drift fails with a nonzero exit status. An intentional future fixture revision must update the explicit contract and hashes together and be reviewed.

The mutation suite proves the validator rejects a deleted good file, an unexpectedly filled missing target, a missing good anchor, an extra link, altered Japanese anchors, changed link occurrences, root-relative or external URLs, scripts/forms, extra files, symlinks, manifest changes, and text-only changes.

These checks validate this small fixed fixture. They are not a general-purpose link checker or evidence about live hosting. Actual deployed HTTP behavior should be verified after deployment. Controllable redirects, 401/403 authentication, 429/rate limits, slow responses/timeouts, TLS failures, SSRF/private-address checks, robots blocking, and malicious inputs are outside this fixture's scope.

## GitHub Pages publishing

The custom workflow at `.github/workflows/pages.yml` publishes **only `site/`**. Repository metadata, tests, the README, and `expected-results.json` are not part of the Pages artifact. The existing `LICENSE` remains at the repository root. No custom domain, paid service, JavaScript, third-party font, package installation, PAT, or custom secret is needed.

- Pull requests targeting `main`, pushes to `main`, and manual workflow runs validate the fixture and its mutation tests on `ubuntu-latest`
- Pull requests validate only; they do not upload or deploy a Pages artifact
- Only a successful push to `main`, or a manual run selected on `main`, uploads `site/` and allows the dependent deployment job to run
- Validation has only `contents: read`. Only the deployment job has `pages: write` and `id-token: write`, and uses the `github-pages` environment
- The official checkout, Pages artifact upload, and Pages deployment actions are pinned to full commit SHAs; checkout does not persist credentials

Before merging the migration from `docs/` to `site/`, the repository's **Settings → Pages → Build and deployment → Source** must be changed from branch publishing to **GitHub Actions** by an authorized maintainer. After merging, the workflow validates and deploys the unchanged public fixture from `site/`. A manual run on `main` can redeploy the validated fixture. Running this locally does not change that repository setting or deploy anything.

All repository files and the published site are publicly readable. `noindex` requests are not access controls and do not make content private.
