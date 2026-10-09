# Link checking fixture

A small static test site for checking link-reporting behavior. All pages and content are synthetic and self-authored. There is no customer data, account flow, form submission, tracking script, external dependency, or external test URL.

Intentionally missing resources and fragments are part of the fixture, not accidental defects. See `expected-results.json`.

GitHub Pages target: deploy the `docs` directory on `main`. Project-relative links are intentional. No custom domain, paid service, JavaScript, third-party font, Actions workflow, or package installation is required by these files.

Expected coverage: 2 source pages, 12 link occurrences: 9 reachable resources/anchors, 2 missing resources, 1 missing anchor. These are expected outcomes from the static files; actual hosted HTTP behavior must be checked after deployment.

Not covered: controllable HTTP redirects, 401/403 authentication, 429/rate limits, slow responses/timeouts, TLS failures, SSRF/private-address checks, robots blocking, malicious inputs. Keep those tests in private local fixtures.

All repository files and the published site will be publicly readable if this proposal is approved. `noindex` requests are not access controls and do not make the content private.
